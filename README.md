# Gemelo digital de una plataforma de comercio electrónico

Proyecto de Aula — **Modelos y Simulación**
Institución Universitaria Pascual Bravo · Período 2026-2
Docente: Jarol Molina

## Descripción

Simulador de eventos discretos que reproduce el comportamiento de una
plataforma de comercio electrónico durante campañas de descuento, con el fin de
evaluar configuraciones de autoescalado y de timeouts mediante métricas de
latencia, abandono de carrito y costo de infraestructura.

La pregunta que guía el proyecto es qué configuración debe adoptar el equipo de
plataforma antes de una campaña, y con qué evidencia.

## Arquitectura del sistema real mínimo

Tres servicios sobre una base de datos compartida:

- **Catálogo** — consultas de productos (lectura)
- **Carrito** — modificación de la selección (lectura y escritura)
- **Pagos** — confirmación de compra, que encola una orden procesada por un worker

Todas las peticiones entran por un único punto de acceso con hilos limitados y cola.
El límite físico del sistema es la utilización de CPU por nodo.

📄 Informe final: [`docs/INFORME_FINAL.md`](docs/INFORME_FINAL.md)

## Cómo correrlo

Requisitos: Docker y Python 3.11+.

```bash
# 0. Una sola vez: entorno de Python para la simulación y el análisis
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Levantar el sistema real (API + 2 workers + PostgreSQL)
docker compose up --build -d

# 2. Correr los experimentos de SimPy y regenerar las tablas del informe (~4–7 min)
python simulacion/main.py
```

## Panel visual (demostración en vivo)

```bash
streamlit run panel.py
```

Abre <http://localhost:8501> con un menú de 5 páginas:

| Página | Qué muestra |
| --- | --- |
| Inicio | El problema, las cifras clave, la recomendación, la oferta minuto a minuto y el diagrama del sistema |
| Sistema real en vivo | Estado de los contenedores, botón para lanzar carga y telemetría cada 2 s |
| Experimentar | Mueves umbral, timeout, pre-escalado y tamaño de la oferta, y lo comparas con la política actual |
| Correr simulación | Ejecuta `simulacion/main.py` (la rápida escribe en `docs/rapido/` y no toca el informe) |
| Resultados | La respuesta a P1–P4, el contraste M/M/c y la calibración |

Los números del panel salen de `docs/resultados_simulacion.json`, el archivo que escribe
`simulacion/main.py`. La carga que se lanza desde el panel queda marcada como `panel_*`, así no
se mezcla con las mediciones oficiales. El panel es para la demostración en vivo de 4 min.

## Otros comandos

| Qué | Comando |
| --- | --- |
| Simulación rápida (5 réplicas, ~1 min; escribe en `docs/rapido/`, no toca el informe) | `python simulacion/main.py --rapido` |
| Contraste M/M/c vs. SimPy | `python analisis/contraste_mmc.py` |
| Generar carga contra el sistema real | `python sistema_real/gateway.py --escenario escalonada` (también `pico` o `reposo`; ~5 min) |
| Recalibrar el gemelo con la telemetría | `python analisis/calibrar.py` |
| Ver la API | <http://localhost:8000/docs> y <http://localhost:8000/metricas> |
| Apagar el sistema real | `docker compose down` (con `-v` también borra la base de datos) |

## Estructura

```text
sistema_real/            Sistema real mínimo (lo que se mide)
  app/main.py            API: /catalogo, /carrito, /pagos/confirmar, /ordenes/{id}
  app/config.py          Capacidades del sistema (leídas de docker-compose.yml)
  app/db.py              Conexión a PostgreSQL y tablas
  app/worker.py          Worker que procesa la cola de órdenes
  app/telemetry.py       Una fila por petición en datos/telemetry_log.csv
  requirements.txt       Dependencias de la API
  gateway.py             Generador de carga (compradores con reintentos y abandono)
  Dockerfile
simulacion/              Gemelo digital en SimPy (lo que se simula)
  parametros.py          Todos los parámetros, con su origen
  parametros_calibrados.json   Lo que salió de la telemetría (lo escribe calibrar.py)
  modelo.py              Entidades, recursos y procesos
  politicas.py           Políticas de autoescalado (patrón Strategy)
  recolector.py          Métricas M1–M6 por fase
  main.py                Experimentos P1–P4 con 30 réplicas
  graficas.py            Figuras de la simulación (docs/figuras/sim_*.png)
analisis/
  calibrar.py            Telemetría -> parámetros (Lognormal, ley de utilización)
  contraste_mmc.py       Erlang C vs. SimPy en modo markoviano
  estilo.py              Estilo común de las figuras
datos/                   Telemetría real y resultados de las pruebas (CSV y JSON)
docs/                    Informe, tablas (.md y .json) y figuras (docs/figuras/)
  rapido/                Salida de --rapido (no se sube al repositorio)
panel.py                 Panel visual en Streamlit (tema en .streamlit/config.toml)
docker-compose.yml
requirements.txt         Dependencias para correr en tu máquina
```

## Flujo de trabajo

```text
sistema real ──gateway.py──> datos/*.csv ──calibrar.py──> parametros_calibrados.json
                                                                   │
                     docs/INFORME_FINAL.md <──tablas y figuras── simulacion/main.py
```

## Documento

[Informe final](docs/INFORME_FINAL.md)

## Integrantes

| Integrante | Rol |
| --- | --- |
| Ana Isabel López Durán | Modelo de simulación e integración del repositorio |
| Gerónimo Álvarez Saldarriaga | Sistema real: API, contenedores y mediciones |
| Brandon Steven Vidal Jaramillo | Generador de carga, calibración y contraste analítico |
