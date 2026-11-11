"""Recolector: El modelo le avisa lo que pasa y resumen()
calcula las métricas de la Entrega 1 POR FASE (el promedio de la hora escondería la oferta):
M1 latencia p95 (s), M2 abandono de compras (%), M3 no atendidas (%), M4 amplificación
(peticiones por comprador), M5 costo (nodos·minuto), M6 utilización máxima de CPU (%)."""
import numpy as np

FASES = ("normal", "pico", "posterior")   # "pico" = la oferta


class Recolector:
    """Guarda contadores por fase y una serie segundo a segundo de una corrida."""

    def __init__(self, cfg):
        """Deja todos los contadores en cero."""
        self.cfg = cfg
        self.peticiones = {f: {"ok": 0, "rechazada": 0, "timeout": 0} for f in FASES}
        self.latencias = {f: [] for f in FASES}      # solo de las completadas
        self.operaciones = {f: 0 for f in FASES}     # compradores que llegaron
        self.compras = {f: 0 for f in FASES}         # compradores de compra
        self.abandonos_compra = {f: 0 for f in FASES}  # compradores de compra que se fueron
        self.esperas = []                            # espera en cola (para Wq)
        self.resultados = []                         # (instante, resultado) de cada petición
        # Una fila por segundo (columnas 0 a 6): t, utilización, nodos listos, nodos pagados,
        # órdenes en cola, peticiones en cola de CPU, peticiones en el sistema.
        self.serie = []

    def _fase(self, t):
        """Fase del instante t, o None si cae en el calentamiento (no se mide)."""
        if t < self.cfg.calentamiento_s:
            return None
        return self.cfg.fase(t)

    # --- Lo que llama el modelo ------------------------------------------
    def registrar_peticion(self, llegada, resultado, latencia):
        """Anota una petición en la fase del instante en que se envió."""
        # La lista completa (con calentamiento) es para las gráficas por minuto.
        self.resultados.append((llegada, resultado))
        f = self._fase(llegada)
        if f is not None:
            self.peticiones[f][resultado] += 1
            # Un timeout no es una latencia real: solo cuentan las respuestas.
            if resultado == "ok":
                self.latencias[f].append(latencia)

    def registrar_operacion(self, t, tipo):
        """Anota que llegó un comprador de tipo 'lectura' o 'compra'."""
        f = self._fase(t)
        if f is not None:
            self.operaciones[f] += 1
            if tipo == "compra":
                self.compras[f] += 1

    def registrar_abandono(self, inicio):
        """Un comprador se fue sin comprar. Cuenta en la fase en que LLEGÓ, como las compras."""
        f = self._fase(inicio)
        if f is not None:
            self.abandonos_compra[f] += 1

    def registrar_espera(self, llegada, espera):
        """Anota la espera en la cola de la CPU (s), sin el calentamiento."""
        if llegada >= self.cfg.calentamiento_s:
            self.esperas.append(espera)

    def registrar_estado(self, t, utilizacion, listos, pagados, ordenes, cola_cpu, en_sistema):
        """El monitor de modelo.py lo llama cada segundo: agrega una fila a la serie."""
        self.serie.append((t, utilizacion, listos, pagados, ordenes, cola_cpu, en_sistema))

    # --- Resumen ----------------------------------------------------------
    def resumen(self):
        """Devuelve {fase: {métrica: valor}} y el costo total en nodos·minuto."""
        serie = np.array(self.serie)
        salida = {}
        for f in FASES:
            p = self.peticiones[f]
            total = p["ok"] + p["rechazada"] + p["timeout"]
            lat = np.array(self.latencias[f])
            # True en los segundos de esta fase (sin el calentamiento).
            en_fase = np.array([t >= self.cfg.calentamiento_s and self.cfg.fase(t) == f
                                for t in serie[:, 0]])
            util = serie[en_fase, 1]          # columna 1 = utilización de cada segundo
            # M6: se promedia cada bloque de 10 s y se toma el mayor. Lo que sobra se deja fuera.
            bloques = len(util) // 10
            util_10s = util[:bloques * 10].reshape(bloques, 10).mean(axis=1) if bloques else util
            # Cada métrica lleva "if ... else" por si la fase no tuvo datos.
            salida[f] = {
                # M1: latencia de las peticiones completadas.
                "p95_s": float(np.percentile(lat, 95)) if len(lat) else float("nan"),
                "p99_s": float(np.percentile(lat, 99)) if len(lat) else float("nan"),
                "latencia_media_s": float(lat.mean()) if len(lat) else float("nan"),
                # M3: rechazadas o con timeout.
                "no_atendidas": (p["rechazada"] + p["timeout"]) / total if total else 0.0,
                # M2
                "abandono_compras": (self.abandonos_compra[f] / self.compras[f]
                                     if self.compras[f] else 0.0),
                # M4: da más de 1 por los pasos de la compra, el polling y los reintentos.
                "amplificacion": total / self.operaciones[f] if self.operaciones[f] else 0.0,
                # M6
                "util_max": float(util_10s.max()) if len(util_10s) else 0.0,
                "util_media": float(util.mean()) if len(util) else 0.0,
            }
        # M5: nodos pagados (columna 3) de cada segundo, pasado a minutos. Incluye el calentamiento.
        salida["costo_nodos_min"] = float(serie[:, 3].sum() / 60)
        return salida

    def series_por_minuto(self):
        """No atendidas y nodos pagados, minuto a minuto (para gráficas)."""
        minutos = int(self.cfg.horizonte_s // 60)
        # Columnas: instante de la petición y 1 si NO fue 'ok'.
        res = np.array([(t, r != "ok") for t, r in self.resultados])
        serie = np.array(self.serie)
        no_atendidas = np.zeros(minutos)
        nodos = np.zeros(minutos)
        for m in range(minutos):
            # El promedio de los 0/1 del minuto = fracción no atendida.
            en_min = (res[:, 0] >= m * 60) & (res[:, 0] < (m + 1) * 60)
            if en_min.any():
                no_atendidas[m] = res[en_min, 1].mean()
            # Promedio de nodos pagados (columna 3) en ese minuto.
            nodos[m] = serie[(serie[:, 0] >= m * 60) & (serie[:, 0] < (m + 1) * 60), 3].mean()
        return {"no_atendidas": no_atendidas, "nodos": nodos}
