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

## 2. Contexto del Sistema y Subsanación de la Entrega 1

### 2.1 Dinámica del Sistema (Qué fluye, qué espera, qué se satura)

- **Entidades que fluyen:** *compradores* de dos tipos (90 % solo miran el catálogo, 10 %
  compran), *peticiones* HTTP (cada reintento cuenta como una petición nueva y le suma carga
  al sistema [1]) y *órdenes* (se
  crean al confirmar el pago y las procesa un worker).
- **Recursos y colas** (los mismos valores están en `docker-compose.yml` y `simulacion/parametros.py`):

  | Recurso | Capacidad | Disciplina | Si se llena |
  | :--- | :--- | :--- | :--- |
  | Punto de acceso de un nodo | 100 peticiones a la vez | — | responde 503 |
  | CPU de un nodo | 1 servidor (0,5 CPU del contenedor) | FIFO | se espera en cola |
  | Workers de órdenes | 2 | FIFO | crece la cola de órdenes |
  | Cola de órdenes | tope 500 | FIFO | el pago responde 503 |

  No modelamos el pool de 10 conexiones a la base de datos. En todas las pruebas, esperar una
  conexión tomó de 0,1 a 0,2 ms, así que nunca fue un límite.
- **Límite físico (C6):** la CPU del nodo. Cada petición gasta en promedio 7,4 ms de nodo, así
  que un nodo no puede atender más de 1 / 7,4 ms ≈ **135 peticiones/s**.
- **Comportamiento del comprador (C7):** cada comprador tiene una paciencia Weibull (media
  20 s) y decide una sola vez si es de los que reintentan (60 %). Si una petición le falla,
  reintenta hasta 3 veces y entre intentos espera 1, 2 y 4 s (±50 %). Abandona si no
  reintenta, si se le acaban los reintentos o si se le acaba la paciencia, incluso mientras
  espera su orden.

Este es el flujo del modelo con la **ruta de escritura corregida**, que fue una observación de
la Entrega 1:

```mermaid
flowchart LR
    A([Llega comprador]) --> B{Tipo}
    B -- "90 %" --> R[GET /catalogo]
    B -- "10 %" --> C[POST /carrito]
    C --> P[POST /pagos/confirmar]
    P -- "encola la orden" --> Q[(Cola de órdenes)] --> W[2 workers]
    P -- "responde 202" --> S[GET /ordenes/id<br/>cada 5 s]
    S -- "pendiente" --> S
    S -- "procesada" --> F([Compra exitosa])
    R & C & P & S -. "503 o timeout" .-> X{¿Reintenta y<br/>tiene paciencia?}
    X -- "sí, tras esperar" --> R
    X -- "no" --> Z([Abandona])
```

Cada petición (lectura, carrito, pago o consulta) pasa por un nodo: punto de acceso → cola
de CPU → servicio Lognormal → respuesta. Un reintento repite la misma petición que falló.

