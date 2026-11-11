"""Políticas de autoescalado que comparamos en P1 y P3: fija, reactiva y pre-escalado.
Patrón Strategy: todas tienen configurar_inicio(plat) y revisar(plat) (cada 30 s). Así el
modelo es igual y las diferencias se deben solo a la política. plat = Plataforma de modelo.py."""
import math


class PoliticaGestion:
    """Interfaz común y capacidad fija: nunca escala."""

    nombre = "fija"

    def configurar_inicio(self, plat):
        """Crea los nodos iniciales ya listos (sin tiempo de arranque)."""
        for _ in range(plat.cfg.nodos_iniciales):
            plat.crear_nodo_listo()

    def revisar(self, plat):
        """La capacidad fija no hace nada."""
        pass


class AutoescaladoReactivo(PoliticaGestion):
    """Política actual: escala DESPUÉS de ver la CPU alta (regla del HPA de Kubernetes)."""

    def __init__(self, umbral=0.80):
        """umbral: CPU objetivo (0.80 = 80 %). P1 compara 0.80 contra 0.60."""
        self.umbral = umbral
        self.nombre = f"reactivo_{int(umbral * 100)}"     # p. ej. "reactivo_80"
        self.ultima_reduccion = 0.0                       # (s) instante de la última reducción

    def nodos_deseados(self, plat):
        """⌈CPU usada / umbral⌉, entre nodos_min y nodos_max (2 nodos al 100 % / 0,8 -> 3)."""
        # CPU usada en nodos, multiplicada segundo a segundo: con los nodos de ahora,
        # un nodo recién llegado haría pedir uno de más.
        deseados = math.ceil(plat.cpu_usada_reciente() / self.umbral)
        return max(plat.cfg.nodos_min, min(plat.cfg.nodos_max, deseados))

    def revisar(self, plat):
        """Si faltan nodos los pide. Si sobran, quita uno."""
        deseados = self.nodos_deseados(plat)
        # Subir: cuenta también los que arrancan, para no pedirlos dos veces.
        if deseados > plat.total_nodos():
            plat.agregar_nodos(deseados - plat.total_nodos())
        # Bajar despacio: un nodo, sin nodos arrancando y 5 min después de la última reducción.
        elif (deseados < len(plat.nodos) and plat.arrancando == 0
              and plat.env.now - self.ultima_reduccion >= plat.cfg.espera_reduccion_s):
            plat.quitar_nodos(1)
            self.ultima_reduccion = plat.env.now


class Preescalado(AutoescaladoReactivo):
    """P3: pide nodos ANTES de la oferta, porque un nodo tarda de 2 a 8 min en arrancar."""

    def __init__(self, nodos_objetivo, umbral=0.80, anticipacion_s=600):
        """nodos_objetivo: 2 o 3 en P3. anticipacion_s: cuánto antes se piden (10 min)."""
        super().__init__(umbral)          # hereda la regla reactiva con el mismo umbral
        self.nodos_objetivo = nodos_objetivo
        self.anticipacion_s = anticipacion_s
        self.nombre = f"preescalado_{nodos_objetivo}"     # p. ej. "preescalado_2"

    def revisar(self, plat):
        """En la ventana asegura nodos_objetivo. Fuera de ella actúa como el reactivo."""
        cfg = plat.cfg
        # Ventana: de 10 min antes de la oferta hasta que termina (600 s a 1800 s).
        en_ventana = (cfg.inicio_oferta_s - self.anticipacion_s
                      <= plat.env.now < cfg.fin_oferta_s)
        if not en_ventana:
            super().revisar(plat)
            return
        # Si la CPU pasa el umbral, el reactivo puede agregar más. Aquí nunca se quitan nodos.
        deseados = max(self.nodos_objetivo, self.nodos_deseados(plat))
        if deseados > plat.total_nodos():
            plat.agregar_nodos(deseados - plat.total_nodos())
