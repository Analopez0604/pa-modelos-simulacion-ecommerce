"""
Calibración: convierte las mediciones de la prueba escalonada en parámetros del modelo SimPy.
La demanda sube cada 30 s (cada tramo es una "etapa"). Ajusta Lognormal vs Exponencial (KS),
saca D con U = X · D y re-escala la Lognormal para que su media sea D.
Escribe simulacion/parametros_calibrados.json y docs/figuras/calibracion_*.png.
    python analisis/calibrar.py
"""
import glob
import json
import math
import os

import numpy as np
import pandas as pd
from scipy import stats

from estilo import COLORES, guardar, linea_sla, plt

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATOS = os.path.join(RAIZ, "datos")
SALIDA = os.path.join(RAIZ, "simulacion", "parametros_calibrados.json")

# Límite de CPU de la API en docker-compose.yml. docker stats mide en % de UN núcleo,
# así que U = cpu% / (100 · CPU_NODO). Ejemplo: 25 % -> U = 25 / 50 = 0,5.
CPU_NODO = 0.5

# Nombre en la telemetría de la API -> nombre en el modelo SimPy
TIPOS = {
    "lectura": "lectura",
    "escritura_rapida": "carrito",
    "escritura_lenta": "pago",
    "consulta_orden": "consulta_orden",
}
# Con CPU < 30 % casi no hay cola, así la latencia medida es casi solo servicio.
U_DESCARGADO = 0.30
MAX_NO_ATENDIDAS = 0.01  # etapas "sin rechazos" (≤ 1 %) para la ley de utilización


def tabla_por_etapa(tel, cpu, gateway):
    """Una fila por etapa: utilización, throughput, D = U/X y lo que vio el cliente."""
    duracion = gateway["duracion_etapa_s"]
    # Solo peticiones exitosas (los 503 de rechazo quedan fuera)
    ok = tel[tel.estado_http < 400]
    # U = CPU promedio de la etapa como fracción del nodo. X = atendidas por segundo.
    # groupby("etapa") saca un valor por etapa. dropna() quita etapas sin alguno de los dos.
    t = pd.DataFrame({
        "U": cpu.groupby("etapa").cpu_api.mean() / (100 * CPU_NODO),
        "X_pet_s": ok.groupby("etapa").size() / duracion,
    }).dropna()
    # Ley de utilización despejada: D = U / X (segundos de CPU por petición), ×1000 -> ms
    t["D_ms"] = t.U / t.X_pet_s * 1000
    # Lo que midió el cliente (gateway) en cada etapa. Las ofrecidas incluyen reintentos.
    cliente = pd.DataFrame(gateway["etapas"]).set_index("etapa")
    t["ofrecidas_pet_s"] = cliente.peticiones / duracion
    t["p95_cliente_ms"] = cliente.p95_ms
    t["no_atendidas"] = cliente.tasa_no_atendidas
    t["lecturas_s"] = cliente.lecturas_s
    t["compras_s"] = cliente.compras_s
    return t


def ajustar_servicio(lat_s):
    """Ajusta una Lognormal y una Exponencial a las latencias (s) y las compara con KS."""
    # Máxima verosimilitud: mu = promedio de ln(x), sigma = desviación estándar de ln(x).
    # scipy devuelve (forma = sigma, loc = 0, escala = e^mu). floc=0: sin desplazamiento.
    sigma, _, escala = stats.lognorm.fit(lat_s, floc=0)
    mu = math.log(escala)
    # KS: D = max |F_n(x) - F(x)|, F_n = proporción de datos ≤ x, F = acumulada ajustada.
    # D pequeño = la curva se parece a los datos.
    ks_log = stats.kstest(lat_s, "lognorm", args=(sigma, 0, escala))
    # Exponencial: escala = 1/λ = media de los datos. Misma prueba KS para comparar.
    _, escala_exp = stats.expon.fit(lat_s, floc=0)
    ks_exp = stats.kstest(lat_s, "expon", args=(0, escala_exp))
    return {
        "n": int(len(lat_s)),
        "mu": mu,
        "sigma": sigma,
        # Media de una Lognormal: E[X] = e^(mu + sigma²/2), pasada a milisegundos
        "media_ms": math.exp(mu + sigma ** 2 / 2) * 1000,
        "ks_lognormal_D": ks_log.statistic,
        "ks_lognormal_p": ks_log.pvalue,
        "ks_exponencial_D": ks_exp.statistic,
        "ks_exponencial_p": ks_exp.pvalue,
    }


def main():
    """Lee las mediciones, hace los tres pasos de la calibración y guarda JSON y figuras."""
    # glob busca todos los ordenes_log*.csv (uno por worker) y pd.concat los une.
    tel = pd.read_csv(os.path.join(DATOS, "telemetry_log.csv"))
    cpu = pd.read_csv(os.path.join(DATOS, "cpu_log.csv"))
    ordenes = pd.concat(pd.read_csv(r) for r in glob.glob(os.path.join(DATOS, "ordenes_log*.csv")))
    with open(os.path.join(DATOS, "gateway_escalonada.json"), encoding="utf-8") as f:
        gateway = json.load(f)
    # Solo la prueba escalonada (los logs también tienen reposo, pico...)
    tel = tel[tel.escenario == "escalonada"]
    cpu = cpu[cpu.escenario == "escalonada"]

    # ---- Paso 2: ley de utilización por etapa --------------------------------
    # U = X · D -> D = U / X  (U = utilización del nodo, X = peticiones atendidas por s).
    # Solo etapas sin rechazos: un 503 gasta CPU pero no cuenta como atendida e inflaría D.
    etapas = tabla_por_etapa(tel, cpu, gateway)
    usadas = etapas[etapas.no_atendidas <= MAX_NO_ATENDIDAS]
    d = usadas.D_ms.mean() / 1000                    # segundos de nodo por petición
    d_de = usadas.D_ms.std() / 1000
    # Capacidad = 1 / D: si cada petición ocupa D segundos, al 100 % caben 1/D por segundo
    capacidad = 1 / d
    print(f"\nLey de utilizacion por etapa (U = X * D), nodo de {CPU_NODO} CPU:")
    print(etapas.round(3).to_string())
    print(f"\nEtapas sin rechazos: {list(usadas.index)}")
    print(f"D = {d * 1000:.2f} ± {d_de * 1000:.2f} ms (media ± desv. estandar)")
    print(f"Capacidad de un nodo al 100 %: {capacidad:.0f} peticiones/s")

    # ---- Paso 1: forma del tiempo de servicio ---------------------------------
    # Etapas "descargadas" (CPU < 30 %) y solo peticiones exitosas: casi sin espera en cola
    descargadas = etapas.index[etapas.U < U_DESCARGADO]
    base = tel[tel.etapa.isin(descargadas) & (tel.estado_http < 400)]
    ajustes = {}
    for tipo_api, tipo in TIPOS.items():
        lat = base[base.tipo == tipo_api].latencia_ms.to_numpy() / 1000
        ajustes[tipo] = ajustar_servicio(lat)
        # Proporción de este tipo en la mezcla
        ajustes[tipo]["proporcion"] = len(lat) / len(base)
    print(f"\nAjuste del tiempo de servicio (etapas {list(descargadas)}, U < {U_DESCARGADO:.0%}):")
    print(f"{'tipo':16s} {'n':>5s} {'mu':>7s} {'sigma':>6s} {'media':>8s}"
          f" {'KS logn D':>10s} {'KS expo D':>10s}")
    for tipo, r in ajustes.items():
        print(f"{tipo:16s} {r['n']:5d} {r['mu']:7.3f} {r['sigma']:6.3f} "
              f"{r['media_ms']:6.2f}ms {r['ks_lognormal_D']:10.3f} {r['ks_exponencial_D']:10.3f}")

    # ---- Paso 3: parámetros del gemelo ---------------------------------------
    # Media ponderada = Σ proporción_i · media_i (tiempo de una petición cualquiera)
    media_ponderada = sum(r["proporcion"] * r["media_ms"] / 1000 for r in ajustes.values())
    # Re-escalado: mu' = mu + ln(k), misma sigma, con k = D / media_ponderada.
    # Multiplicar una Lognormal por k le suma ln(k) a mu, así la media del gemelo queda en D.
    servicio = {
        tipo: [r["mu"] + math.log(d / media_ponderada), r["sigma"]]
        for tipo, r in ajustes.items()
    }
    print("\nTiempo de CPU por peticion en el gemelo (sin carga):")
    for tipo, (mu, sigma) in servicio.items():
        print(f"  {tipo:16s} Lognormal(mu={mu:.3f}, sigma={sigma:.3f}) "
              f"media {math.exp(mu + sigma ** 2 / 2) * 1000:.2f} ms")
    # Tiempo del worker por orden (ms -> s). Solo para mostrar.
    worker = ordenes.servicio_ms / 1000
    # "servicio_lognormal_cpu" es lo que usa el modelo SimPy
    resultado = {
        "origen": "analisis/calibrar.py sobre la prueba escalonada del sistema real",
        "cpu_nodo": CPU_NODO,
        "servicio_lognormal_cpu": servicio,
        "demanda_cpu_ms": {"media": d * 1000, "desv": d_de * 1000},
        "capacidad_nodo_pet_s": capacidad,
        "ajuste_latencia": ajustes,
        "worker_ordenes_s": {"media": float(worker.mean()), "min": float(worker.min()),
                             "max": float(worker.max())},
        "etapas": etapas.reset_index().round(4).to_dict(orient="records"),
    }
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)
    print(f"\nWorker de ordenes: media {worker.mean() * 1000:.0f} ms "
          f"(min {worker.min() * 1000:.0f}, max {worker.max() * 1000:.0f})")
    print(f"Parametros guardados en {os.path.relpath(SALIDA, RAIZ)}")

    # ---- Figuras ---------------------------------------------------------------
    print("\nFiguras:")
    # Figura 1: histograma de la latencia de las lecturas (ms) con las dos curvas encima
    lat = base[base.tipo == "lectura"].latencia_ms.to_numpy()
    r = ajustes["lectura"]
    fig, ax = plt.subplots()
    # Hasta el percentil 99 para que el 1 % más lento no estire el eje.
    # density=True: el área total vale 1, así se compara con una densidad.
    ax.hist(lat, bins=60, range=(0, np.percentile(lat, 99)), density=True,
            color=COLORES[0], alpha=0.35, label="Medido (GET /catalogo)")
    # Lognormal ajustada (escala e^mu en ms) y Exponencial con la media de los datos
    x = np.linspace(0.01, np.percentile(lat, 99), 300)
    ax.plot(x, stats.lognorm.pdf(x, r["sigma"], 0, math.exp(r["mu"]) * 1000),
            color=COLORES[1], label=f"Lognormal (KS D = {r['ks_lognormal_D']:.3f})")
    ax.plot(x, stats.expon.pdf(x, 0, lat.mean()), color=COLORES[2],
            label=f"Exponencial (KS D = {r['ks_exponencial_D']:.3f})")
    ax.set_xlabel("Latencia dentro de la API (ms)")
    ax.set_ylabel("Densidad (1/ms)")
    ax.set_title("La Lognormal describe la lectura mucho mejor que la Exponencial")
    ax.legend()
    guardar(fig, "calibracion_ajuste_lectura.png")

    # Figura 2: p95 y no atendidas que vio el cliente a medida que sube la demanda
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.2))
    demanda = etapas.lecturas_s + etapas.compras_s
    ax1.plot(demanda, etapas.p95_cliente_ms / 1000, marker="o", color=COLORES[0],
             label="p95 medido")
    linea_sla(ax1, 2.0, "SLA p95 = 2 s")
    ax1.set_xlabel("Demanda (compradores/s)")
    ax1.set_ylabel("p95 de latencia (s)")
    ax1.legend()
    ax2.plot(demanda, etapas.no_atendidas * 100, marker="o", color=COLORES[0],
             label="No atendidas medidas")
    linea_sla(ax2, 10, "SLA 10 %")
    ax2.set_xlabel("Demanda (compradores/s)")
    ax2.set_ylabel("Peticiones no atendidas (%)")
    ax2.legend()
    # Etapas que rompen el SLA (> 10 % no atendidas o p95 > 2 s). El título dice la primera.
    falla = etapas[(etapas.no_atendidas > 0.10) | (etapas.p95_cliente_ms > 2000)]
    if len(falla):
        fig.suptitle(f"Un nodo real deja de cumplir el SLA desde "
                     f"{demanda[falla.index[0]]:.0f} compradores/s")
    guardar(fig, "calibracion_escalonada_real.png")


if __name__ == "__main__":
    main()
