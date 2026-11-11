"""
Programa principal: corre el modelo SimPy muchas veces y responde P1 a P4 con media e IC95.
Experimento 1: hora de campaña con cada política (P1 a P3). Experimento 2: capacidad (P4).
Escribe docs/resultados_simulacion.md, .json (lo lee panel.py) y docs/figuras/sim_*.png.
    python simulacion/main.py            # 30 réplicas, 10 en P4 (~5 min)
    python simulacion/main.py --rapido   # 5 réplicas (~1 min), escribe en docs/rapido/
"""
import argparse
import json
import math
import os
import time
from dataclasses import replace
from multiprocessing import Pool

import numpy as np
from scipy import stats

from modelo import simular
from parametros import Config
from politicas import AutoescaladoReactivo, PoliticaGestion, Preescalado

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# La réplica r usa la semilla 1000 + r. Todos los escenarios usan las mismas semillas, así
# se comparan en las mismas condiciones y los resultados se pueden repetir.
SEMILLA_BASE = 1000

# SLA en la oferta: p95 < 2 s (el 95 % de las peticiones tarda menos), no atendidas
# (503 o timeout) < 10 % y abandono de compras < 5 %.
SLA = {"p95_s": 2.0, "no_atendidas": 0.10, "abandono_compras": 0.05}

# Experimento 1: (nombre, pregunta, política, cambios a Config). ("reactivo", u) agrega
# nodos cuando la CPU pasa u. ("preescalado", n) pide n nodos 10 min antes de la oferta.
ESCENARIOS = [
    ("Actual: reactivo 80 %, timeout 5 s", "Base", ("reactivo", 0.80), {}),
    ("Reactivo 60 %", "P1", ("reactivo", 0.60), {}),
    ("Timeout 2 s", "P2", ("reactivo", 0.80), {"timeout_cliente_s": 2.0}),
    ("Timeout 10 s", "P2", ("reactivo", 0.80), {"timeout_cliente_s": 10.0}),
    ("Preescalado a 2 nodos", "P3", ("preescalado", 2), {}),
    ("Preescalado a 3 nodos", "P3", ("preescalado", 3), {}),
]

# Experimento 2: compradores/s (90 % lecturas, 10 % compras), con 1 y 2 nodos fijos.
DEMANDAS = [20, 40, 60, 80, 90, 100, 120, 140, 160, 180]
NODOS_P4 = [1, 2]


def crear_politica(spec):
    """Convierte ("reactivo", 0.8) o ("preescalado", 2) en la política. Otro tipo: nodos fijos."""
    tipo, valor = spec
    if tipo == "reactivo":
        return AutoescaladoReactivo(umbral=valor)
    if tipo == "preescalado":
        return Preescalado(nodos_objetivo=valor)
    return PoliticaGestion()


def correr_una(trabajo):
    """Corre una réplica en otro proceso. Devuelve (clave, métricas por fase, series)."""
    clave, cfg, spec, semilla = trabajo
    met = simular(cfg, crear_politica(spec), semilla)
    return clave, met.resumen(), met.series_por_minuto()


def correr_todas(trabajos):
    """Corre todas las réplicas en paralelo, un proceso por núcleo, en el mismo orden."""
    with Pool() as pool:
        return pool.map(correr_una, trabajos)


def media_ic(valores):
    """Media y semiancho del IC95 de una lista con un valor por réplica."""
    v = np.array(valores, dtype=float)
    n = len(v)
    # IC95 = media ± t(0,975, n-1) · s / √n   (n = réplicas, s = desviación estándar)
    # t(0,975, 29) ≈ 2,045 con 30 réplicas. ddof=1 hace que std divida por n-1.
    semiancho = stats.t.ppf(0.975, n - 1) * v.std(ddof=1) / math.sqrt(n)
    return {"media": float(v.mean()), "ic95": float(semiancho)}


def experimento_campana(replicas):
    """Experimento 1: la hora de campaña (20 min normal, 10 de oferta, 30 después)."""
    # replace(Config(), **cambios) copia la configuración y cambia solo esos campos.
    trabajos = [(nombre, replace(Config(), **cambios), spec, SEMILLA_BASE + r)
                for nombre, _, spec, cambios in ESCENARIOS for r in range(replicas)]
    crudos = correr_todas(trabajos)

    resultados = {}
    for nombre, pregunta, _, _ in ESCENARIOS:
        corridas = [(res, serie) for clave, res, serie in crudos if clave == nombre]
        # KPIs de la fase "pico" (la oferta): media e IC95 entre réplicas
        oferta = {k: media_ic([res["pico"][k] for res, _ in corridas])
                  for k in corridas[0][0]["pico"]}
        # El costo es de la hora completa, no solo de la oferta
        oferta["costo_nodos_min"] = media_ic([res["costo_nodos_min"] for res, _ in corridas])
        # Series minuto a minuto, promediadas entre réplicas (para la gráfica)
        series = {k: np.mean([s[k] for _, s in corridas], axis=0)
                  for k in ("no_atendidas", "nodos")}
        # Cumple si la media de las tres métricas queda bajo su límite
        cumple = all(oferta[k]["media"] < v for k, v in SLA.items())
        resultados[nombre] = {"pregunta": pregunta, "oferta": oferta,
                              "series": series, "cumple_sla": cumple}
    return resultados


def experimento_capacidad(replicas):
    """Experimento 2 (P4): cuántos compradores/s aguantan 1 y 2 nodos fijos con el SLA."""
    trabajos = []
    for nodos in NODOS_P4:
        for d in DEMANDAS:
            # 6 min de demanda constante, sin oferta. El primer minuto no se mide porque
            # arranca vacío. Con nodos_min = nodos_iniciales los nodos no cambian.
            cfg = replace(Config(), lecturas_s=0.9 * d, compras_s=0.1 * d,
                          duracion_normal_s=360, duracion_oferta_s=0,
                          duracion_posterior_s=0, calentamiento_s=60,
                          nodos_iniciales=nodos, nodos_min=nodos)
            for r in range(replicas):
                trabajos.append(((nodos, d), cfg, ("fija", None), SEMILLA_BASE + r))
    crudos = correr_todas(trabajos)

    resultados = {}
    for nodos in NODOS_P4:
        filas = []
        for d in DEMANDAS:
            # Solo hay fase "normal" (no hay oferta)
            res = [r["normal"] for clave, r, _ in crudos if clave == (nodos, d)]
            fila = {"demanda": d}
            for k in ("p95_s", "no_atendidas", "util_media"):
                fila[k] = media_ic([x[k] for x in res])
            # P4 mira solo la API (p95 y no atendidas), no el abandono de compras.
            fila["cumple_sla"] = (fila["p95_s"]["media"] < SLA["p95_s"]
                                  and fila["no_atendidas"]["media"] < SLA["no_atendidas"])
            filas.append(fila)
        # Capacidad = la mayor demanda que cumple el SLA (0 si ninguna)
        sostenible = [f["demanda"] for f in filas if f["cumple_sla"]]
        resultados[nodos] = {"filas": filas, "capacidad": max(sostenible, default=0)}
    return resultados


def fmt(m, escala=1.0, dec=2):
    """Texto "media ± semiancho". `escala` = 100 pasa de fracción a %, `dec` = decimales."""
    return f"{m['media'] * escala:.{dec}f} ± {m['ic95'] * escala:.{dec}f}"


def tablas(campana, capacidad, replicas, replicas_p4):
    """Arma el Markdown de docs/resultados_simulacion.md con las dos tablas."""
    t = [
        "# Resultados de la simulacion",
        "",
        "Generado por `python simulacion/main.py`. Valores: media ± semiancho del IC 95 %.",
        "",
        f"## 1. Campana de 60 min ({replicas} replicas) — KPIs durante la oferta",
        "",
        "| Configuracion | Pregunta | p95 (s) | p99 (s) | No atendidas (%) | Abandono de compras (%)"
        " | Amplificacion (x) | CPU max (%) | Costo de la hora (nodos·min) | ¿Cumple SLA? |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for nombre, r in campana.items():
        o = r["oferta"]
        t.append(f"| {nombre} | {r['pregunta']} | {fmt(o['p95_s'])} | {fmt(o['p99_s'])} "
                 f"| {fmt(o['no_atendidas'], 100, 1)} | {fmt(o['abandono_compras'], 100, 1)} "
                 f"| {fmt(o['amplificacion'])} | {fmt(o['util_max'], 100, 0)} "
                 f"| {fmt(o['costo_nodos_min'], 1, 1)} | {'Si' if r['cumple_sla'] else 'No'} |")
    # Tabla 2: una fila por demanda y una columna por cada número de nodos
    t += ["", "SLA: p95 < 2 s, no atendidas < 10 %, abandono de compras < 5 %.", "",
          f"## 2. Capacidad con demanda constante ({replicas_p4} replicas)", "",
          "Mezcla 90/10, sin autoescalado. Cada celda: p95 (s) / no atendidas (%) / CPU (%).", "",
          "| Demanda (compradores/s) | 1 nodo | 2 nodos |",
          "| :---: | :---: | :---: |"]
    for i, d in enumerate(DEMANDAS):
        celdas = [f"{f['p95_s']['media']:.2f} / {f['no_atendidas']['media'] * 100:.1f} / "
                  f"{f['util_media']['media'] * 100:.0f}"
                  for f in (capacidad[n]["filas"][i] for n in NODOS_P4)]
        t.append(f"| {d} | " + " | ".join(celdas) + " |")
    t.append("")
    for n in NODOS_P4:
        t.append(f"- **{n} nodo(s)**: cumple el SLA hasta **{capacidad[n]['capacidad']} "
                 "compradores/s**.")
    return "\n".join(t) + "\n"


def guardar_json(campana, capacidad, replicas, carpeta):
    """Guarda los resultados en <carpeta>/resultados_simulacion.json para panel.py."""
    # JSON no entiende numpy (por eso .tolist()) y solo acepta claves de texto (str(n)).
    datos = {
        "replicas": replicas,
        "campana": {nombre: {"pregunta": r["pregunta"], "cumple_sla": r["cumple_sla"],
                             "oferta": r["oferta"],
                             "series": {k: v.tolist() for k, v in r["series"].items()}}
                    for nombre, r in campana.items()},
        "capacidad": {str(n): r["capacidad"] for n, r in capacidad.items()},
    }
    with open(os.path.join(carpeta, "resultados_simulacion.json"), "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=1, ensure_ascii=False)


def main():
    """Lee los argumentos, corre los dos experimentos y guarda tablas, JSON y figuras."""
    ap = argparse.ArgumentParser(description="Experimentos del gemelo digital")
    ap.add_argument("--rapido", action="store_true", help="5 replicas (demostracion)")
    args = ap.parse_args()
    replicas = 5 if args.rapido else 30
    replicas_p4 = 5 if args.rapido else 10

    import graficas   # aquí y no arriba: los procesos hijos no necesitan matplotlib
    import estilo     # (graficas agrega analisis/ a la ruta, ahí está estilo)

    # --rapido escribe en docs/rapido/ para no pisar los resultados del informe.
    carpeta = os.path.join(RAIZ, "docs", "rapido" if args.rapido else "")
    estilo.CARPETA_FIGURAS = os.path.join(carpeta, "figuras")
    os.makedirs(carpeta, exist_ok=True)

    inicio = time.time()
    print(f"1/2 Campana de 60 min: {len(ESCENARIOS)} configuraciones x {replicas} replicas...")
    campana = experimento_campana(replicas)
    print(f"2/2 Capacidad de 1 y 2 nodos: {len(DEMANDAS)} demandas x {replicas_p4} replicas...")
    capacidad = experimento_capacidad(replicas_p4)

    # Config() se pasa solo para saber dónde empieza y dónde termina la oferta.
    md = tablas(campana, capacidad, replicas, replicas_p4)
    with open(os.path.join(carpeta, "resultados_simulacion.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print("\n" + md)
    guardar_json(campana, capacidad, replicas, carpeta)
    graficas.dibujar_todo(campana, capacidad, Config(), SLA)
    print(f"\nListo en {time.time() - inicio:.0f} s.")


# Obligatorio con multiprocessing: los procesos hijos importan este archivo y no deben
# volver a lanzar los experimentos.
if __name__ == "__main__":
    main()
