"""
Contraste: el modelo SimPy en condiciones de M/M/c debe dar lo mismo que Erlang C.
Parte A: validación en "modo markoviano". Parte B: qué cambia con la dinámica real.
Escribe docs/contraste_mmc.md y docs/figuras/contraste_mmc.png.
    python analisis/contraste_mmc.py            # 30 réplicas por punto (~3 min)
    python analisis/contraste_mmc.py --rapido   # 5 réplicas
"""
import argparse
import math
import os
import sys
from dataclasses import replace
from multiprocessing import Pool

import numpy as np
from scipy import stats

# Pone simulacion/ de primera en las rutas de Python para importar los módulos del gemelo.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "simulacion"))

from modelo import simular                      # noqa: E402
from parametros import CALIBRACION, Config      # noqa: E402
from politicas import PoliticaGestion           # noqa: E402

# Los mismos números de la Entrega 1. M/M/c = llegadas Poisson, servicio Exponencial, c servidores.
MU = 25.0                  # pet/s por servidor (40 ms)
C = 6                      # servidores
LAMBDAS = [40, 105, 120, 135]   # tasas de llegada (pet/s): rho = 0,27, 0,70, 0,80 y 0,90
SEMILLA_BASE = 1000
CALENTAMIENTO_S = 300      # 5 min que se descartan porque el sistema arranca vacío
MEDIDO_S = 1800            # 30 min que se miden


def mmc(lam, mu, c):
    """Fórmulas de M/M/c (Erlang C). Devuelve {rho, Lq, Wq, W, L}, o None si rho >= 1."""
    # a = lambda / mu: servidores ocupados en promedio. rho = a / c: utilización de cada uno.
    a = lam / mu
    rho = a / c
    if rho >= 1:
        return None                                  # inestable: la cola no para de crecer
    # P0 = probabilidad de sistema vacío
    #    = 1 / [ Σ_{n=0}^{c-1} a^n / n!  +  a^c / (c! · (1 - rho)) ]
    suma = sum(a ** n / math.factorial(n) for n in range(c))
    termino = a ** c / (math.factorial(c) * (1 - rho))
    p0 = 1 / (suma + termino)
    # Erlang C: Pq = a^c / (c! · (1 - rho)) · P0 = probabilidad de que una llegada espere
    pq = termino * p0
    lq = pq * rho / (1 - rho)                        # Lq = Pq · rho / (1 - rho): cuántos esperan
    wq = lq / lam                                    # Wq = Lq / lambda (Little): espera en cola
    w = wq + 1 / mu                                  # W = Wq + 1/mu: espera + servicio
    # L = lambda · W (Ley de Little): cuántos hay en el sistema
    return {"rho": rho, "Lq": lq, "Wq": wq, "W": w, "L": lam * w}


def config_markoviana(lam):
    """Config del gemelo que se comporta como M/M/6: un nodo, servicio Exponencial de 40 ms."""
    # Solo lecturas a tasa lambda, 1 nodo con C = 6 "cajeros" de CPU y una sola fila.
    # Media de la Exponencial: e^(mu + sigma²/2) = e^(ln(1/MU) + 0) = 1/MU = 0,04 s
    return replace(
        Config(),
        lecturas_s=lam, compras_s=0,
        duracion_normal_s=CALENTAMIENTO_S + MEDIDO_S, duracion_oferta_s=0,
        duracion_posterior_s=0, calentamiento_s=CALENTAMIENTO_S,
        nodos_iniciales=1, nodos_min=1, cpu_por_nodo=C,
        tope_acceso=10 ** 9,                         # cola infinita
        timeout_cliente_s=None,                      # el cliente espera lo que sea
        servicio_exponencial=True,
        servicio_lognormal={"lectura": [math.log(1 / MU), 0.0]},
    )


def config_lognormal(lam):
    """Igual, pero el servicio es Lognormal con la sigma medida (media 40 ms)."""
    sigma = CALIBRACION["ajuste_latencia"]["lectura"]["sigma"]
    # Media Lognormal = e^(mu + sigma²/2). Para que sea 1/MU: mu = ln(1/MU) - sigma²/2
    mu_ln = math.log(1 / MU) - sigma ** 2 / 2
    return replace(config_markoviana(lam), servicio_exponencial=False,
                   servicio_lognormal={"lectura": [mu_ln, sigma]})


def config_reglas_reales(lam):
    """Lognormal + tope de acceso (100) + timeout de 5 s, con los reintentos y la paciencia."""
    return replace(config_lognormal(lam), tope_acceso=100, timeout_cliente_s=5.0)


def correr_una(trabajo):
    """Corre una réplica (en otro proceso) y mide rho, Lq, L, Wq, W, lambda y no atendidas."""
    clave, cfg, semilla = trabajo
    met = simular(cfg, PoliticaGestion(), semilla)
    # met.serie: una fila por segundo. Columnas 0 = tiempo, 1 = utilización, 5 = cola, 6 = sistema.
    # Lq y L se miden cada segundo y Wq y W por petición: así Little sí prueba algo.
    serie = np.array(met.serie)
    medir = serie[:, 0] >= cfg.calentamiento_s
    r = met.resumen()["normal"]
    return clave, {
        "rho": serie[medir, 1].mean(),
        "Lq": serie[medir, 5].mean(),
        "L": serie[medir, 6].mean(),
        "Wq": np.mean(met.esperas) if met.esperas else float("nan"),
        "W": r["latencia_media_s"],
        # lambda observada = peticiones completadas / 1800 s medidos
        "lambda_obs": len(met.latencias["normal"]) / MEDIDO_S,
        "no_atendidas": r["no_atendidas"],
    }


def correr(casos, replicas):
    """Corre cada caso en paralelo. Devuelve {clave: {métrica: (media, semiancho IC95)}}."""
    trabajos = [(clave, cfg, SEMILLA_BASE + r) for clave, cfg in casos for r in range(replicas)]
    with Pool() as pool:
        crudos = pool.map(correr_una, trabajos)
    resultado = {}
    for clave, _ in casos:
        filas = [m for k, m in crudos if k == clave]
        resultado[clave] = {}
        for k in filas[0]:
            v = np.array([f[k] for f in filas], dtype=float)
            # IC95: semiancho = t(0,975, n-1) · s / √n  (n = réplicas, s = desv. estándar)
            ic = stats.t.ppf(0.975, len(v) - 1) * v.std(ddof=1) / math.sqrt(len(v))
            resultado[clave][k] = (float(v.mean()), float(ic))
    return resultado


def ms(x):
    """Segundos a texto en ms con 1 decimal, o una raya si no hay dato."""
    return "—" if math.isnan(x) else f"{x * 1000:.1f}"


def main():
    """Corre las partes A y B, escribe docs/contraste_mmc.md y dibuja la figura."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapido", action="store_true", help="5 replicas")
    replicas = 5 if ap.parse_args().rapido else 30

    print(f"Parte A: M/M/c vs SimPy ({replicas} replicas por punto)...")
    a = correr([(lam, config_markoviana(lam)) for lam in LAMBDAS], replicas)
    # rho = lambda / (mu · c) = 135/150 = 0,9 (estable) y 180/150 = 1,2 (saturado)
    print("Parte B: dinamica real...")
    casos_b = [
        ("rho 0,9 · Exponencial (M/M/c)", config_markoviana(135)),
        ("rho 0,9 · Lognormal medida", config_lognormal(135)),
        ("rho 1,2 · Exponencial (M/M/c)", config_markoviana(180)),
        ("rho 1,2 · Lognormal + tope de acceso, timeout, reintentos y abandono",
         config_reglas_reales(180)),
    ]
    b = correr(casos_b, replicas)

    # ---- Informe en Markdown: tabla A (fórmula vs SimPy) ----
    md = ["# Contraste M/M/c vs. SimPy", "",
          f"Generado por `python analisis/contraste_mmc.py`. mu = {MU:.0f} pet/s, c = {C}. "
          f"{replicas} replicas por punto (semillas desde {SEMILLA_BASE}), "
          f"{MEDIDO_S // 60} min medidos tras {CALENTAMIENTO_S // 60} min de calentamiento. "
          "SimPy: media ± IC 95 %.", "",
          "## A. Validacion (modo markoviano)", "",
          "| lambda (pet/s) | Metrica | M/M/c (formula) | SimPy | Error relativo (%) |",
          "| :---: | :--- | :---: | :---: | :---: |"]
    for lam in LAMBDAS:
        teo = mmc(lam, MU, C)
        # (clave, nombre, escala, unidad): Wq y W se muestran en ms
        for k, nombre, esc, u in [("rho", "rho", 1, ""), ("Lq", "Lq", 1, ""),
                                  ("Wq", "Wq", 1000, " ms"), ("W", "W", 1000, " ms")]:
            m, ic = a[lam][k]
            error = abs(m - teo[k]) / teo[k] * 100
            md.append(f"| {lam} | {nombre} | {teo[k] * esc:.4f}{u} | "
                      f"{m * esc:.4f} ± {ic * esc:.4f}{u} | {error:.1f} |")
    # ---- Ley de Little: L = lambda · W. Si las columnas coinciden, la simulación es coherente.
    md += ["", "### Ley de Little con lo medido en SimPy", "",
           "| lambda | L (promedio en el tiempo) | lambda observada × W | Diferencia (%) |",
           "| :---: | :---: | :---: | :---: |"]
    for lam in LAMBDAS:
        L = a[lam]["L"][0]
        lw = a[lam]["lambda_obs"][0] * a[lam]["W"][0]
        md.append(f"| {lam} | {L:.4f} | {lw:.4f} | {abs(L - lw) / lw * 100:.2f} |")
    # ---- Tabla B: qué pasa con la dinámica real y qué diría M/M/c ----
    md += ["", "## B. Que cambia con la dinamica real", "",
           "| Caso | Wq (ms) | W (ms) | No atendidas (%) | M/M/c predice W |",
           "| :--- | :---: | :---: | :---: | :---: |"]
    for nombre, cfg in casos_b:
        r = b[nombre]
        teo = mmc(cfg.lecturas_s, MU, C)
        # Con rho >= 1 mmc devuelve None: la espera crece sin límite
        w_teo = f"{teo['W'] * 1000:.1f} ms" if teo else "∞ (inestable)"
        md.append(f"| {nombre} | {ms(r['Wq'][0])} | {ms(r['W'][0])} | "
                  f"{r['no_atendidas'][0] * 100:.1f} | {w_teo} |")
    texto = "\n".join(md) + "\n"
    with open(os.path.join(RAIZ, "docs", "contraste_mmc.md"), "w", encoding="utf-8") as f:
        f.write(texto)
    print("\n" + texto)

    # ---- Figura: Wq de la fórmula (curva) contra Wq de SimPy (puntos con IC95) ----
    # Se importa aquí para que los procesos hijos no carguen matplotlib.
    from estilo import COLORES, guardar, plt
    fig, ax = plt.subplots()
    # Curva teórica con lambda entre 30 y 145 pet/s (rho < 1)
    x = np.linspace(30, 145, 100)
    ax.plot(x, [mmc(v, MU, C)["Wq"] * 1000 for v in x], color=COLORES[0],
            label="M/M/c (formula de Erlang C)")
    # Puntos de SimPy con barra de error = IC95
    ax.errorbar(LAMBDAS, [a[v]["Wq"][0] * 1000 for v in LAMBDAS],
                yerr=[a[v]["Wq"][1] * 1000 for v in LAMBDAS], fmt="o", color=COLORES[1],
                capsize=4, label="SimPy modo markoviano (media ± IC 95 %)")
    ax.set_xlabel("Tasa de llegadas lambda (pet/s)")
    ax.set_ylabel("Espera media en cola Wq (ms)")
    ax.set_title("El modelo SimPy reproduce las formulas de M/M/c")
    ax.legend()
    guardar(fig, "contraste_mmc.png")


if __name__ == "__main__":
    main()
