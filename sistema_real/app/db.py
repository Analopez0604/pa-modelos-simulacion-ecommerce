"""
Conexión a PostgreSQL (con SQLAlchemy) y las tablas de la tienda.
El pool de conexiones lo configuramos a mano porque es uno de los recursos del modelo.
"""
import time

from sqlalchemy import (
    create_engine, select, Column, Integer, String, Numeric, DateTime, func,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from . import config

# Motor de los endpoints. Su pool es el recurso que medimos: POOL_SIZE conexiones, ninguna
# extra y como máximo POOL_TIMEOUT segundos esperando una libre.
engine = create_engine(
    config.DB_URL,
    pool_size=config.POOL_SIZE,
    max_overflow=config.MAX_OVERFLOW,
    pool_timeout=config.POOL_TIMEOUT,
)

# Motor de monitoreo sin pool (NullPool), para que /metricas no le quite conexiones al que medimos.
engine_monitor = create_engine(config.DB_URL, poolclass=NullPool)

# expire_on_commit=False: se pueden leer los datos de un objeto después de guardar.
Sesion = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


class Producto(Base):
    """Tabla productos: el catálogo de la tienda."""
    __tablename__ = "productos"
    id = Column(Integer, primary_key=True)
    nombre = Column(String(120), nullable=False)
    precio = Column(Numeric(10, 2), nullable=False)


class ItemCarrito(Base):
    """Tabla items_carrito: un producto que un comprador puso en su carrito."""
    __tablename__ = "items_carrito"
    id = Column(Integer, primary_key=True)
    comprador_id = Column(String(64), nullable=False, index=True)
    producto_id = Column(Integer, nullable=False)
    cantidad = Column(Integer, nullable=False, default=1)
    creado_en = Column(DateTime, server_default=func.now())


class Orden(Base):
    """Tabla ordenes: las compras confirmadas. Es la cola que vacían los workers."""
    __tablename__ = "ordenes"
    id = Column(Integer, primary_key=True)
    comprador_id = Column(String(64), nullable=False, index=True)
    total = Column(Numeric(10, 2), nullable=False)
    # pendiente -> procesando -> procesada
    estado = Column(String(20), nullable=False, default="pendiente", index=True)
    encolada_en = Column(DateTime, server_default=func.now())  # cuándo entró a la cola
    procesada_en = Column(DateTime, nullable=True)             # la llena el worker al terminar


def preparar_base(sembrar=True, intentos=30):
    """Crea las tablas (y el catálogo si sembrar=True). Reintenta porque la base tarda en prender."""
    for intento in range(1, intentos + 1):
        try:
            Base.metadata.create_all(engine)
            break
        except Exception:
            if intento == intentos:
                raise
            time.sleep(1)
    if sembrar:
        sembrar_catalogo()


def sembrar_catalogo(cantidad=200):
    """Carga `cantidad` productos de ejemplo, solo si la tabla productos está vacía."""
    with Sesion() as s:
        if s.query(Producto).count() > 0:
            return
        s.add_all([
            Producto(
                nombre=f"Producto {i:03d}",
                precio=round(10_000 + i * 137.5, 2),
            )
            for i in range(1, cantidad + 1)
        ])
        s.commit()


def contar_pendientes():
    """Cuenta las órdenes pendientes sin usar el pool principal."""
    with engine_monitor.connect() as con:
        consulta = select(func.count()).where(Orden.estado == "pendiente")
        return con.execute(consulta).scalar_one()
