# Informe Final: Gemelo digital de una plataforma de comercio electrónico bajo campañas de descuento

- **Curso:** Modelos y Simulación (2026-2), I.U. Pascual Bravo
- **Grupo:** Grupo 1
- **Repositorio:** <https://github.com/Analopez0604/pa-modelos-simulacion-ecommerce>
- **Comando de despliegue API:** `docker compose up --build -d`
- **Comando de ejecución SimPy:** `python simulacion/main.py` (genera [`resultados_simulacion.md`](resultados_simulacion.md) y las figuras)
- **Panel visual para la demostración en vivo:** `streamlit run panel.py`

| Integrante | Correo Institucional | Rol Ejecutado | Commits Principales (Módulos) |
| :--- | :--- | :--- | :--- |
| Ana Isabel López Durán | <ana.lopez710@pascualbravo.edu.co> | Modelo de simulación e integración del repositorio | `simulacion/` (parámetros, recolector, modelo, políticas, gráficas, experimentos), `panel.py`, configuración del repositorio e integración de los PR del equipo |
| Gerónimo Álvarez Saldarriaga | <geronimo.alvarez454@pascualbravo.edu.co> | Sistema real: API, contenedores y mediciones | `sistema_real/app/` (config, modelo de datos, endpoints, telemetría, worker), `docker-compose.yml`, `Dockerfile`, corridas y datos de `datos/` |
| Brandon Steven Vidal Jaramillo | <brandon.vidal578@pascualbravo.edu.co> | Generador de carga, calibración y contraste analítico | `sistema_real/gateway.py`, `analisis/calibrar.py`, `analisis/estilo.py`, `analisis/contraste_mmc.py`, figuras de calibración y de contraste |

---

## 1. Resumen Ejecutivo

Una tienda en línea lanza ofertas relámpago. Durante 10 minutos, la navegación se multiplica por 20 y las compras por 6. Montamos un sistema real mínimo (API + PostgreSQL + workers de órdenes en Docker), lo medimos bajo carga y con esas mediciones calibramos un modelo de eventos discretos en SimPy. Con 30 réplicas por configuración comparamos la política actual (autoescalado reactivo al 80 % de CPU, timeout de 5 s) contra bajar el umbral al 60 %, cambiar el timeout a 2 o 10 s y pre-escalar antes de la oferta. Con la política actual, durante la oferta no se atiende el 33 % de las peticiones y abandona el 20 % de los que iban a comprar. La causa es que un nodo nuevo tarda de 2 a 8 minutos en arrancar. Bajar el umbral o cambiar el timeout no mejora nada. Recomendamos pre-escalar a 2 nodos 10 minutos antes de la oferta: el abandono baja a 0 % y la CPU máxima queda en 73 %. A cambio, la hora cuesta 15 % más (80,5 vs. 70,0 nodos·min). Pagamos unos 10 minutos más de un nodo para no perder a uno de cada cinco compradores.
