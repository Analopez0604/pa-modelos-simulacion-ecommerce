# Resultados de la simulacion

Generado por `python simulacion/main.py`. Valores: media ± semiancho del IC 95 %.

## 1. Campana de 60 min (30 replicas) — KPIs durante la oferta

| Configuracion | Pregunta | p95 (s) | p99 (s) | No atendidas (%) | Abandono de compras (%) | Amplificacion (x) | CPU max (%) | Costo de la hora (nodos·min) | ¿Cumple SLA? |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Actual: reactivo 80 %, timeout 5 s | Base | 0.74 ± 0.00 | 0.78 ± 0.00 | 33.0 ± 3.5 | 20.1 ± 2.8 | 1.37 ± 0.04 | 100 ± 0 | 70.0 ± 0.0 | No |
| Reactivo 60 % | P1 | 0.74 ± 0.00 | 0.78 ± 0.00 | 33.0 ± 3.5 | 20.1 ± 2.8 | 1.37 ± 0.04 | 100 ± 0 | 82.6 ± 1.0 | No |
| Timeout 2 s | P2 | 0.74 ± 0.00 | 0.78 ± 0.00 | 33.0 ± 3.5 | 20.1 ± 2.8 | 1.37 ± 0.04 | 100 ± 0 | 70.0 ± 0.0 | No |
| Timeout 10 s | P2 | 0.74 ± 0.00 | 0.78 ± 0.00 | 33.0 ± 3.5 | 20.1 ± 2.8 | 1.37 ± 0.04 | 100 ± 0 | 70.0 ± 0.0 | No |
| Preescalado a 2 nodos | P3 | 0.03 ± 0.00 | 0.04 ± 0.00 | 0.0 ± 0.0 | 0.0 ± 0.0 | 1.06 ± 0.00 | 73 ± 0 | 80.5 ± 0.0 | Si |
| Preescalado a 3 nodos | P3 | 0.02 ± 0.00 | 0.03 ± 0.00 | 0.0 ± 0.0 | 0.0 ± 0.0 | 1.06 ± 0.00 | 49 ± 0 | 105.0 ± 0.0 | Si |

SLA: p95 < 2 s, no atendidas < 10 %, abandono de compras < 5 %.

## 2. Capacidad con demanda constante (10 replicas)

Mezcla 90/10, sin autoescalado. Cada celda: p95 (s) / no atendidas (%) / CPU (%).

| Demanda (compradores/s) | 1 nodo | 2 nodos |
| :---: | :---: | :---: |
| 20 | 0.02 / 0.0 / 18 | 0.02 / 0.0 / 9 |
| 40 | 0.03 / 0.0 / 36 | 0.02 / 0.0 / 18 |
| 60 | 0.04 / 0.0 / 53 | 0.02 / 0.0 / 27 |
| 80 | 0.11 / 0.0 / 83 | 0.02 / 0.0 / 41 |
| 90 | 0.54 / 0.9 / 96 | 0.02 / 1.0 / 49 |
| 100 | 0.78 / 10.6 / 100 | 0.02 / 3.4 / 55 |
| 120 | 0.82 / 36.5 / 100 | 0.03 / 6.9 / 66 |
| 140 | 0.83 / 51.2 / 100 | 0.04 / 9.4 / 77 |
| 160 | 0.83 / 60.1 / 100 | 0.08 / 11.6 / 88 |
| 180 | 0.83 / 65.9 / 100 | 0.59 / 12.8 / 98 |

- **1 nodo(s)**: cumple el SLA hasta **90 compradores/s**.
- **2 nodo(s)**: cumple el SLA hasta **140 compradores/s**.
