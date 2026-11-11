"""
Estilo común de las figuras (colores, ejes, línea del SLA y guardado), para que todas se
vean iguales en el informe. No se ejecuta solo: lo importan calibrar.py, contraste_mmc.py
y graficas.py. Guarda los .png en docs/figuras/.
"""
import os

import matplotlib

# "Agg" = dibujar y guardar a archivo, sin abrir ventanas. Tiene que ir antes de pyplot.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# main.py cambia CARPETA_FIGURAS en modo --rapido.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARPETA_FIGURAS = os.path.join(RAIZ, "docs", "figuras")

# Orden fijo (azul, naranja, aqua, violeta): la serie 1 es azul en todas las figuras.
COLORES = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6"]
COLOR_SLA = "#e34948"     # rojo
TINTA = "#52514e"         # gris oscuro para los ejes

# Valores por defecto de todas las figuras (tamaño en pulgadas, sin borde arriba ni a la derecha)
plt.rcParams.update({
    "figure.figsize": (8, 4.5),
    "figure.dpi": 120,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": TINTA,
    "axes.labelcolor": "#0b0b0b",
    "axes.grid": True,
    "grid.color": "#e4e3df",
    "grid.linewidth": 0.8,
    "xtick.color": TINTA,
    "ytick.color": TINTA,
    "lines.linewidth": 2,
    "font.size": 11,
    "legend.frameon": False,
})


def linea_sla(ax, valor, texto):
    """Línea horizontal roja punteada en el límite del SLA. `texto` sale en la leyenda."""
    ax.axhline(valor, color=COLOR_SLA, linestyle="--", linewidth=1.5, label=texto)


def guardar(fig, nombre, ajustar=True):
    """Guarda la figura en CARPETA_FIGURAS y la cierra. `ajustar` acomoda los márgenes."""
    os.makedirs(CARPETA_FIGURAS, exist_ok=True)
    ruta = os.path.join(CARPETA_FIGURAS, nombre)
    if ajustar:
        fig.tight_layout()
    fig.savefig(ruta)
    plt.close(fig)
    # Ojo: siempre dice docs/figuras/, aunque con --rapido se guarde en otra carpeta
    print(f"  figura: docs/figuras/{nombre}")
