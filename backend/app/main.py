"""FastAPI application factory and ASGI entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api import api_router
from app.api.deps import get_database
from app.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the SQLite schema before the first request is served."""
    get_database().init_schema()
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Housing Explorer API",
        description="Normalised housing listings from pluggable sources.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Compresión antes que CORS a propósito: `add_middleware` inserta al
    # principio, así que el último en declararse queda por fuera, y CORS por
    # fuera es lo que garantiza que sus cabeceras salgan también en los errores.
    #
    # Vale la pena por lo que hay que mandar ahora: los polígonos de barrio son
    # 278 kB de JSON. Los agregados de /stats y las celdas del mapa se
    # benefician igual. El mínimo de 1 kB deja fuera las respuestas cortas,
    # donde comprimir cuesta más CPU de lo que ahorra en red.
    #
    # El nivel 4 no es el que trae Starlette, que usa el 9. Medido sobre esos
    # 278 kB de barrios:
    #
    #     nivel 1 ->  82 kB en  4,9 ms
    #     nivel 4 ->  71 kB en  7,6 ms
    #     nivel 6 ->  67 kB en 16,8 ms
    #     nivel 9 ->  66 kB en 71,3 ms
    #
    # Del 4 al 9 se ganan 5 kB y se pagan 64 ms de CPU por petición: en la
    # práctica, la petición completa pasaba de 5 ms a 76. Este servidor ya se
    # cayó una vez por contención de CPU entre hilos (ver storage/cache.py), así
    # que gastarla a cambio de un 7 % de tamaño es exactamente el cambio que no
    # interesa.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=4)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api")

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"service": settings.app_name, "docs": "/docs"}

    return app


app = create_app()
