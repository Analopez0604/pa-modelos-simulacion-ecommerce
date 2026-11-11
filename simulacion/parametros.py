"""Parámetros de la simulación (clase Config), todo en SEGUNDOS. Lee parametros_calibrados.json.
Otro escenario: dataclasses.replace(Config(), campo=valor).
Origen: [MEDIDO] medido y calibrado, [REAL] igual que docker-compose.yml, [E1] supuesto de
la Entrega 1, [E1*] supuesto de la Entrega 1 corregido, [DECISIÓN] lo que movemos en P1 a P4."""
import json
import math
import os
from dataclasses import dataclass, field

# Se lee una vez al importar. La ruta sale de este archivo, así funciona desde cualquier carpeta.
_RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parametros_calibrados.json")
with open(_RUTA, encoding="utf-8") as _f:
    CALIBRACION = json.load(_f)


@dataclass
class Config:
    """Configuración de UNA corrida."""

    # ---- Horizonte: 20 min normal + 10 min de oferta + 30 min después --------
    duracion_normal_s: float = 20 * 60      # (s) tráfico normal antes de la oferta
    duracion_oferta_s: float = 10 * 60      # (s) la oferta relámpago
    duracion_posterior_s: float = 30 * 60   # (s) después de la oferta
    calentamiento_s: float = 5 * 60          # (s) no se mide: el sistema arranca vacío

    # ---- Demanda: compradores que llegan por segundo [E1] -------------------
    lecturas_s: float = 9.0                 # (compradores/s) solo miran el catálogo (90 %)
    compras_s: float = 1.0                  # (compradores/s) van a comprar (10 %)
    multiplicador_lecturas: float = 20.0    # (veces) en la oferta: 9 x 20 = 180 lecturas/s
    multiplicador_compras: float = 6.0      # (veces) en la oferta: 1 x 6 = 6 compras/s

    # ---- Un nodo (una copia de la API) [REAL] -------------------------------
    cpu_por_nodo: int = 1                    # (cajeros) un servidor = los 0,5 CPU del contenedor
    tope_acceso: int = 100                   # (peticiones) --limit-concurrency: la siguiente da 503
    # No modelamos el pool de 10 conexiones: la espera medida fue de 0,1 a 0,2 ms.
    # (s de CPU) Lognormal(mu, sigma) por tipo de petición [MEDIDO]. Promedio D ≈ 7,4 ms.
    servicio_lognormal: dict = field(
        default_factory=lambda: dict(CALIBRACION["servicio_lognormal_cpu"]))
    servicio_exponencial: bool = False       # True = Exponencial de igual media (contraste M/M/c)

    # ---- Cola de órdenes ------------------------------------------------------
    workers_ordenes: int = 2                 # [E1*] con 1 worker no alcanza en el pico
    trabajo_orden_s: tuple = (0.15, 0.40)    # (s) Uniforme, igual que el worker real [REAL]
    tope_cola_ordenes: int = 500             # (órdenes) cola llena = pago rechazado [REAL]

    # ---- Comprador -------------------------------------------------------------
    # (s) cuánto espera UNA respuesta. None = espera lo que sea (contraste M/M/c).
    timeout_cliente_s: float = 5.0           # [DECISIÓN P2] la política actual
    max_reintentos: int = 3                  # 3 reintentos -> backoff de 1, 2 y 4 s [E1]
    prob_reintento: float = 0.60             # el 60 % reintenta, el 40 % se va al primer fallo [E1]
    backoff_base_s: float = 1.0              # (s) se duplica en cada intento [E1]
    # Paciencia = cuánto aguanta antes de irse. Es una Weibull.
    paciencia_forma: float = 2.0             # [E1*] Weibull k = 2 (cada vez más impaciente)
    paciencia_media_s: float = 20.0          # (s) paciencia promedio [E1]
    intervalo_polling_s: float = 5.0         # (s) cada cuánto consulta su orden [E1]

    # ---- Autoescalado ------------------------------------------------------------
    nodos_iniciales: int = 1                 # [REAL] el sistema real tiene 1 nodo
    nodos_min: int = 1                       # (nodos) nunca baja de 1
    nodos_max: int = 8                       # (nodos) nunca sube de 8
    arranque_nodo_s: tuple = (120, 480)      # (s) Uniforme(2, 8) min hasta quedar listo [E1]
    periodo_revision_s: float = 30.0         # (s) revisa y mira la CPU de esos 30 s
    espera_reduccion_s: float = 300.0        # (s) no quita más de 1 nodo cada 5 min

    # ---- Derivados (@property: se usa como campo, pero se calcula) ----------------
    @property
    def paciencia_escala_s(self):
        """Escala de la Weibull (s) = media / Gamma(1 + 1/k). Con 20 s y k = 2 da unos 22,6 s."""
        return self.paciencia_media_s / math.gamma(1 + 1 / self.paciencia_forma)

    @property
    def inicio_oferta_s(self):
        """Instante (s) en que empieza la oferta (1200 s)."""
        return self.duracion_normal_s

    @property
    def fin_oferta_s(self):
        """Instante (s) en que termina la oferta (1800 s)."""
        return self.duracion_normal_s + self.duracion_oferta_s

    @property
    def horizonte_s(self):
        """Duración total de la corrida (3600 s)."""
        return self.fin_oferta_s + self.duracion_posterior_s

    def fase(self, t):
        """'normal', 'pico' (= la oferta) o 'posterior'."""
        if t < self.inicio_oferta_s:
            return "normal"
        if t < self.fin_oferta_s:
            return "pico"
        return "posterior"

    def tasa(self, tipo, t):
        """Compradores/s en el instante t. Cambia con la fase (Poisson no homogéneo)."""
        en_oferta = self.fase(t) == "pico"
        if tipo == "lectura":
            return self.lecturas_s * (self.multiplicador_lecturas if en_oferta else 1)
        return self.compras_s * (self.multiplicador_compras if en_oferta else 1)
