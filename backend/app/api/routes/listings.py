"""Listing endpoints. Filtering and paging are pushed down to SQL."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from app.api.deps import RepositoryDep
from app.models.filters import ListingQuery, MapQuery
from app.models.listing import Listing
from app.models.responses import Facets, ListingPage, MapData

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get(
    "",
    response_model=ListingPage,
    summary="Lista paginada de anuncios",
    description=(
        "Devuelve los anuncios que cumplen los filtros, junto con el total que "
        "los cumple ignorando la paginación.\n\n"
        "El bounding box (`lat_min`, `lat_max`, `lon_min`, `lon_max`) filtra por "
        "el área visible del mapa y hay que darlo completo o no darlo. Los "
        "filtros de superficie y habitaciones excluyen los anuncios que no "
        "declaran ese dato.\n\n"
        "Para buscar chollos: `?solo_chollos=true&orden=desviacion` devuelve los "
        "anuncios más baratos de lo que el modelo estima, empezando por el que "
        "más se aparta."
    ),
)
def list_listings(
    query: Annotated[ListingQuery, Query()], repository: RepositoryDep
) -> ListingPage:
    items = repository.list(
        query, limit=query.limit, offset=query.offset, order=query.order.value
    )
    total = repository.count(query)
    return ListingPage.build(items, total=total, limit=query.limit, offset=query.offset)


@router.get(
    "/facets",
    response_model=Facets,
    summary="Opciones y rangos para el panel de filtros",
    description=(
        "Valores disponibles (ciudades, operaciones, tipos de inmueble) y los "
        "mínimos y máximos de precio, superficie y habitaciones sobre **todo** el "
        "conjunto almacenado. Sirve para que el frontend no tenga que codificar a "
        "mano las ciudades ni los topes de los deslizadores.\n\n"
        "Se declara antes que `/listings/{listing_id}` a propósito: FastAPI resuelve "
        "las rutas por orden de registro, y si no, `facets` se leería como un id."
    ),
)
def get_facets(repository: RepositoryDep) -> Facets:
    return Facets(**repository.facets())


@router.get(
    "/map",
    response_model=MapData,
    summary="Puntos o celdas para pintar el mapa",
    description=(
        "Todo lo que cumple el filtro, a la resolución que el zoom actual puede "
        "dibujar. **No hay tope de anuncios**: cuando caben, vienen uno a uno en "
        "`points`; cuando no, vienen agrupados en celdas en `clusters`, calculadas "
        "en SQL. `total` es siempre el número exacto de coincidencias.\n\n"
        "El umbral entre los dos modos es `max_puntos`. `zoom` decide el tamaño de "
        "la celda: a más zoom, celdas más pequeñas."
    ),
)
def get_map_data(query: Annotated[MapQuery, Query()], repository: RepositoryDep) -> MapData:
    return MapData(
        **repository.map_data(
            query,
            zoom=query.zoom,
            point_budget=query.max_points,
            always_aggregate=query.heat,
        )
    )


@router.get(
    "/{listing_id}",
    response_model=Listing,
    summary="Detalle de un anuncio",
    description=(
        "Acepta el identificador global `fuente:id` (por ejemplo "
        "`idealista18:A1234`) o el id a secas. Con el id a secas, si dos fuentes "
        "comparten identificador la respuesta es 409 en lugar de elegir una."
    ),
    responses={
        404: {"description": "No existe ningún anuncio con ese identificador"},
        409: {"description": "El id existe en más de una fuente; usa fuente:id"},
    },
)
def get_listing(
    listing_id: Annotated[str, Path(description="`fuente:id` o `id`")],
    repository: RepositoryDep,
) -> Listing:
    if ":" in listing_id:
        listing = repository.get_by_global_id(listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail=f"No existe el anuncio {listing_id}")
        return listing

    matches = repository.find_by_id(listing_id)
    if not matches:
        raise HTTPException(status_code=404, detail=f"No existe el anuncio {listing_id}")
    if len(matches) > 1:
        sources = ", ".join(sorted(listing.source for listing in matches))
        raise HTTPException(
            status_code=409,
            detail=f"El id {listing_id} existe en varias fuentes ({sources}); usa fuente:id",
        )
    return matches[0]


@router.get(
    "/{source}/{listing_id}",
    response_model=Listing,
    summary="Detalle de un anuncio, con la fuente explícita",
    description="Equivalente a `/listings/{fuente}:{id}`, sin ambigüedad posible.",
    responses={404: {"description": "No existe ningún anuncio con ese identificador"}},
)
def get_listing_by_source(
    source: str,
    listing_id: str,
    repository: RepositoryDep,
) -> Listing:
    listing = repository.get(source, listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"No existe el anuncio {source}:{listing_id}")
    return listing
