"""
Figuras de la simulación para el informe y el panel. No se ejecuta solo: main.py llama a
dibujar_todo(...) con los resultados ya calculados.
Escribe en docs/figuras/ sim_evolucion_campana.png, sim_politicas.png y sim_capacidad.png.
"""
import os
import sys

import numpy as np

# estilo.py está en analisis/. Se agrega esa carpeta a las rutas donde Python busca módulos.
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "analisis"))
from estilo import COLORES, COLOR_SLA, guardar, linea_sla, plt  # noqa: E402

# Nombre del escenario actual. Tiene que ser igual al de ESCENARIOS en main.py.
ACTUAL = "Actual: reactivo 80 %, timeout 5 s"


def sombrear_oferta(ax, cfg):
    """Pinta de gris la franja de la oferta (en minutos)."""
    # axvspan dibuja un rectángulo vertical entre dos valores de x. zorder=0 lo deja al fondo.
    ax.axvspan(cfg.inicio_oferta_s / 60, cfg.fin_oferta_s / 60, color="#eeeeea", zorder=0)


def evolucion(campana, cfg, sla):
    """Minuto a minuto: no atendidas y nodos pagados, actual contra pre-escalado a 2 nodos."""
    elegidos = [ACTUAL, "Preescalado a 2 nodos"]   # 60 % y los timeouts salen igual al actual
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.8))
    for color, nombre in zip(COLORES, elegidos):
        s = campana[nombre]["series"]
        # +0.5: cada punto se dibuja en la mitad de su minuto (0.5, 1.5, 2.5, ...)
        minutos = np.arange(len(s["nodos"])) + 0.5
        ax1.plot(minutos, s["no_atendidas"] * 100, color=color, label=nombre, clip_on=False)
        ax2.step(minutos, s["nodos"], where="mid", color=color)
    linea_sla(ax1, sla["no_atendidas"] * 100, "SLA 10 %")
    ax1.set_ylabel("Peticiones no atendidas (%)")
    ax1.set_ylim(0, 100)
    ax2.set_ylabel("Nodos pagados (listos + arrancando)")
    ax2.set_ylim(0, None)
    for ax in (ax1, ax2):
        ax.set_xlabel("Tiempo (min) — franja gris: oferta")
        sombrear_oferta(ax, cfg)
    # Una sola leyenda abajo, tomada de ax1. rect deja libre el 8 % de abajo para ella.
    fig.legend(*ax1.get_legend_handles_labels(), loc="lower center", ncol=4, fontsize=10)
    fig.suptitle("Con la politica actual los nodos nuevos llegan cuando la oferta ya empezo")
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    guardar(fig, "sim_evolucion_campana.png", ajustar=False)


def politicas(campana, sla):
    """Abandono de compras y costo de cada configuración, en barras con su IC95."""
    nombres = list(campana)
    # Posición vertical de cada barra. [::-1] la invierte para que el primero quede arriba.
    y = np.arange(len(nombres))[::-1]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    ab = [campana[n]["oferta"]["abandono_compras"] for n in nombres]
    costo = [campana[n]["oferta"]["costo_nodos_min"] for n in nombres]
    # barh = barra horizontal, xerr = bigote de ± IC95
    ax1.barh(y, [a["media"] * 100 for a in ab], xerr=[a["ic95"] * 100 for a in ab],
             color=COLORES[0], height=0.6, capsize=3)
    ax1.axvline(sla["abandono_compras"] * 100, color=COLOR_SLA, linestyle="--",
                linewidth=1.5, label="SLA 5 %")
    ax1.set_yticks(y, nombres)
    ax1.set_xlabel("Abandono de compras en la oferta (%)")
    ax1.legend()
    ax2.barh(y, [c["media"] for c in costo], xerr=[c["ic95"] for c in costo],
             color=COLORES[1], height=0.6, capsize=3)
    ax2.set_xlabel("Costo de la hora (nodos·min)")
    fig.suptitle("Solo el pre-escalado cumple el SLA de abandono")
    guardar(fig, "sim_politicas.png")


def capacidad(datos, sla):
    """No atendidas contra demanda constante, para 1 y 2 nodos."""
    fig, ax = plt.subplots()
    for color, (nodos, r) in zip(COLORES, datos.items()):
        d = [f["demanda"] for f in r["filas"]]
        m = np.array([f["no_atendidas"]["media"] for f in r["filas"]]) * 100
        ic = np.array([f["no_atendidas"]["ic95"] for f in r["filas"]]) * 100
        ax.plot(d, m, marker="o", color=color, label=f"{nodos} nodo(s)")
        # Banda del IC95 (alpha = transparencia)
        ax.fill_between(d, m - ic, m + ic, color=color, alpha=0.15)
    linea_sla(ax, sla["no_atendidas"] * 100, "SLA 10 %")
    ax.set_xlabel("Demanda constante (compradores/s, mezcla 90/10)")
    ax.set_ylabel("Peticiones no atendidas (%)")
    ax.set_ylim(0, 102)
    ax.legend()
    ax.set_title("Capacidad maxima sostenible de 1 y 2 nodos")
    guardar(fig, "sim_capacidad.png")


def dibujar_todo(campana, capacidad_, cfg, sla):
    """Dibuja las tres figuras. `capacidad_` lleva guion bajo para no tapar capacidad()."""
    print("Figuras:")
    evolucion(campana, cfg, sla)
    politicas(campana, sla)
    capacidad(capacidad_, sla)
