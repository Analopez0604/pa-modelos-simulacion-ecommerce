"""Recursos de la tienda. Se leen de docker-compose.yml y, si falta uno, vale lo de abajo."""
import os

DB_URL = os.getenv(
    "DB_URL",
    "postgresql+psycopg2://ecommerce:ecommerce@db:5432/ecommerce",
)

# Pool de conexiones: pocas conexiones que se reutilizan en orden de llegada, sin extras.
POOL_SIZE = int(os.getenv("POOL_SIZE", "10"))
MAX_OVERFLOW = int(os.getenv("MAX_OVERFLOW", "0"))
POOL_TIMEOUT = float(os.getenv("POOL_TIMEOUT", "5"))  # segundos esperando conexión -> 503

# Hilos del punto de acceso: los endpoints no son async y FastAPI los corre en hilos.
HILOS_ACCESO = int(os.getenv("HILOS_ACCESO", "40"))

# Máximo de órdenes pendientes. Si se llena, pagos responde 503.
TOPE_COLA_ORDENES = int(os.getenv("TOPE_COLA_ORDENES", "500"))

# Carpeta de los CSV. En el contenedor /datos es la carpeta datos/ del proyecto.
CARPETA_DATOS = os.getenv("CARPETA_DATOS", "/datos")
