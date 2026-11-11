"""
API de la tienda (FastAPI). Tres caminos, como en la simulación: lectura (/catalogo),
escritura rápida (/carrito) y escritura lenta (/pagos/confirmar, encola la orden para el worker).
Cada petición queda en datos/telemetry_log.csv. Docker compose la deja en localhost:8000.
"""
import time
from contextlib import asynccontextmanager, contextmanager

import anyio.to_thread
from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import TimeoutError as PoolTimeout

from . import config, telemetry
from .db import (
    ItemCarrito, Orden, Producto, Sesion, contar_pendientes, engine, preparar_base,
)

registro = telemetry.RegistroCSV(
    "telemetry_log.csv", telemetry.COLUMNAS_API, config.CARPETA_DATOS
)


@asynccontextmanager
async def ciclo_de_vida(app):
    """Lo que hace la API al prender (antes del yield) y al apagarse (después del yield)."""
    # Fijamos cuántos hilos tiene el punto de acceso, como decidir cuántos cajeros abren.
    anyio.to_thread.current_default_thread_limiter().total_tokens = config.HILOS_ACCESO
    preparar_base()
    registro.abrir()
    yield
    registro.cerrar()


app = FastAPI(title="Gemelo digital e-commerce - sistema real", lifespan=ciclo_de_vida)

# A qué camino del modelo pertenece cada ruta (se escribe en la columna "tipo").
TIPO_POR_RUTA = {
    "/catalogo": "lectura",
    "/carrito": "escritura_rapida",
    "/pagos/confirmar": "escritura_lenta",
    "/ordenes/{orden_id}": "consulta_orden",
}
# Rutas de mantenimiento: no son tráfico de compradores y no se anotan.
RUTAS_SIN_MEDIR = {"/salud", "/metricas", "/docs", "/openapi.json"}

# Peticiones dentro de la API ahora. El middleware corre en un solo hilo, no necesita candado.
en_vuelo = 0


@app.middleware("http")
async def medir(request: Request, call_next):
    """Mide cada petición y la anota. Ojo: los 503 del tope de uvicorn no llegan aquí."""
    global en_vuelo
    if request.url.path in RUTAS_SIN_MEDIR:
        return await call_next(request)

    # espera_pool_ms la llena sesion_medida() más adelante.
    request.state.espera_pool_ms = 0.0
    en_vuelo += 1
    ocupacion = en_vuelo
    inicio = time.perf_counter()
    estado = 500  # si el endpoint falla sin responder, queda anotado como 500
    try:
        respuesta = await call_next(request)
        estado = respuesta.status_code
        return respuesta
    finally:
        latencia_ms = (time.perf_counter() - inicio) * 1000
        en_vuelo -= 1
        # Guardamos la ruta "plantilla" (/ordenes/{orden_id}) y no /ordenes/123.
        ruta = getattr(request.scope.get("route"), "path", request.url.path)
        # Escenario y etapa llegan en cabeceras del gateway. Si no vienen, quedan "manual" y "0".
        registro.escribir([
            telemetry.ahora_iso(),
            request.headers.get("x-escenario", "manual"),
            request.headers.get("x-etapa", "0"),
            ruta,
            TIPO_POR_RUTA.get(ruta, "otro"),
            f"{latencia_ms:.3f}",
            f"{request.state.espera_pool_ms:.3f}",
            estado,
            ocupacion,
        ])


@contextmanager
def sesion_medida(request):
    """Pide una conexión del pool y mide la espera. Tras POOL_TIMEOUT s sin ella, da 503."""
    sesion = Sesion()
    t0 = time.perf_counter()
    try:
        sesion.connection()  # aquí se toma la conexión del pool (o se espera por ella)
    except PoolTimeout:
        sesion.close()
        raise HTTPException(status_code=503, detail="pool de conexiones agotado")
    finally:
        request.state.espera_pool_ms = (time.perf_counter() - t0) * 1000
    # Al terminar el "with" del endpoint, la conexión vuelve al pool.
    try:
        yield sesion
    finally:
        sesion.close()


# --- 1. Lectura: catálogo ---
@app.get("/catalogo")
def ver_catalogo(request: Request, pagina: int = Query(1, ge=1), tam: int = Query(20, ge=1, le=100)):
    """Devuelve una página de `tam` productos ordenados por id."""
    with sesion_medida(request) as s:
        filas = (
            s.query(Producto)
            .order_by(Producto.id)
            .offset((pagina - 1) * tam)
            .limit(tam)
            .all()
        )
    return {
        "pagina": pagina,
        "productos": [
            {"id": p.id, "nombre": p.nombre, "precio": float(p.precio)}
            for p in filas
        ],
    }


# --- 2. Escritura rápida: carrito ---
class ItemEntrada(BaseModel):
    """JSON de POST /carrito. Si está mal, FastAPI responde 422."""
    comprador_id: str = Field(min_length=1, max_length=64)
    producto_id: int
    cantidad: int = Field(1, ge=1, le=10)


@app.post("/carrito", status_code=201)
def agregar_al_carrito(item: ItemEntrada, request: Request):
    """Guarda el ítem y responde de una vez. No crea orden."""
    with sesion_medida(request) as s:
        if s.get(Producto, item.producto_id) is None:
            raise HTTPException(status_code=404, detail="producto no existe")
        s.add(ItemCarrito(
            comprador_id=item.comprador_id,
            producto_id=item.producto_id,
            cantidad=item.cantidad,
        ))
        s.commit()
    return {"estado": "agregado", "producto_id": item.producto_id}


# --- 3. Escritura lenta: confirmación de pago ---
class PagoEntrada(BaseModel):
    """JSON de POST /pagos/confirmar."""
    comprador_id: str = Field(min_length=1, max_length=64)


@app.post("/pagos/confirmar", status_code=202)
def confirmar_pago(pago: PagoEntrada, request: Request, response: Response):
    """Crea la orden pendiente y responde 202 (recibida). La procesa después el worker."""
    with sesion_medida(request) as s:
        items = (
            s.query(ItemCarrito)
            .filter(ItemCarrito.comprador_id == pago.comprador_id)
            .all()
        )
        # Carrito vacío: si ya tiene orden es un reintento y devolvemos la misma con 200 (si no, 400).
        # Así reintentar tras un timeout no es error. Suponemos una compra pendiente por comprador.
        if not items:
            previa = (
                s.query(Orden)
                .filter(Orden.comprador_id == pago.comprador_id)
                .order_by(Orden.id.desc())
                .first()
            )
            if previa is None:
                raise HTTPException(status_code=400, detail="carrito vacio")
            response.status_code = 200
            return {"orden_id": previa.id, "estado": previa.estado}

        # Tope de la cola: con TOPE_COLA_ORDENES pendientes, rechaza con 503.
        pendientes = s.query(Orden).filter(Orden.estado == "pendiente").count()
        if pendientes >= config.TOPE_COLA_ORDENES:
            raise HTTPException(status_code=503, detail="cola de ordenes llena")

        total = 0.0
        for it in items:
            p = s.get(Producto, it.producto_id)
            if p is not None:
                total += float(p.precio) * it.cantidad

        # Crear la orden y vaciar el carrito van en el mismo commit.
        orden = Orden(comprador_id=pago.comprador_id, total=total, estado="pendiente")
        s.add(orden)
        for it in items:
            s.delete(it)
        s.commit()
        orden_id = orden.id

    return {"orden_id": orden_id, "estado": "pendiente"}


# --- Consulta de la orden (polling) ---
@app.get("/ordenes/{orden_id}")
def ver_orden(orden_id: int, request: Request):
    """Estado de una orden. Es lo que pregunta el comprador en el polling."""
    with sesion_medida(request) as s:
        orden = s.get(Orden, orden_id)
        if orden is None:
            raise HTTPException(status_code=404, detail="orden no existe")
        return {"orden_id": orden.id, "estado": orden.estado, "total": float(orden.total)}


# --- Mantenimiento ---
@app.get("/salud")
def salud():
    """Responde ok si la API está prendida."""
    return {"estado": "ok"}


@app.get("/metricas")
def metricas():
    """Estado de los recursos ahora. No usa el pool principal, pero sí un hilo de acceso."""
    return {
        "pool_tamano": config.POOL_SIZE,
        "pool_en_uso": engine.pool.checkedout(),  # conexiones prestadas en este momento
        "hilos_acceso": config.HILOS_ACCESO,
        "en_vuelo": en_vuelo,
        "ordenes_pendientes": contar_pendientes(),
    }
