"""
Worker que procesa las órdenes pendientes, una a la vez (el "cajero" que cobra los pagos).
Más workers bajan la cola más rápido, agrandar la cola no. docker-compose.yml prende 2.
Anota cada orden en datos/ordenes_log_<contenedor>.csv. Se corre con `python -m app.worker`.
"""
import os
import random
import socket
import time
from datetime import datetime, timezone

from sqlalchemy import text, update

from . import config, telemetry
from .db import Orden, Sesion, preparar_base

# Cada cuánto revisa la cola cuando está vacía (segundos)
INTERVALO_SONDEO = float(os.getenv("INTERVALO_SONDEO", "0.2"))
# Trabajo de cada orden en segundos: imita la pasarela de pago externa, que no modelamos
TRABAJO_MIN = float(os.getenv("TRABAJO_ORDEN_MIN", "0.15"))
TRABAJO_MAX = float(os.getenv("TRABAJO_ORDEN_MAX", "0.40"))

# gethostname() es el nombre del contenedor, distinto en cada réplica: cada una tiene su CSV.
registro = telemetry.RegistroCSV(
    f"ordenes_log_{socket.gethostname()}.csv", telemetry.COLUMNAS_ORDENES,
    config.CARPETA_DATOS,
)


def tomar_una_orden(sesion):
    """Toma la orden pendiente más vieja (FIFO) y la marca procesando. None si no hay."""
    # FOR UPDATE bloquea la fila elegida. Con SKIP LOCKED el otro worker no espera: pasa a la
    # siguiente libre. Así dos workers nunca toman la misma orden.
    fila = sesion.execute(text("""
        SELECT id FROM ordenes
        WHERE estado = 'pendiente'
        ORDER BY encolada_en
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    """)).first()
    if fila is None:
        return None
    # El commit suelta el bloqueo. Ya no está "pendiente", así que el otro worker no la ve.
    orden = sesion.get(Orden, fila[0])
    orden.estado = "procesando"
    sesion.commit()
    return orden


def procesar(orden, sesion):
    """Espera el trabajo simulado, marca la orden procesada y la anota en el CSV."""
    inicio = time.perf_counter()
    time.sleep(random.uniform(TRABAJO_MIN, TRABAJO_MAX))
    orden.estado = "procesada"
    orden.procesada_en = datetime.now(timezone.utc)
    sesion.commit()
    servicio_ms = (time.perf_counter() - inicio) * 1000

    # Antigüedad = espera en cola + servicio. La base guarda UTC sin zona, por eso se la ponemos.
    encolada = orden.encolada_en.replace(tzinfo=timezone.utc)
    antiguedad_ms = (datetime.now(timezone.utc) - encolada).total_seconds() * 1000

    registro.escribir([
        telemetry.ahora_iso(), orden.id, f"{servicio_ms:.3f}", f"{antiguedad_ms:.3f}",
    ])


def recuperar_ordenes_huerfanas():
    """Si un worker se cayó a mitad de una orden, la devuelve a la cola."""
    # Ojo: cambia TODAS las órdenes en 'procesando', no solo las de este worker.
    with Sesion() as s:
        s.execute(
            update(Orden).where(Orden.estado == "procesando").values(estado="pendiente")
        )
        s.commit()


def main():
    """Repite para siempre: tomar una orden y procesarla."""
    # El catálogo lo carga la API. El worker solo necesita las tablas.
    preparar_base(sembrar=False)
    recuperar_ordenes_huerfanas()
    registro.abrir()
    print(f"worker arriba, sondeando cada {INTERVALO_SONDEO}s", flush=True)
    try:
        # Si la cola está vacía, espera INTERVALO_SONDEO y vuelve a mirar. Una sesión por vuelta.
        while True:
            with Sesion() as sesion:
                orden = tomar_una_orden(sesion)
                if orden is None:
                    time.sleep(INTERVALO_SONDEO)
                    continue
                procesar(orden, sesion)
    except KeyboardInterrupt:
        pass
    finally:
        registro.cerrar()


if __name__ == "__main__":
    main()
