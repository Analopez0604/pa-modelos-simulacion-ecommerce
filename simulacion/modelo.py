"""Modelo de la tienda en SimPy (eventos discretos)

Camino de una petición, igual que en el sistema real (sistema_real/app/main.py):
    llega -> el balanceador elige el nodo menos cargado
          -> PUNTO DE ACCESO del nodo: si ya hay TOPE_ACCESO peticiones adentro -> 503
          -> CPU DEL NODO: cola FIFO y 1 servidor, servicio Lognormal -> responde

Timeout no es lo mismo que abandono:
    timeout  -> el cliente deja de esperar UNA petición, pero el servidor la sigue procesando
    abandono -> el COMPRADOR se va (se le acabó la paciencia o no reintenta)
"""
import math
import random

import simpy

from recolector import Recolector


# ---- Entidades ---------------------------------------------------------
class Comprador:
    """El cliente del banco. Recuerda su paciencia y si es de los que reintentan."""

    def __init__(self, env, cfg, rng):
        """Sortea la paciencia y si reintenta."""
        self.inicio = env.now                     # (s) cuándo llegó a la tienda
        # Paciencia (s): Weibull(escala, forma k = 2), con media 20 s.
        self.paciencia = rng.weibullvariate(cfg.paciencia_escala_s, cfg.paciencia_forma)
        self.reintenta = rng.random() < cfg.prob_reintento
        self.orden = None                         # la Orden que crea el pago (si compra)

    def se_canso(self, ahora):
        """True si ya pasó más tiempo que su paciencia."""
        return ahora - self.inicio > self.paciencia


class Orden:
    """Se crea al confirmar el pago. El worker la marca como procesada."""

    def __init__(self):
        """Nace sin procesar."""
        self.procesada = False


# ---- Recursos ----------------------------------------------------------
class Nodo:
    """Una copia de la API: punto de acceso + CPU."""

    def __init__(self, env, cfg):
        """Crea el cajero (CPU) del nodo y sus contadores."""
        # simpy.Resource = el cajero del banco: capacity cajeros (aquí 1) y una fila FIFO.
        self.cpu = simpy.Resource(env, capacity=cfg.cpu_por_nodo)
        self.en_sistema = 0          # peticiones dentro del nodo
        self.cpu_ocupada_s = 0.0     # segundos de CPU usados desde que arrancó
        self._cpu_ocupada_antes = 0.0   # valor de cpu_ocupada_s en la medición anterior
        self.util_ultimo_s = 0.0     # utilización del último segundo

    def actualizar_util(self):
        """Cada segundo: CPU usada en ese segundo / capacidad (como docker stats)."""
        # Cada servicio se anota completo en el segundo en que empieza (dura pocos ms).
        usada = self.cpu_ocupada_s - self._cpu_ocupada_antes
        self._cpu_ocupada_antes = self.cpu_ocupada_s
        self.util_ultimo_s = min(1.0, usada / self.cpu.capacity)


class Plataforma:
    """Estado del sistema. La política (patrón Strategy) decide los nodos y esta clase obedece."""

    def __init__(self, env, cfg, politica, semilla):
        """Recibe el entorno SimPy, la Config, la política y la semilla de la réplica."""
        self.env = env
        self.cfg = cfg
        self.politica = politica
        # Semilla fija: la misma semilla da la misma corrida (las 30 réplicas se pueden repetir).
        self.rng = random.Random(semilla)
        self.nodos = []              # nodos listos (reciben tráfico)
        self.arrancando = 0          # nodos pedidos que aún no están listos
        # simpy.Store = la cola de órdenes: put() mete una orden y get() la saca o espera.
        self.cola_ordenes = simpy.Store(env)
        self.met = Recolector(cfg)   # la "libreta" donde se anotan las métricas

    # --- Nodos -----------------------------------------------------------
    def crear_nodo_listo(self):
        """Agrega un nodo que ya recibe tráfico (sin esperar arranque)."""
        self.nodos.append(Nodo(self.env, self.cfg))

    def agregar_nodos(self, cantidad):
        """Pide nodos nuevos. Cada arranque es un proceso aparte y corren en paralelo."""
        for _ in range(cantidad):
            self.env.process(self._arrancar_nodo())

    def _arrancar_nodo(self):
        """Espera el arranque (120 a 480 s) y deja el nodo listo."""
        self.arrancando += 1          # mientras arranca ya se paga, pero no atiende
        # env.timeout(t): este proceso duerme t segundos de simulación.
        yield self.env.timeout(self.rng.uniform(*self.cfg.arranque_nodo_s))
        self.arrancando -= 1
        self.crear_nodo_listo()

    def quitar_nodos(self, cantidad):
        """Saca nodos del balanceador, sin bajar de nodos_min. Terminan lo que tenían."""
        for _ in range(cantidad):
            if len(self.nodos) > self.cfg.nodos_min:
                self.nodos.pop()

    def total_nodos(self):
        """Nodos que se pagan: los listos y los que están arrancando."""
        return len(self.nodos) + self.arrancando

    def utilizacion(self):
        """Utilización promedio de CPU de los nodos listos en el último segundo."""
        return sum(n.util_ultimo_s for n in self.nodos) / len(self.nodos)

    def cpu_usada_reciente(self):
        """CPU de los últimos 30 s en nodos (2 nodos al 50 % = 1). La lee el autoescalador."""
        # Una fila por segundo: fila[1] = utilización, fila[2] = nodos listos.
        ultimas = self.met.serie[-int(self.cfg.periodo_revision_s):]
        return sum(fila[1] * fila[2] for fila in ultimas) / len(ultimas) if ultimas else 0.0

    # --- Tiempos de servicio ------------------------------------------------
    def tiempo_servicio(self, tipo):
        """Tiempo de CPU de una petición (s): Lognormal(mu, sigma) calibrada."""
        mu, sigma = self.cfg.servicio_lognormal[tipo]
        # Modo M/M/c: Exponencial con la MISMA media, exp(mu + sigma²/2).
        # expovariate recibe la tasa = 1 / media.
        if self.cfg.servicio_exponencial:
            return self.rng.expovariate(1.0 / math.exp(mu + sigma ** 2 / 2))
        return self.rng.lognormvariate(mu, sigma)


# ---- Una petición: lado del servidor y lado del cliente ----------------
def atender(env, plat, tipo, comprador):
    """Lado del SERVIDOR: da 'ok' o 'rechazada'. Sigue aunque el cliente se vaya por timeout."""
    cfg = plat.cfg
    nodo = min(plat.nodos, key=lambda n: n.en_sistema)   # balanceador: el menos cargado
    # Punto de acceso lleno (100 adentro): 503 sin esperar ni gastar CPU.
    if nodo.en_sistema >= cfg.tope_acceso:
        return "rechazada"

    nodo.en_sistema += 1
    llegada = env.now
    # request/release del taller: el "with" libera la CPU al salir del bloque.
    with nodo.cpu.request() as turno:
        yield turno                    # espera turno de la CPU (fila FIFO)
        plat.met.registrar_espera(llegada, env.now - llegada)   # tiempo en cola (Wq)
        servicio = plat.tiempo_servicio(tipo)
        nodo.cpu_ocupada_s += servicio                   # para calcular la utilización
        yield env.timeout(servicio)    # la CPU trabaja en la petición
    nodo.en_sistema -= 1

    # Una sola orden por comprador: un reintento del pago responde lo mismo, como la API real.
    if tipo == "pago" and comprador.orden is None:
        # Cola de órdenes llena (500): el pago se rechaza aunque ya gastó CPU.
        if len(plat.cola_ordenes.items) >= cfg.tope_cola_ordenes:
            return "rechazada"
        comprador.orden = Orden()
        plat.cola_ordenes.put(comprador.orden)           # Store.put: la deja para un worker
    return "ok"


def pedir(env, plat, tipo, comprador):
    """Lado del CLIENTE: espera hasta timeout_cliente_s. Da 'ok', 'rechazada' o 'timeout'."""
    llegada = env.now
    # Proceso aparte: el servidor sigue trabajando aunque el cliente se vaya (trabajo perdido).
    servidor = env.process(atender(env, plat, tipo, comprador))
    if plat.cfg.timeout_cliente_s is None:              # modo M/M/c: espera lo que sea
        yield servidor
    else:
        # servidor | env.timeout(t) = lo que pase primero: la respuesta o el timeout (5 s).
        yield servidor | env.timeout(plat.cfg.timeout_cliente_s)
    # Si el servidor terminó, su resultado está en .value. Si no, ganó el reloj.
    resultado = servidor.value if servidor.triggered else "timeout"
    plat.met.registrar_peticion(llegada, resultado, env.now - llegada)
    return resultado


def con_reintentos(env, plat, tipo, comprador):
    """Hasta 1 + max_reintentos intentos con backoff. True si lo logró, False si se rindió."""
    cfg = plat.cfg
    for intento in range(cfg.max_reintentos + 1):
        # yield from = correr pedir() aquí mismo y recibir su return.
        resultado = yield from pedir(env, plat, tipo, comprador)
        if resultado == "ok":
            return True
        if (not comprador.reintenta or intento == cfg.max_reintentos
                or comprador.se_canso(env.now)):
            return False
        # Backoff: espera 1, 2, 4 s por un azar entre 0,5 y 1,5, para que no
        # todos los compradores reintenten al mismo tiempo.
        yield env.timeout(cfg.backoff_base_s * 2 ** intento * plat.rng.uniform(0.5, 1.5))
        if comprador.se_canso(env.now):
            return False
    return False                       # no se llega aquí: el ciclo sale antes


# ---- Compradores -------------------------------------------------------
def comprador_lectura(env, plat):
    """Comprador que solo mira el catálogo (una petición con reintentos)."""
    comprador = Comprador(env, plat.cfg, plat.rng)
    plat.met.registrar_operacion(env.now, "lectura")     # cuenta para la amplificación (M4)
    # Si falla no se anota abandono: el abandono (M2) solo mide a los que compran.
    yield from con_reintentos(env, plat, "lectura", comprador)


def comprador_compra(env, plat):
    """Comprador que compra: carrito, pago y consultas de su orden."""
    cfg = plat.cfg
    comprador = Comprador(env, cfg, plat.rng)
    plat.met.registrar_operacion(env.now, "compra")

    for paso in ("carrito", "pago"):
        ok = yield from con_reintentos(env, plat, paso, comprador)
        if not ok:
            plat.met.registrar_abandono(comprador.inicio)
            return

    # Polling: consulta su orden cada 5 s hasta verla procesada o cansarse (sin reintentos).
    while not comprador.se_canso(env.now):
        yield env.timeout(cfg.intervalo_polling_s)
        resultado = yield from pedir(env, plat, "consulta_orden", comprador)
        if resultado == "ok" and comprador.orden.procesada:
            return                                           # compra completa
    plat.met.registrar_abandono(comprador.inicio)


# ---- Procesos de fondo -------------------------------------------------
def llegadas(env, plat, tipo):
    """Generador de clientes del banco: Poisson no homogéneo, la tasa cambia con la fase."""
    proceso = comprador_lectura if tipo == "lectura" else comprador_compra
    while True:
        tasa = plat.cfg.tasa(tipo, env.now)
        if tasa == 0:                      # este tipo no llega en esta fase
            yield env.timeout(1.0)
            continue
        # Espera un tiempo Exponencial de media 1/tasa y lanza el comprador sin esperarlo.
        yield env.timeout(plat.rng.expovariate(tasa))
        env.process(proceso(env, plat))


def worker_ordenes(env, plat):
    """Saca órdenes de la cola (FIFO) y las procesa una a la vez."""
    while True:
        # Store.get(): si la cola está vacía, el worker espera aquí.
        orden = yield plat.cola_ordenes.get()
        yield env.timeout(plat.rng.uniform(*plat.cfg.trabajo_orden_s))
        orden.procesada = True        # el comprador lo verá en su próxima consulta


def monitor(env, plat):
    """Cada segundo anota utilización, nodos y colas en la serie."""
    while True:
        for nodo in plat.nodos:
            nodo.actualizar_util()
        plat.met.registrar_estado(
            env.now, plat.utilizacion(), len(plat.nodos), plat.total_nodos(),
            len(plat.cola_ordenes.items),
            sum(len(n.cpu.queue) for n in plat.nodos),     # esperando CPU
            sum(n.en_sistema for n in plat.nodos),         # dentro de los nodos
        )
        yield env.timeout(1.0)        # duerme 1 s y vuelve a medir


def autoescalador(env, plat):
    """Cada 30 s le pregunta a la política (Strategy) qué hacer."""
    while True:
        yield env.timeout(plat.cfg.periodo_revision_s)
        plat.politica.revisar(plat)


# ---- Una corrida completa ----------------------------------------------
def simular(cfg, politica, semilla):
    """Corre una réplica y devuelve el Recolector con todo lo medido."""
    env = simpy.Environment()                        # el reloj de la simulación
    plat = Plataforma(env, cfg, politica, semilla)
    politica.configurar_inicio(plat)                 # crea los nodos iniciales
    env.process(llegadas(env, plat, "lectura"))
    env.process(llegadas(env, plat, "compra"))
    for _ in range(cfg.workers_ordenes):
        env.process(worker_ordenes(env, plat))
    env.process(monitor(env, plat))
    env.process(autoescalador(env, plat))
    env.run(until=cfg.horizonte_s)                   # hasta el final del horizonte (3600 s)
    return plat.met
