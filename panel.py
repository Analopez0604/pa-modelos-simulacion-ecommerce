"""
Panel visual del proyecto en Streamlit. Es un extra para la demo, no lo pide la guía.
Páginas: Inicio, Sistema real en vivo, Experimentar, Correr simulación y Resultados.
No tiene modelo propio: usa las mismas funciones de simulacion/.
Abrir: streamlit run panel.py (desde la raíz del proyecto).
"""
import json
import os
import subprocess          # subprocess = correr otro programa (docker, gateway, main.py)
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import altair as alt
import numpy as np
import pandas as pd
import requests
import streamlit as st

# Agregamos simulacion/ a donde Python busca módulos, para importar el modelo.
RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(RAIZ, "simulacion"))

# noqa: E402 le dice al revisor de estilo que estos import tienen que ir aquí abajo.
from modelo import simular                                       # noqa: E402
from parametros import Config                                    # noqa: E402
from politicas import AutoescaladoReactivo, Preescalado          # noqa: E402

API = "http://localhost:8000"
DATOS = os.path.join(RAIZ, "datos")
DOCS = os.path.join(RAIZ, "docs")
FIGURAS = os.path.join(DOCS, "figuras")
CPU_NODO = 0.5            # la API tiene 0,5 CPU (docker-compose.yml)
AZUL, NARANJA, ROJO, GRIS = "#2a78d6", "#eb6834", "#e34948", "#eeeeea"
# Nombres exactos de las dos políticas en el JSON de resultados.
ACTUAL = "Actual: reactivo 80 %, timeout 5 s"
RECOMENDADA = "Preescalado a 2 nodos"

st.set_page_config(page_title="Gemelo digital e-commerce", page_icon="🛒", layout="wide")


# ===== Utilidades =====
def leer(ruta):
    """Devuelve el contenido de un archivo de texto."""
    with open(ruta, encoding="utf-8") as f:
        return f.read()


# st.cache_data guarda el resultado. Si se llama otra vez con los mismos argumentos,
# devuelve lo guardado y no vuelve a leer el archivo.
@st.cache_data
def cargar_resultados(ruta, modificado):
    """Lee el JSON de resultados. `modificado` hace que la caché lo relea si el archivo cambia."""
    return json.loads(leer(ruta))


def resultados(carpeta=DOCS):
    """Devuelve los resultados de la simulación o None si todavía no existen."""
    ruta = os.path.join(carpeta, "resultados_simulacion.json")
    if not os.path.exists(ruta):
        return None
    return cargar_resultados(ruta, os.path.getmtime(ruta))


def lanzar(comando, nombre):
    """Corre un comando en segundo plano y guarda lo que imprime en un archivo temporal."""
    log = os.path.join(tempfile.gettempdir(), f"panel_{nombre}.log")
    # Popen arranca el programa y sigue de largo, sin esperar a que termine.
    with open(log, "w") as f:
        proceso = subprocess.Popen(comando, cwd=RAIZ, stdout=f, stderr=subprocess.STDOUT)
    # st.session_state es una memoria que dura entre clics. Streamlit vuelve a correr todo
    # el script en cada clic, y sin esto perderíamos el proceso.
    st.session_state[nombre] = {"proceso": proceso, "log": log}


def estado_proceso(nombre):
    """Devuelve (estado, salida) de un proceso lanzado con lanzar()."""
    info = st.session_state.get(nombre)
    if not info:
        return None, ""
    salida = leer(info["log"])[-3000:]
    # poll() da None si sigue corriendo. Si ya terminó, da el código de salida (0 = todo bien).
    codigo = info["proceso"].poll()
    if codigo is None:
        return "corriendo", salida
    return ("terminado" if codigo == 0 else "fallo"), salida


def linea_sla(valor):
    """Capa de Altair: una línea roja punteada a la altura del SLA."""
    return alt.Chart(pd.DataFrame({"y": [valor]})).mark_rule(
        color=ROJO, strokeDash=[6, 4]).encode(y="y:Q")


def franja_oferta():
    """Capa de Altair: una franja gris entre los minutos 20 y 30 (la oferta)."""
    return alt.Chart(pd.DataFrame({"a": [20], "b": [30]})).mark_rect(
        color=GRIS, opacity=0.6).encode(x="a:Q", x2="b:Q")


def grafica_minutos(series, campo, titulo_y, sla=None, escala=1):
    """Gráfica minuto a minuto con una línea por política y la oferta en gris."""
    # Altair necesita una tabla larga: una fila por minuto y política. Sumamos 0,5 para que
    # cada punto quede en la mitad del minuto.
    filas = []
    for nombre, s in series.items():
        for minuto, valor in enumerate(s[campo]):
            filas.append({"min": minuto + 0.5, "valor": valor * escala, "politica": nombre})
    df = pd.DataFrame(filas)
    lineas = alt.Chart(df).mark_line(strokeWidth=2.5).encode(
        x=alt.X("min:Q", title="Minuto de la campaña (gris: oferta)"),
        y=alt.Y("valor:Q", title=titulo_y),
        color=alt.Color("politica:N", title=None, legend=alt.Legend(orient="bottom"),
                        scale=alt.Scale(range=[AZUL, NARANJA])),
        tooltip=["politica", "min", alt.Tooltip("valor:Q", format=".1f")])
    # En Altair el + pone una capa encima de otra.
    capa = franja_oferta() + lineas
    if sla is not None:
        capa = capa + linea_sla(sla)
    return capa.properties(height=300)


def figura(nombre, carpeta=FIGURAS):
    """Muestra una imagen .png de la carpeta, si existe."""
    ruta = os.path.join(carpeta, nombre)
    if os.path.exists(ruta):
        st.image(ruta, width="stretch")


# ===== Página 1: Inicio =====
# Diagrama de cómo fluye una petición, escrito en DOT (el lenguaje de Graphviz).
DIAGRAMA = """
digraph {
  rankdir=LR; bgcolor="transparent"; nodesep=0.35;
  node [shape=box, style="rounded,filled", fillcolor="#e8f1fc", color="#2a78d6",
        fontname="Helvetica", fontsize=11];
  edge [color="#8a8a8a", fontcolor="#8a8a8a", fontname="Helvetica", fontsize=9];
  comprador [label="Comprador\\n90 % mira · 10 % compra", shape=ellipse,
             fillcolor="#fde9df", color="#eb6834"];
  acceso [label="Punto de acceso\\n(tope 100)"];
  cpu [label="CPU del nodo\\n(cola FIFO)"];
  ordenes [label="Cola de órdenes"];
  workers [label="2 workers"];
  respuesta [label="Respuesta", shape=ellipse, fillcolor="#e3f5ec", color="#1baf7a"];
  comprador -> acceso -> cpu -> respuesta;
  cpu -> ordenes [label="pago"];
  ordenes -> workers;
  acceso -> comprador [label="503 / timeout:\\nreintenta o abandona", style=dashed, constraint=false];
}
"""


def pagina_inicio():
    """Página Inicio: el problema, las cifras clave, la recomendación y el diagrama."""
    # Título, pregunta y etiquetas con las tecnologías.
    st.title("🛒 Gemelo digital de una tienda en línea")
    st.markdown("#### ¿Cómo preparar la plataforma para una oferta relámpago sin perder ventas?")
    fila = st.container(horizontal=True)
    for texto, color in [("SimPy", "blue"), ("FastAPI + PostgreSQL", "violet"),
                         ("Docker", "gray"), ("30 réplicas por política", "green")]:
        fila.badge(texto, color=color)

    r = resultados()
    if r is None:
        st.info("Todavía no hay resultados. Ve a **Correr simulación** y ejecuta la completa.")
        return
    # a y m son las métricas de la política actual y de la recomendada durante la oferta.
    actual, mejor = r["campana"][ACTUAL], r["campana"][RECOMENDADA]
    a, m = actual["oferta"], mejor["oferta"]
    costo_extra = m["costo_nodos_min"]["media"] / a["costo_nodos_min"]["media"] - 1

    # Tarjetas de la oferta: abandono hoy, abandono con pre-escalado, costo extra y capacidad.
    st.write("")
    c = st.columns(4)
    c[0].metric("Compradores que abandonan hoy", f"{a['abandono_compras']['media']:.0%}",
                border=True, chart_data=[v * 100 for v in actual["series"]["no_atendidas"]],
                chart_type="area", help="Durante la oferta, con la política actual. "
                "La minigráfica son las peticiones no atendidas minuto a minuto.")
    # delta_color="inverse" pinta de verde cuando el número BAJA (menos abandono es mejor).
    c[1].metric("Con pre-escalado a 2 nodos", f"{m['abandono_compras']['media']:.0%}",
                f"{(m['abandono_compras']['media'] - a['abandono_compras']['media']) * 100:.0f} puntos",
                delta_color="inverse", border=True,
                chart_data=[v * 100 for v in mejor["series"]["no_atendidas"]], chart_type="area")
    c[2].metric("Costo extra de capacidad", f"{costo_extra:+.0%}",
                f"{m['costo_nodos_min']['media'] - a['costo_nodos_min']['media']:+.1f} nodos·min",
                delta_color="inverse", border=True,
                chart_data=mejor["series"]["nodos"], chart_type="line")
    # delta_color="off" deja gris el texto de abajo (es un dato, no un cambio).
    c[3].metric("Capacidad de un nodo", f"{r['capacidad']['1']} comp/s",
                f"2 nodos: {r['capacidad']['2']} comp/s", delta_color="off", border=True)

    st.success(f"**Recomendación:** pre-escalar a **2 nodos 10 minutos antes de la oferta**. "
               f"El abandono baja de {a['abandono_compras']['media']:.0%} a "
               f"{m['abandono_compras']['media']:.0%} pagando {costo_extra:.0%} más de "
               "capacidad en la hora.", icon=":material/check_circle:")

    # Gráfica minuto a minuto: política actual contra pre-escalado.
    with st.container(border=True):
        st.markdown("##### La oferta minuto a minuto")
        st.altair_chart(grafica_minutos(
            {"Política actual": actual["series"], "Pre-escalado a 2 nodos": mejor["series"]},
            "no_atendidas", "Peticiones no atendidas (%)", sla=10, escala=100), width="stretch")
        st.caption("Con la política actual el segundo nodo tarda de 2 a 8 min en arrancar: "
                   "mientras tanto se rechaza a un tercio de las peticiones.")

    # Diagrama y pasos del trabajo. Los números de los pasos 2 y 3 están escritos a mano.
    izquierda, derecha = st.columns([3, 2])
    with izquierda.container(border=True):
        st.markdown("##### Cómo fluye una petición")
        st.graphviz_chart(DIAGRAMA, width="stretch")
    with derecha.container(border=True):
        st.markdown("##### Cómo lo hicimos")
        st.markdown(
            "1. **Medimos** un sistema real en Docker con carga escalonada y de pico.\n"
            "2. **Calibramos**: tiempo de servicio Lognormal y la ley de utilización "
            "U = X · D (7,4 ms por petición).\n"
            "3. **Validamos** el modelo contra las fórmulas de M/M/c (error ≤ 2,4 %).\n"
            f"4. **Simulamos** {len(r['campana'])} políticas × {r['replicas']} réplicas "
            "de una campaña de 60 min.")


# ===== Página 2: Sistema real en vivo =====
def contenedores():
    """Devuelve [[servicio, estado], ...] de docker compose, o [] si Docker no responde."""
    # Pide una línea "servicio;estado" por contenedor y espera máximo 10 s.
    try:
        salida = subprocess.run(["docker", "compose", "ps", "--format", "{{.Service}};{{.State}}"],
                                cwd=RAIZ, capture_output=True, text=True, timeout=10).stdout
        return [linea.split(";") for linea in salida.strip().splitlines() if ";" in linea]
    except Exception:
        return []


# st.fragment(run_every="2s") hace que este pedazo de la página se actualice solo cada 2 s,
# sin volver a correr el resto de la página.
@st.fragment(run_every="2s")
def telemetria_en_vivo():
    """Tarjetas y gráficas del sistema real en los últimos 2 minutos."""
    ahora = datetime.now(timezone.utc)
    ventana = ahora - timedelta(minutes=2)
    try:
        m = requests.get(f"{API}/metricas", timeout=2).json()
    except Exception:
        m = None   # la API no responde (apagada o saturada)

    # Los dos CSV, solo con las filas de los últimos 2 minutos.
    tel = pd.read_csv(os.path.join(DATOS, "telemetry_log.csv"))
    tel["t"] = pd.to_datetime(tel.ts_iso)
    tel = tel[tel.t >= ventana]
    cpu = pd.read_csv(os.path.join(DATOS, "cpu_log.csv"))
    cpu["t"] = pd.to_datetime(cpu.ts_iso)
    cpu = cpu[cpu.t >= ventana]
    # docker stats da la CPU como % de UN núcleo. La API solo tiene medio núcleo, así que lo
    # pasamos a la escala del nodo: 100 % = el nodo está lleno.
    cpu["cpu_nodo"] = cpu.cpu_api / (100 * CPU_NODO) * 100
    cpu = cpu.dropna(subset=["cpu_nodo"])     # docker stats a veces no responde

    # Bloques de 2 s: peticiones/s y percentil 95 (p95) de la latencia.
    por_bloque = pd.DataFrame({"t": [], "peticiones": [], "p95": []})
    if not tel.empty:
        por_bloque = tel.set_index("t").resample("2s").agg(
            peticiones=("latencia_ms", "size"),
            p95=("latencia_ms", lambda x: x.quantile(0.95) if len(x) else np.nan)).reset_index()
        por_bloque["peticiones"] = por_bloque.peticiones / 2
    # Las peticiones de los últimos 10 s dan los números grandes de las tarjetas.
    ultimos = tel[tel.t >= ahora - timedelta(seconds=10)]

    # Tarjetas en vivo. Las minigráficas usan los últimos 30 puntos.
    c = st.columns(5)
    c[0].metric("Peticiones por segundo", f"{len(ultimos) / 10:.0f}", border=True,
                chart_data=por_bloque.peticiones.tolist()[-30:], chart_type="area")
    c[1].metric("p95 dentro de la API", f"{ultimos.latencia_ms.quantile(0.95):.0f} ms"
                if len(ultimos) else "—", border=True,
                chart_data=por_bloque.p95.fillna(0).tolist()[-30:])
    c[2].metric("CPU del nodo", f"{cpu.cpu_nodo.iloc[-1]:.0f} %" if len(cpu) else "—",
                border=True, chart_data=cpu.cpu_nodo.tolist()[-30:])
    c[3].metric("Errores (5xx)", f"{(ultimos.estado_http >= 500).mean():.1%}"
                if len(ultimos) else "—", border=True)
    c[4].metric("Órdenes pendientes", m["ordenes_pendientes"] if m else "—", border=True,
                help="Se lee de /metricas. Muestra — si la API no responde en 2 s.")

    if tel.empty:
        st.info("Sin peticiones en los últimos 2 minutos. Inicia una carga arriba.",
                icon=":material/info:")
        return
    # Gráficas en vivo: peticiones por segundo y p95 con la línea del SLA.
    g = st.columns(2)
    with g[0].container(border=True):
        st.markdown("**Peticiones atendidas por segundo**")
        st.altair_chart(alt.Chart(por_bloque).mark_area(
            color=AZUL, opacity=0.35, line={"color": AZUL}).encode(
            x=alt.X("t:T", title=None), y=alt.Y("peticiones:Q", title="pet/s"))
            .properties(height=220), width="stretch")
    with g[1].container(border=True):
        st.markdown("**p95 dentro de la API** (línea roja: SLA de 2 s)")
        st.altair_chart((alt.Chart(por_bloque).mark_line(color=NARANJA, strokeWidth=2.5)
                         .encode(x=alt.X("t:T", title=None), y=alt.Y("p95:Q", title="ms"))
                         + linea_sla(2000)).properties(height=220), width="stretch")


def pagina_sistema_real():
    """Página Sistema real: contenedores, generador de carga y telemetría en vivo."""
    st.title("📡 Sistema real en vivo")
    st.caption("API FastAPI + PostgreSQL + 2 workers de órdenes, en Docker (la API tiene 0,5 CPU).")

    # Estado de los contenedores y botón para levantar o apagar Docker.
    with st.container(border=True):
        izquierda, derecha = st.columns([3, 1], vertical_alignment="center")
        servicios = contenedores()
        fila = izquierda.container(horizontal=True)
        if servicios:
            for servicio, estado in servicios:
                arriba = estado == "running"
                fila.badge(servicio, color="green" if arriba else "red",
                           icon=":material/check_circle:" if arriba else ":material/error:")
        else:
            fila.badge("Contenedores apagados", color="red", icon=":material/power_off:")
        # st.button da True solo en la corrida en la que se hizo clic.
        # Aquí sí esperamos a que docker termine. Luego st.rerun() pinta el estado nuevo.
        if derecha.button("Apagar" if servicios else "Levantar", width="stretch",
                          icon=":material/power_settings_new:"):
            comando = ["docker", "compose", "down" if servicios else "up", *([] if servicios else ["-d"])]
            with st.spinner(" ".join(comando)):
                subprocess.run(comando, cwd=RAIZ)
            st.rerun()

    # Generar carga con sistema_real/gateway.py.
    with st.container(border=True):
        st.markdown("##### Generar carga")
        c = st.columns([2, 2, 1, 1], vertical_alignment="bottom")
        escenario = c[0].selectbox("Escenario", ["pico", "escalonada", "reposo"],
                                   help="pico: oferta de 180 lecturas/s · escalonada: sube hasta "
                                        "saturar · reposo: sin tráfico")
        duracion = c[1].slider("Segundos por etapa", 10, 60, 20)
        estado, salida = estado_proceso("carga")
        if c[2].button("Iniciar", type="primary", disabled=estado == "corriendo",
                       width="stretch", icon=":material/play_arrow:"):
            # La etiqueta "panel_..." separa estas corridas de las mediciones oficiales.
            lanzar([sys.executable, "sistema_real/gateway.py", "--escenario", escenario,
                    "--duracion", str(duracion), "--etiqueta", f"panel_{escenario}"], "carga")
            st.rerun()
        if c[3].button("Detener", disabled=estado != "corriendo", width="stretch",
                       icon=":material/stop:"):
            st.session_state["carga"]["proceso"].terminate()
            st.rerun()
        if estado:
            with st.expander(f"Salida del generador ({estado})"):
                st.code(salida or "...")

    st.markdown("##### Telemetría (se actualiza cada 2 s)")
    telemetria_en_vivo()


# ===== Página 3: Experimentar =====
# show_spinner=False porque la página ya tiene su propio spinner. Con la caché, repetir
# la misma combinación sale al instante.
@st.cache_data(show_spinner=False)
def simular_config(umbral, timeout, nodos_pre, multiplicador, replicas):
    """Simula `replicas` campañas con esa configuración y devuelve (promedio, serie)."""
    cfg = replace(Config(), timeout_cliente_s=float(timeout),
                  multiplicador_lecturas=float(multiplicador))
    oferta, costos, series = [], [], []
    # Una réplica por semilla (1000, 1001, ...). Con más de 1 nodo se usa Preescalado.
    for r in range(replicas):
        politica = (Preescalado(nodos_pre, umbral=umbral) if nodos_pre > 1
                    else AutoescaladoReactivo(umbral=umbral))
        met = simular(cfg, politica, 1000 + r)
        resumen = met.resumen()
        oferta.append(resumen["pico"])          # "pico" = métricas de los 10 min de oferta
        costos.append(resumen["costo_nodos_min"])
        series.append(met.series_por_minuto())
    # Promedio entre réplicas de cada métrica y de cada punto de las series.
    promedio = {k: float(np.mean([o[k] for o in oferta])) for k in oferta[0]}
    promedio["costo"] = float(np.mean(costos))
    serie = {k: (np.mean([s[k] for s in series], axis=0)).tolist() for k in series[0]}
    return promedio, serie


def pagina_experimentar():
    """Página Experimentar: simula tu configuración y la compara con la política actual."""
    st.title("🧪 Experimentar con el gemelo")
    st.caption("Diseña una política y compárala con la actual (reactivo 80 %, timeout 5 s, "
               "sin pre-escalado). Cada réplica es una campaña de 60 min y tarda unos segundos.")

    # st.form agrupa los controles: no se simula nada hasta presionar Simular.
    with st.form("experimento", border=True):
        c = st.columns(5)
        # El umbral pasa de % a fracción. Pre-escalado 1 = "No".
        umbral = c[0].slider("Umbral de CPU (%)", 50, 95, 80, 5) / 100
        timeout = c[1].slider("Timeout del cliente (s)", 1, 15, 5)
        nodos_pre = c[2].select_slider("Pre-escalado", options=[1, 2, 3, 4], value=2,
                                       format_func=lambda n: "No" if n == 1 else f"{n} nodos")
        multiplicador = c[3].slider("Tamaño de la oferta (lecturas ×)", 5, 30, 20)
        replicas = c[4].slider("Réplicas", 1, 10, 3)
        # Simula la política actual y la tuya con el mismo tamaño de oferta.
        if st.form_submit_button("Simular", type="primary", icon=":material/play_arrow:"):
            with st.spinner("Simulando la campaña..."):
                st.session_state["resultado_experimento"] = (
                    simular_config(0.80, 5, 1, multiplicador, replicas),
                    simular_config(umbral, timeout, nodos_pre, multiplicador, replicas))
    if "resultado_experimento" not in st.session_state:
        st.info("Mueve los controles y presiona **Simular**.", icon=":material/tune:")
        return
    (actual, serie_actual), (nueva, serie_nueva) = st.session_state["resultado_experimento"]

    # Aviso de SLA, con los mismos límites de simulacion/main.py.
    cumple = (nueva["no_atendidas"] < 0.10 and nueva["abandono_compras"] < 0.05
              and nueva["p95_s"] < 2)
    if cumple:
        st.success("**Tu configuración cumple el SLA durante la oferta.**",
                   icon=":material/verified:")
    else:
        st.error("**Tu configuración no cumple el SLA durante la oferta.**",
                 icon=":material/warning:")

    # Tarjetas: tu configuración y la diferencia frente a la actual.
    # Cada tupla: (título, clave en el resultado, factor para mostrarlo, unidad, ayuda).
    tarjetas = [("No atendidas", "no_atendidas", 100, "%", "SLA < 10 %"),
                ("Abandono de compras", "abandono_compras", 100, "%", "SLA < 5 %"),
                ("p95 de latencia", "p95_s", 1000, " ms", "SLA < 2000 ms"),
                ("CPU máxima", "util_max", 100, "%", "Límite C6: 85 %")]
    k = st.columns(5)
    # zip llena las 4 primeras columnas. La quinta es para el costo.
    for col, (titulo, clave, escala, unidad, ayuda) in zip(k, tarjetas):
        valor = nueva[clave] * escala
        col.metric(titulo, f"{valor:.1f}{unidad}", f"{valor - actual[clave] * escala:+.1f} vs. actual",
                   delta_color="inverse", border=True, help=ayuda)
    k[4].metric("Costo de la hora", f"{nueva['costo']:.1f} nodos·min",
                f"{nueva['costo'] - actual['costo']:+.1f} vs. actual",
                delta_color="inverse", border=True)

    # Gráficas minuto a minuto: no atendidas y nodos pagados.
    series = {"Política actual": serie_actual, "Tu configuración": serie_nueva}
    g = st.columns(2)
    with g[0].container(border=True):
        st.markdown("**Peticiones no atendidas (%)**")
        st.altair_chart(grafica_minutos(series, "no_atendidas", "%", sla=10, escala=100),
                        width="stretch")
    with g[1].container(border=True):
        st.markdown("**Nodos pagados (listos + arrancando)**")
        st.altair_chart(grafica_minutos(series, "nodos", "nodos"), width="stretch")


# ===== Página 4: Correr la simulación =====
# Se actualiza sola cada 2 s para ir mostrando la salida mientras main.py corre.
@st.fragment(run_every="2s")
def salida_simulacion():
    """Muestra la salida de simulacion/main.py y, si terminó bien, sus resultados."""
    estado, salida = estado_proceso("simulacion")
    if estado == "corriendo":
        with st.status("Simulando... (la salida aparece al terminar cada experimento)",
                       expanded=True):
            st.code(salida or "...")
    elif estado == "fallo":
        st.error("La simulación falló. Esta es su salida:")
        st.code(salida)
    elif estado == "terminado":
        # La rápida escribe en docs/rapido/ y la completa en docs/ (la del informe).
        carpeta = os.path.join(DOCS, "rapido") if st.session_state["modo_sim"] == "rapida" else DOCS
        st.success(f"Listo. Resultados en `{os.path.relpath(carpeta, RAIZ)}/`.",
                   icon=":material/check_circle:")
        a, b = st.columns(2)
        with a:
            figura("sim_evolucion_campana.png", os.path.join(carpeta, "figuras"))
        with b:
            figura("sim_politicas.png", os.path.join(carpeta, "figuras"))
        with st.expander("Tablas completas"):
            st.markdown(leer(os.path.join(carpeta, "resultados_simulacion.md")))


def pagina_simulacion():
    """Página Correr simulación: lanza simulacion/main.py (rápida o completa)."""
    st.title("▶️ Correr la simulación")
    st.caption("Ejecuta `simulacion/main.py`: 6 políticas en una campaña de 60 min (P1–P3) "
               "y la capacidad de 1 y 2 nodos (P4).")
    estado, _ = estado_proceso("simulacion")
    c = st.columns(2)
    # Corrida rápida. El modo se guarda para saber después en qué carpeta buscar.
    with c[0].container(border=True):
        st.markdown("##### Rápida · 5 réplicas · ~1–2 min")
        st.caption("Para la demostración. Escribe en `docs/rapido/` y no toca el informe.")
        if st.button("Correr rápida", disabled=estado == "corriendo", width="stretch",
                     icon=":material/bolt:"):
            lanzar([sys.executable, "simulacion/main.py", "--rapido"], "simulacion")
            st.session_state["modo_sim"] = "rapida"
            st.rerun()
    # Corrida completa, la que va al informe.
    with c[1].container(border=True):
        st.markdown("##### Completa · 30 réplicas · ~5 min")
        st.caption("Regenera las tablas y figuras oficiales del informe en `docs/`.")
        if st.button("Correr completa", type="primary", disabled=estado == "corriendo",
                     width="stretch", icon=":material/play_arrow:"):
            lanzar([sys.executable, "simulacion/main.py"], "simulacion")
            st.session_state["modo_sim"] = "completa"
            st.rerun()
    salida_simulacion()


# ===== Página 5: Resultados =====
def tabla_politicas(r, nombres):
    """Tabla con los indicadores de esas políticas durante la oferta."""
    filas = []
    for n in nombres:
        o = r["campana"][n]["oferta"]
        filas.append({"Política": n,
                      "Abandono de compras (%)": o["abandono_compras"]["media"] * 100,
                      "No atendidas (%)": o["no_atendidas"]["media"] * 100,
                      "p95 (s)": o["p95_s"]["media"],
                      "Costo (nodos·min)": o["costo_nodos_min"]["media"],
                      "Cumple SLA": "✅" if r["campana"][n]["cumple_sla"] else "❌"})
    # Las columnas de % se ven como barras de 0 a 100.
    barra = st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f %%")
    st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch",
                 column_config={"Abandono de compras (%)": barra, "No atendidas (%)": barra,
                                "p95 (s)": st.column_config.NumberColumn(format="%.2f"),
                                "Costo (nodos·min)": st.column_config.NumberColumn(format="%.1f")})


def pagina_resultados():
    """Página Resultados: una pestaña por pregunta, el contraste M/M/c y la calibración."""
    st.title("📊 Resultados")
    r = resultados()
    if r is None:
        st.info("Todavía no hay resultados. Ve a **Correr simulación** y ejecuta la completa.")
        return
    st.caption(f"Media de {r['replicas']} réplicas durante la oferta. "
               "SLA: p95 < 2 s, no atendidas < 10 %, abandono de compras < 5 %.")
    # Costo medio de cada política, para los textos de costo.
    costo = {n: c["oferta"]["costo_nodos_min"]["media"] for n, c in r["campana"].items()}

    # Lo que se dibuja dentro de `with t[i]` va en la pestaña i. Las conclusiones están
    # escritas a mano. Solo los porcentajes de costo y la capacidad salen del JSON.
    t = st.tabs(["P1 · Umbral", "P2 · Timeout", "P3 · Pre-escalado", "P4 · Capacidad",
                 "M/M/c vs. SimPy", "Calibración"])
    with t[0]:
        st.info(f"Bajar el umbral de 80 % a 60 % **no cambia nada durante la oferta**: el daño "
                f"ocurre mientras arranca el segundo nodo. Solo sube el costo "
                f"({costo['Reactivo 60 %'] / costo[ACTUAL] - 1:+.0%}).", icon=":material/lightbulb:")
        tabla_politicas(r, [ACTUAL, "Reactivo 60 %"])
    with t[1]:
        st.info("Con 2, 5 o 10 s el resultado es el mismo: con el tope de 100 peticiones y "
                "~7,4 ms cada una, nadie espera más de ~0,74 s. El nodo rechaza antes de que "
                "el timeout actúe.", icon=":material/lightbulb:")
        tabla_politicas(r, ["Timeout 2 s", ACTUAL, "Timeout 10 s"])
    with t[2]:
        st.info(f"Pedir **2 nodos 10 min antes** elimina el abandono por "
                f"{costo[RECOMENDADA] / costo[ACTUAL] - 1:+.0%} de costo. Con 3 nodos también "
                "se cumple, pero cuesta más sin mejorar nada.", icon=":material/lightbulb:")
        tabla_politicas(r, [ACTUAL, RECOMENDADA, "Preescalado a 3 nodos"])
        figura("sim_evolucion_campana.png")
    with t[3]:
        st.info(f"Un nodo aguanta hasta **{r['capacidad']['1']} compradores/s** y dos nodos "
                f"hasta **{r['capacidad']['2']}**. Con 2 nodos el límite pasa a ser la cola "
                "de órdenes (2 workers ≈ 7 órdenes/s).", icon=":material/lightbulb:")
        figura("sim_capacidad.png")
    with t[4]:
        st.info("En modo M/M/c el modelo reproduce las fórmulas de la Entrega 1 con error "
                "≤ 2,4 % y cumple la Ley de Little. Con la dinámica real (tope, timeout, "
                "reintentos) el resultado ya no lo puede predecir la fórmula.",
                icon=":material/lightbulb:")
        figura("contraste_mmc.png")
        with st.expander("Tablas del contraste"):
            st.markdown(leer(os.path.join(DOCS, "contraste_mmc.md")))
    with t[5]:
        st.info("El tiempo de servicio se ajustó con una Lognormal (mucho mejor que la "
                "Exponencial) y la ley de utilización U = X · D dio 7,4 ms por petición.",
                icon=":material/lightbulb:")
        a, b = st.columns(2)
        with a:
            figura("calibracion_ajuste_lectura.png")
        with b:
            figura("calibracion_escalonada_real.png")


# ===== Navegación =====
st.sidebar.markdown("### 🛒 Gemelo digital")
st.sidebar.caption("Proyecto de Aula · Modelos y Simulación\nI.U. Pascual Bravo · Grupo 1")
# st.navigation arma el menú de páginas. Cada st.Page une una función de arriba con su
# título. .run() corre la página que eligió el usuario.
st.navigation([
    st.Page(pagina_inicio, title="Inicio", icon=":material/home:", default=True),
    st.Page(pagina_sistema_real, title="Sistema real en vivo", icon=":material/monitoring:"),
    st.Page(pagina_experimentar, title="Experimentar", icon=":material/science:"),
    st.Page(pagina_simulacion, title="Correr simulación", icon=":material/play_circle:"),
    st.Page(pagina_resultados, title="Resultados", icon=":material/insights:"),
]).run()
