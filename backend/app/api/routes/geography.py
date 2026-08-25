"""The fixed geography: neighbourhood polygons and points of interest.

Both endpoints return plain GeoJSON, ready to hand to a Leaflet ``L.geoJSON``
layer without transforming anything. They are the only endpoints in the API
that do not go near SQLite -- see :mod:`app.geodata` for why the data is a file.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from app import geodata
from app.models.responses import FeatureCollection

router = APIRouter(tags=["geografía"])

#: GeoJSON has its own media type (RFC 7946 §12). Anything that understands it
#: also understands application/json, so nothing is lost by being specific.
GEOJSON_MEDIA_TYPE = "application/geo+json"

#: A day. These files change when the ingestion is re-run, which is to say
#: hardly ever; the browser re-fetching 280 kB of unchanged borders on every
#: reload is pure waste. Not `immutable`, so a re-export still lands eventually.
CACHE_CONTROL = "public, max-age=86400"

CityParam = Annotated[
    str | None,
    Query(
        alias="ciudad",
        description="Devuelve solo la geografía de esa ciudad. Sin distinguir mayúsculas.",
        examples=["Madrid"],
    ),
]


def _geojson(body: bytes) -> Response:
    """Serve pre-serialised bytes, rather than a model FastAPI would re-encode."""
    return Response(
        content=body,
        media_type=GEOJSON_MEDIA_TYPE,
        headers={"Cache-Control": CACHE_CONTROL},
    )


def _serve(body_for: Callable[[str | None], bytes], city: str | None) -> Response:
    try:
        body = body_for(city)
    except geodata.GeoDataUnavailable as error:
        # 503 and not 500: the API is working, the geography just is not there,
        # and the message says which command puts it there.
        raise HTTPException(status_code=503, detail=str(error)) from error
    return _geojson(body)


@router.get(
    "/neighbourhoods",
    response_model=None,
    responses={200: {"model": FeatureCollection, "content": {GEOJSON_MEDIA_TYPE: {}}}},
    summary="Polígonos de barrio, en GeoJSON",
    description=(
        "Los 277 barrios que el dataset delimita en Madrid, Barcelona y Valencia, "
        "como un `FeatureCollection` de `MultiPolygon` en EPSG:4326.\n\n"
        "Cada `Feature` lleva en `properties` el `location_id` del dataset "
        "(`LOCATIONID`), el `name` del barrio y la `city`.\n\n"
        "No depende de los filtros: es la geografía, no los anuncios."
    ),
)
def get_neighbourhoods(city: CityParam = None) -> Response:
    return _serve(geodata.neighbourhoods, city)


@router.get(
    "/points-of-interest",
    response_model=None,
    responses={200: {"model": FeatureCollection, "content": {GEOJSON_MEDIA_TYPE: {}}}},
    summary="Puntos de interés, en GeoJSON",
    description=(
        "Tres cosas por ciudad, distinguidas por `properties.kind`:\n\n"
        "- `centro`: un `Point`, el centro de la ciudad (CBD) desde el que el "
        "dataset mide `DISTANCE_TO_CITY_CENTER`.\n"
        "- `metro`: un `Point` por boca de metro. El dataset no trae los nombres "
        "de las estaciones, así que estos `Feature` no llevan `name`.\n"
        "- `calle`: un `LineString` con la calle principal de la ciudad "
        "(Castellana, Diagonal, Blasco Ibáñez), que es lo que el dataset usa "
        "como eje de referencia."
    ),
)
def get_points_of_interest(city: CityParam = None) -> Response:
    return _serve(geodata.points_of_interest, city)


# El usuario que escriba la grafía americana no debería encontrarse un 404 por
# una letra. El resto del código es británico ("neighbourhood", "colour"), así
# que la canónica es la de arriba y esta es un alias que no ensucia el OpenAPI.
@router.get("/neighborhoods", include_in_schema=False)
def get_neighborhoods_alias(city: CityParam = None) -> Response:
    return _serve(geodata.neighbourhoods, city)
