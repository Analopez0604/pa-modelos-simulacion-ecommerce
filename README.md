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

## Estado del proyecto

| Entrega | Fecha | Estado |
|---|---|---|
| Entrega 1 — Planteamiento y modelo conceptual DES | 5 de noviembre | En curso |
| Entrega 2 — Telemetría, ajuste de distribuciones e inferencia | — | Pendiente |
| Entrega final — Componente multiagente e integración | 26 de noviembre | Pendiente |

El prototipo técnico (API en contenedor, telemetría y modelo SimPy) se incorporará
en las entregas siguientes.

## Documento

[`docs/PA_E1_Grupo2_GemeloEcommerce.pdf`](docs/)