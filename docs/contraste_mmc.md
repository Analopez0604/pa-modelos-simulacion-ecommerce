# Contraste M/M/c vs. SimPy

Generado por `python analisis/contraste_mmc.py`. mu = 25 pet/s, c = 6. 30 replicas por punto (semillas desde 1000), 30 min medidos tras 5 min de calentamiento. SimPy: media ± IC 95 %.

## A. Validacion (modo markoviano)

| lambda (pet/s) | Metrica | M/M/c (formula) | SimPy | Error relativo (%) |
| :---: | :--- | :---: | :---: | :---: |
| 40 | rho | 0.2667 | 0.2665 ± 0.0006 | 0.1 |
| 40 | Lq | 0.0023 | 0.0027 ± 0.0006 | 15.1 |
| 40 | Wq | 0.0583 ms | 0.0606 ± 0.0027 ms | 4.0 |
| 40 | W | 40.0583 ms | 40.0422 ± 0.0578 ms | 0.0 |
| 105 | rho | 0.7000 | 0.7011 ± 0.0008 | 0.2 |
| 105 | Lq | 0.7839 | 0.8026 ± 0.0152 | 2.4 |
| 105 | Wq | 7.4662 ms | 7.5383 ± 0.0882 ms | 1.0 |
| 105 | W | 47.4662 ms | 47.5769 ± 0.1112 ms | 0.2 |
| 120 | rho | 0.8000 | 0.8003 ± 0.0007 | 0.0 |
| 120 | Lq | 2.0711 | 2.0713 ± 0.0360 | 0.0 |
| 120 | Wq | 17.2591 ms | 17.4243 ± 0.2007 ms | 1.0 |
| 120 | W | 57.2591 ms | 57.4367 ± 0.2085 ms | 0.3 |
| 135 | rho | 0.9000 | 0.8992 ± 0.0008 | 0.1 |
| 135 | Lq | 6.6611 | 6.6496 ± 0.1180 | 0.2 |
| 135 | Wq | 49.3417 ms | 49.2560 ± 0.8655 ms | 0.2 |
| 135 | W | 89.3417 ms | 89.2636 ± 0.8742 ms | 0.1 |

### Ley de Little con lo medido en SimPy

| lambda | L (promedio en el tiempo) | lambda observada × W | Diferencia (%) |
| :---: | :---: | :---: | :---: |
| 40 | 1.6031 | 1.6013 | 0.11 |
| 105 | 5.0113 | 4.9981 | 0.26 |
| 120 | 6.8633 | 6.8944 | 0.45 |
| 135 | 12.0571 | 12.0603 | 0.03 |

## B. Que cambia con la dinamica real

| Caso | Wq (ms) | W (ms) | No atendidas (%) | M/M/c predice W |
| :--- | :---: | :---: | :---: | :---: |
| rho 0,9 · Exponencial (M/M/c) | 49.3 | 89.3 | 0.0 | 89.3 ms |
| rho 0,9 · Lognormal medida | 33.0 | 73.0 | 0.0 | 89.3 ms |
| rho 1,2 · Exponencial (M/M/c) | 205306.5 | 205343.1 | 0.0 | ∞ (inestable) |
| rho 1,2 · Lognormal + tope de acceso, timeout, reintentos y abandono | 617.9 | 657.9 | 37.9 | ∞ (inestable) |
