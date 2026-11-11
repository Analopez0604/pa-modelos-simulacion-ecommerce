"""
Telemetría: anota una fila por evento en un CSV, sin promedios, para poder ajustar distribuciones.
Cada proceso escribe en su propio archivo: la API en telemetry_log.csv y cada worker en
ordenes_log_<contenedor>.csv. Lo lee analisis/calibrar.py.
"""
import csv
import os
import threading
from datetime import datetime, timezone

# Columnas del CSV de la API (datos/telemetry_log.csv)
COLUMNAS_API = [
    "ts_iso",          # cuándo terminó la petición (UTC)
    "escenario",       # reposo | escalonada | pico (lo manda el gateway)
    "etapa",           # número de etapa de la curva de demanda
    "endpoint",        # ruta, por ejemplo /ordenes/{orden_id}
    "tipo",            # lectura | escritura_rapida | escritura_lenta | consulta_orden
    "latencia_ms",     # tiempo total dentro de la API
    "espera_pool_ms",  # parte de esa latencia que se fue esperando una conexión del pool
    "estado_http",     # código de respuesta: 200/201/202 bien, 4xx error, 503 rechazo
    "en_vuelo",        # peticiones que había dentro de la API cuando entró esta
]

# Columnas del CSV del worker de órdenes (datos/ordenes_log_<contenedor>.csv)
COLUMNAS_ORDENES = [
    "ts_iso",
    "orden_id",
    "servicio_ms",     # lo que tardó el worker procesando la orden
    "antiguedad_ms",   # tiempo total desde que la orden entró a la cola
]


def ahora_iso():
    """Hora actual en UTC como texto ISO con milisegundos."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RegistroCSV:
    """Un archivo CSV que se abre una vez y recibe una fila por evento."""

    def __init__(self, nombre_archivo, columnas, carpeta):
        self.ruta = os.path.join(carpeta, nombre_archivo)
        self.columnas = columnas
        self._archivo = None
        self._escritor = None
        # Varios hilos de la API escriben en el mismo archivo. El candado evita mezclar filas.
        self._candado = threading.Lock()

    def abrir(self):
        """Abre el archivo para agregar al final y pone encabezados si es nuevo."""
        os.makedirs(os.path.dirname(self.ruta), exist_ok=True)
        es_nuevo = not os.path.exists(self.ruta)
        self._archivo = open(self.ruta, "a", newline="", encoding="utf-8")
        self._escritor = csv.writer(self._archivo)
        if es_nuevo:
            self._escritor.writerow(self.columnas)
            self._archivo.flush()

    def escribir(self, fila):
        """Agrega una fila. Si el archivo no está abierto, no hace nada."""
        if self._escritor is None:
            return
        with self._candado:
            self._escritor.writerow(fila)
            # flush: lo pasa al disco de una vez, para no perder filas si el contenedor se apaga.
            self._archivo.flush()

    def cerrar(self):
        if self._archivo:
            self._archivo.close()
            self._archivo = None
            self._escritor = None
