"""
Gateway: manda compradores de mentira a la tienda por etapas y dice si cada etapa quedó OPTIMO
o SATURADO. No autoescala: aquí solo medimos. El comprador sigue las mismas reglas que en SimPy.
Escribe datos/gateway_<etiqueta>.json y datos/cpu_log.csv. Con la tienda prendida:
    python sistema_real/gateway.py --escenario pico   (o reposo, escalonada)
"""
import argparse
import csv
import json
import math
import os
import random
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

BASE = os.getenv("API_URL", "http://localhost:8000")

# --- Cómo se comporta el comprador ---
TIMEOUT_CLIENTE = 5.0      # segundos que el cliente espera una respuesta (política actual)
MAX_REINTENTOS = 3         # reintentos después del primer intento (4 intentos en total)
PROB_REINTENTO = 0.60      # parte de los compradores que sí reintenta
BACKOFF_BASE = 1.0         # espera 1, 2 y 4 s antes de cada reintento, +-50 % al azar
INTERVALO_POLLING = 5.0    # cada cuánto el comprador pregunta por su orden

# Paciencia: cuánto aguanta un comprador antes de irse. Weibull con forma k = 2 y media 20 s.
# Media de una Weibull = escala * Gamma(1 + 1/k)  ->  escala = 20 / Gamma(1.5)
PACIENCIA_FORMA = 2.0
PACIENCIA_ESCALA = 20.0 / math.gamma(1 + 1 / PACIENCIA_FORMA)   # = 22.57 s

# SLA: una etapa está saturada si el p95 pasa de 2000 ms (M1) o más del 10 % no se atiende (M3).
UMBRAL_P95_MS = 2000.0
UMBRAL_NO_ATENDIDAS = 0.10

# --- Curvas de demanda: una pareja (lecturas/s, compras/s) por etapa ---
CURVAS = {
    # Sin tráfico: lo que gastan los recursos con la tienda quieta.
    "reposo": [(0, 0)],
    # Sube por escalones hasta encontrar dónde se rompe un nodo (su capacidad).
    "escalonada": [(9, 1), (18, 2), (27, 3), (36, 4), (45, 5),
                   (54, 6), (72, 8), (90, 10), (108, 12)],
    # Normal (mezcla 90/10), oferta relámpago (lecturas x20, compras x6) y recuperación.
    # Es el mismo escenario que corre la simulación.
    "pico": [(9, 1), (9, 1), (180, 6), (180, 6), (9, 1), (9, 1), (9, 1)],
}
DURACION_ETAPA = 30.0      # segundos por etapa (se cambia con --duracion)

CARPETA_DATOS = "datos"   # relativa a donde se ejecuta el script (la raíz del proyecto)


# --- Lo que se va sumando en una etapa ---
class Etapa:
    """Contadores y latencias de una etapa. Muchos hilos la actualizan a la vez."""

    def __init__(self, numero, lecturas_s, compras_s):
        self.numero = numero
        self.lecturas_s = lecturas_s
        self.compras_s = compras_s
        self.latencias = []          # ms de las peticiones completadas
        self.peticiones = 0          # peticiones que llegaron a la tienda
        self.completadas = 0
        self.rechazadas = 0          # 503: punto de acceso, pool o cola llenos
        self.timeouts = 0
        self.errores = 0             # otros 4xx/5xx
        self.operaciones = 0         # operaciones que los compradores quisieron hacer
        self.compras = 0             # compradores que empezaron una compra
        self.compras_abandonadas = 0
        self.lecturas_abandonadas = 0
        self.cpu = []                # % de CPU del contenedor de la API
        # Miles de compradores (hilos) suman al tiempo. El candado evita perder sumas.
        self.candado = threading.Lock()

    def suma(self, campo, valor=1):
        """Suma `valor` al contador llamado `campo` (por ejemplo "timeouts")."""
        with self.candado:
            setattr(self, campo, getattr(self, campo) + valor)

    def percentil(self, p):
        """Percentil p (0.95 = p95) de las latencias, o nan si no hay datos."""
        if not self.latencias:
            return float("nan")
        datos = sorted(self.latencias)
        return datos[min(len(datos) - 1, int(p * len(datos)))]

    def resumen(self):
        """Indicadores de la etapa (van al JSON y a pantalla)."""
        # No atendidas = rechazadas (503 o conexión rechazada) + timeouts.
        # Amplificación = peticiones / operaciones (una compra hace varias y los reintentos suman).
        no_atendidas = self.rechazadas + self.timeouts
        return {
            "etapa": self.numero,
            "lecturas_s": self.lecturas_s,
            "compras_s": self.compras_s,
            "operaciones": self.operaciones,
            "peticiones": self.peticiones,
            "amplificacion": round(self.peticiones / self.operaciones, 3) if self.operaciones else 0,
            "completadas": self.completadas,
            "rechazadas": self.rechazadas,
            "timeouts": self.timeouts,
            "errores": self.errores,
            "tasa_no_atendidas": round(no_atendidas / self.peticiones, 4) if self.peticiones else 0,
            "compras": self.compras,
            "abandono_compras": round(self.compras_abandonadas / self.compras, 4) if self.compras else 0,
            "p95_ms": None if math.isnan(self.percentil(0.95)) else round(self.percentil(0.95), 1),
            "p99_ms": None if math.isnan(self.percentil(0.99)) else round(self.percentil(0.99), 1),
            "cpu_media_pct": round(sum(self.cpu) / len(self.cpu), 1) if self.cpu else None,
            "cpu_max_pct": round(max(self.cpu), 1) if self.cpu else None,
        }

    def esta_saturada(self):
        """True si la etapa no cumple el SLA."""
        r = self.resumen()
        lat_mala = r["p95_ms"] is not None and r["p95_ms"] > UMBRAL_P95_MS
        return lat_mala or r["tasa_no_atendidas"] > UMBRAL_NO_ATENDIDAS


# --- El comprador ---
class Comprador:
    """Un comprador: tiene su propia paciencia y decide una sola vez si reintenta."""

    def __init__(self, rng):
        self.rng = rng
        self.inicio = time.perf_counter()
        self.paciencia = rng.weibullvariate(PACIENCIA_ESCALA, PACIENCIA_FORMA)  # segundos
        self.reintenta = rng.random() < PROB_REINTENTO

    def esperando(self):
        return time.perf_counter() - self.inicio

    def se_canso(self):
        return self.esperando() > self.paciencia


def pedir(metodo, ruta, etapa, escenario, **kw):
    """Hace la petición, la cuenta en la etapa según cómo le fue y devuelve (ok, respuesta)."""
    etapa.suma("peticiones")
    # Cabeceras que la API copia al CSV, para saber de qué corrida y etapa es cada fila.
    cabeceras = {"X-Escenario": escenario, "X-Etapa": str(etapa.numero)}
    t0 = time.perf_counter()
    # Si la API no responde en TIMEOUT_CLIENTE (5 s), el cliente deja de esperar.
    try:
        r = requests.request(metodo, f"{BASE}{ruta}", timeout=TIMEOUT_CLIENTE,
                             headers=cabeceras, **kw)
    except requests.exceptions.Timeout:
        etapa.suma("timeouts")
        return False, None
    except requests.exceptions.RequestException:
        etapa.suma("rechazadas")       # conexión rechazada: el nodo no recibe más
        return False, None
    # 503 es rechazo por saturación, otro 4xx/5xx es error. Solo los éxitos suman latencia al p95.
    ms = (time.perf_counter() - t0) * 1000
    if r.status_code == 503:
        etapa.suma("rechazadas")
        return False, r
    if r.status_code >= 400:
        etapa.suma("errores")
        return False, r
    etapa.suma("completadas")
    with etapa.candado:
        etapa.latencias.append(ms)
    return True, r


def con_reintentos(hacer_peticion, comprador, etapa):
    """Intenta hasta 1 + MAX_REINTENTOS veces. Devuelve la respuesta, o None si se fue."""
    for intento in range(MAX_REINTENTOS + 1):
        ok, r = hacer_peticion()
        if ok:
            return r
        if not comprador.reintenta or intento == MAX_REINTENTOS or comprador.se_canso():
            return None
        # Backoff exponencial: 1, 2 y 4 s, por un azar entre 0.5 y 1.5 para que no reintenten
        # todos al mismo tiempo.
        espera = BACKOFF_BASE * 2 ** intento * comprador.rng.uniform(0.5, 1.5)
        time.sleep(espera)
        if comprador.se_canso():
            return None
    return None


def operacion_lectura(etapa, escenario, rng):
    """Un comprador pide una página del catálogo al azar, con reintentos."""
    etapa.suma("operaciones")
    c = Comprador(rng)
    pagina = rng.randint(1, 10)
    r = con_reintentos(lambda: pedir("GET", f"/catalogo?pagina={pagina}", etapa, escenario),
                       c, etapa)
    if r is None:
        etapa.suma("lecturas_abandonadas")


def operacion_compra(etapa, escenario, rng):
    """Un comprador agrega al carrito, paga y pregunta por su orden. Si se rinde, es abandono."""
    etapa.suma("operaciones")
    etapa.suma("compras")
    c = Comprador(rng)
    comprador_id = f"c{rng.getrandbits(48)}"

    # 1. Agregar al carrito. Cada reintento sortea producto y cantidad otra vez.
    r = con_reintentos(lambda: pedir("POST", "/carrito", etapa, escenario, json={
        "comprador_id": comprador_id,
        "producto_id": rng.randint(1, 200),
        "cantidad": rng.randint(1, 3),
    }), c, etapa)
    if r is None:
        etapa.suma("compras_abandonadas")
        return

    # 2. Confirmar el pago (encola la orden)
    r = con_reintentos(lambda: pedir("POST", "/pagos/confirmar", etapa, escenario,
                                     json={"comprador_id": comprador_id}), c, etapa)
    if r is None:
        etapa.suma("compras_abandonadas")
        return
    orden_id = r.json()["orden_id"]   # la API responde 202 (o 200 si es un reintento)

    # 3. Polling cada 5 s, sin reintentos. Si se cansa antes de ver "procesada", es abandono
    # aunque la orden siga en la cola.
    while not c.se_canso():
        time.sleep(INTERVALO_POLLING)
        ok, resp = pedir("GET", f"/ordenes/{orden_id}", etapa, escenario)
        if ok and resp.json().get("estado") == "procesada":
            return
    etapa.suma("compras_abandonadas")


# --- Medición de CPU con docker stats ---
def leer_cpu():
    """Devuelve {servicio: %CPU}, por ejemplo {"api": 35.2}. 100 % es un núcleo completo."""
    try:
        salida = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}};{{.CPUPerc}}"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return {}
    valores = {}
    # Los contenedores se llaman <proyecto>-<servicio>-<n>. Con 2 workers queda el último leído.
    for linea in salida.strip().splitlines():
        nombre, _, pct = linea.partition(";")
        for servicio in ("api", "worker", "db"):
            if f"-{servicio}-" in nombre:
                try:
                    valores[servicio] = float(pct.strip().rstrip("%"))
                except ValueError:
                    pass
    return valores


def muestrear_cpu(estado, escenario, parar):
    """Hilo de fondo: guarda la CPU en datos/cpu_log.csv hasta que se activa `parar`."""
    ruta = os.path.join(CARPETA_DATOS, "cpu_log.csv")
    es_nuevo = not os.path.exists(ruta)
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if es_nuevo:
            w.writerow(["ts_iso", "escenario", "etapa", "cpu_api", "cpu_worker", "cpu_db"])
        while not parar.is_set():
            v = leer_cpu()          # docker stats tarda unos 1-2 s por lectura
            etapa = estado["etapa"]
            if "api" in v:
                etapa.cpu.append(v["api"])
            w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        escenario, etapa.numero, v.get("api"), v.get("worker"), v.get("db")])
            f.flush()


# --- Ejecución ---
def lanzar_etapa(etapa, duracion, pool, escenario, rng):
    """Manda compradores durante `duracion` segundos. Lecturas y compras llegan como Poisson."""
    total = etapa.lecturas_s + etapa.compras_s
    fin = time.perf_counter() + duracion
    if total == 0:
        time.sleep(duracion)
        return
    # Como en SimPy: entre llegadas pasa un tiempo Exponencial(tasa total). Luego se escoge
    # si es lectura o compra con probabilidad proporcional a su tasa.
    proxima = time.perf_counter()
    while True:
        proxima += rng.expovariate(total)
        if proxima >= fin:
            break
        time.sleep(max(0.0, proxima - time.perf_counter()))
        semilla = rng.getrandbits(32)   # cada comprador tiene su propio generador
        if rng.random() < etapa.lecturas_s / total:
            # Cada comprador corre en su propio hilo mientras este ciclo sigue creando llegadas.
            pool.submit(operacion_lectura, etapa, escenario, random.Random(semilla))
        else:
            pool.submit(operacion_compra, etapa, escenario, random.Random(semilla))
    time.sleep(max(0.0, fin - time.perf_counter()))


def ordenes_pendientes():
    """Pregunta a /metricas cuántas órdenes hay en cola (None si no responde)."""
    try:
        return requests.get(f"{BASE}/metricas", timeout=5).json().get("ordenes_pendientes")
    except Exception:
        return None


def imprimir_etapa(etapa):
    """Resumen parcial de la etapa: los compradores aún activos solo salen en el JSON final."""
    r = etapa.resumen()
    print("=" * 76)
    print(f" ETAPA {r['etapa']} | DEMANDA: {r['lecturas_s']} lecturas/s + "
          f"{r['compras_s']} compras/s")
    print("=" * 76)
    print(f"  Operaciones: {r['operaciones']} | Peticiones: {r['peticiones']} | "
          f"Amplificacion: {r['amplificacion']}x")
    print(f"  Completadas: {r['completadas']} | Rechazadas: {r['rechazadas']} | "
          f"Timeouts: {r['timeouts']} | Otros errores: {r['errores']}")
    print(f"  p95: {r['p95_ms']} ms | p99: {r['p99_ms']} ms | "
          f"No atendidas: {r['tasa_no_atendidas'] * 100:.1f}% | CPU API: {r['cpu_media_pct']}% de 1 nucleo")
    print(f"  Ordenes pendientes al cerrar la etapa: {ordenes_pendientes()}")
    print(f"  --> ESTADO: {'SATURADO' if etapa.esta_saturada() else 'OPTIMO'}\n")


def main():
    """Lee los argumentos, recorre las etapas de la curva y guarda el resumen en JSON."""
    ap = argparse.ArgumentParser(description="Generador de carga del sistema real")
    ap.add_argument("--escenario", default="pico", choices=list(CURVAS))
    ap.add_argument("--duracion", type=float, default=DURACION_ETAPA,
                    help="segundos por etapa")
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--etiqueta", default=None,
                    help="nombre de la corrida en los CSV y en el JSON (por defecto, el escenario)")
    args = ap.parse_args()
    etiqueta = args.etiqueta or args.escenario

    os.makedirs(CARPETA_DATOS, exist_ok=True)
    rng = random.Random(args.semilla)  # la misma semilla repite las mismas llegadas
    curva = CURVAS[args.escenario]
    print(f"\nEscenario: {args.escenario} | {len(curva)} etapas de {args.duracion:.0f} s "
          f"| semilla {args.semilla}\n")

    etapas = [Etapa(i, lec, com) for i, (lec, com) in enumerate(curva, start=1)]
    estado = {"etapa": etapas[0]}   # etapa en curso, la lee el hilo de CPU
    # Hilo aparte que mide la CPU. `parar` es la bandera para avisarle que termine.
    parar = threading.Event()
    hilo_cpu = threading.Thread(target=muestrear_cpu, args=(estado, etiqueta, parar),
                                daemon=True)
    hilo_cpu.start()

    # Las etapas van seguidas, sin vaciar la tienda entre una y otra. Hilos de sobra porque en
    # el pico hay miles de compradores esperando a la vez.
    with ThreadPoolExecutor(max_workers=6000) as pool:
        for etapa in etapas:
            estado["etapa"] = etapa
            lanzar_etapa(etapa, args.duracion, pool, etiqueta, rng)
            imprimir_etapa(etapa)
        print("Esperando a que terminen los compradores que siguen activos...")
    parar.set()

    resultado = {
        "escenario": args.escenario,
        "etiqueta": etiqueta,
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "duracion_etapa_s": args.duracion,
        "semilla": args.semilla,
        "parametros": {
            "timeout_cliente_s": TIMEOUT_CLIENTE,
            "max_reintentos": MAX_REINTENTOS,
            "prob_reintento": PROB_REINTENTO,
            "paciencia_weibull_k": PACIENCIA_FORMA,
            "paciencia_weibull_escala_s": round(PACIENCIA_ESCALA, 2),
            "intervalo_polling_s": INTERVALO_POLLING,
        },
        "etapas": [e.resumen() for e in etapas],
    }
    salida = os.path.join(CARPETA_DATOS, f"gateway_{etiqueta}.json")
    with open(salida, "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)
    print(f"\nResumen final (con los compradores ya terminados) en {salida}")
    print("Telemetria por peticion en datos/telemetry_log.csv, CPU en datos/cpu_log.csv")


if __name__ == "__main__":
    main()
