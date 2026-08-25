"""Query models shared by /listings and /stats.

The public query-parameter names are Spanish (``precio_min``, ``m2_max``,
``tipo_operacion``...) because that is the vocabulary of the domain; the Python
attributes stay English like the rest of the code. Pydantic aliases bridge the
two, and ``populate_by_name`` lets internal callers use either.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.geometry import PolygonError, parse_polygon, polygon_bounds
from app.models.listing import (
    BARGAIN_THRESHOLD,
    Amenity,
    Condition,
    Operation,
    PropertyType,
)


class ListingOrder(StrEnum):
    """How to sort a page of listings."""

    RECENT = "reciente"
    PRICE = "precio"
    PRICE_DESC = "precio_desc"
    #: Biggest negative deviation first: the bargains, most extreme first.
    DEVIATION = "desviacion"


class ListingFilters(BaseModel):
    """Everything a caller can narrow a listing set by.

    Unknown query parameters are rejected rather than ignored, so a typo like
    ``precio_minimo`` fails loudly instead of silently widening the search.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    price_min: float | None = Field(
        default=None, alias="precio_min", ge=0, description="Precio mínimo en euros"
    )
    price_max: float | None = Field(
        default=None, alias="precio_max", ge=0, description="Precio máximo en euros"
    )

    size_min: float | None = Field(
        default=None, alias="m2_min", ge=0, description="Superficie mínima en m2"
    )
    size_max: float | None = Field(
        default=None, alias="m2_max", ge=0, description="Superficie máxima en m2"
    )

    rooms: int | None = Field(
        default=None, alias="habitaciones", ge=0, description="Número exacto de habitaciones"
    )
    rooms_min: int | None = Field(
        default=None, alias="habitaciones_min", ge=0, description="Habitaciones mínimas"
    )

    operation: Operation | None = Field(
        default=None, alias="tipo_operacion", description="venta o alquiler"
    )
    property_type: PropertyType | None = Field(
        default=None, alias="tipo_inmueble", description="piso, casa, estudio..."
    )

    zone: str | None = Field(
        default=None,
        alias="zona",
        min_length=1,
        description="Ciudad (coincidencia exacta, sin distinguir mayúsculas)",
    )
    neighbourhoods: list[str] | None = Field(
        default=None,
        alias="barrio",
        max_length=277,
        description=(
            "Barrios concretos, repetible: `?barrio=<LOCATIONID>&barrio=<LOCATIONID>`. "
            "Se piden **cualquiera** de ellos, no todos a la vez — al revés que "
            "`extras`, porque un piso está en un barrio y no en cinco.\n\n"
            "Toma el `LOCATIONID` del dataset y no el nombre porque los nombres se "
            "repiten: hay un «Sant Antoni» en Barcelona y otro en Valencia. Los ids "
            "y sus nombres vienen en `/listings/facets`, y los polígonos en "
            "`/neighbourhoods`.\n\n"
            "El tope de 277 es el número de barrios que hay: pedirlos todos es lo "
            "mismo que no filtrar, y pedir más es una petición mal formada."
        ),
    )
    source: str | None = Field(default=None, description="Fuente de datos, p.ej. idealista18")

    # Bounding box of the visible map area. All four or none.
    lat_min: float | None = Field(default=None, ge=-90, le=90)
    lat_max: float | None = Field(default=None, ge=-90, le=90)
    lon_min: float | None = Field(default=None, ge=-180, le=180)
    lon_max: float | None = Field(default=None, ge=-180, le=180)

    # -- características de la vivienda ------------------------------------

    bathrooms_min: int | None = Field(
        default=None, alias="banos_min", ge=0, le=20, description="Baños mínimos"
    )
    floor_min: int | None = Field(
        default=None, alias="planta_min", ge=-5, le=60,
        description="Planta mínima. 0 es bajo; útil para excluir bajos",
    )
    floor_max: int | None = Field(
        default=None, alias="planta_max", ge=-5, le=60, description="Planta máxima"
    )
    year_min: int | None = Field(
        default=None, alias="anio_min", ge=1000, le=2100,
        description="Construido a partir de este año",
    )
    condition: Condition | None = Field(
        default=None, alias="estado",
        description="obra_nueva, buen_estado o a_reformar",
    )
    amenities: list[Amenity] | None = Field(
        default=None,
        alias="extras",
        description=(
            "Extras exigidos, repetible: `?extras=ascensor&extras=garaje`. Se piden "
            "**todos**, no cualquiera de ellos"
        ),
    )

    # -- distancia a puntos de interés -------------------------------------

    center_max_km: float | None = Field(
        default=None, alias="centro_max_km", ge=0, le=100,
        description="Como mucho a esta distancia del centro de la ciudad, en km",
    )
    metro_max_km: float | None = Field(
        default=None, alias="metro_max_km", ge=0, le=100,
        description="Como mucho a esta distancia de una boca de metro, en km",
    )

    # -- price model -------------------------------------------------------

    bargains_only: bool = Field(
        default=False,
        alias="solo_chollos",
        description=(
            "Solo anuncios cuyo precio está muy por debajo del que estima el modelo. "
            f"Equivale a `desviacion_max={BARGAIN_THRESHOLD:.0f}`, y `desviacion_max` "
            "manda si se dan los dos"
        ),
    )
    deviation_max: float | None = Field(
        default=None,
        alias="desviacion_max",
        ge=-100,
        le=1000,
        description=(
            "Desviación máxima sobre el precio estimado, en %. **Negativo = más barato "
            "de lo esperado**: `-30` devuelve lo que se pide al menos un 30 % por debajo "
            "de la estimación. Excluye los anuncios sin estimar"
        ),
    )
    scored_only: bool = Field(
        default=False,
        alias="solo_estimados",
        description="Solo anuncios a los que el modelo ha podido poner precio",
    )

    ids: list[str] | None = Field(
        default=None,
        alias="ids",
        max_length=500,
        description=(
            "Identificadores globales concretos (`fuente:id`), repetibles: "
            "`?ids=idealista18:A1&ids=idealista18:A2`. Lo usa la vista de favoritos, "
            "que vive en el navegador y solo puede pedir sus anuncios por id"
        ),
    )

    polygon: str | None = Field(
        default=None,
        alias="poligono",
        description=(
            "Área dibujada a mano, como `lat,lon;lat,lon;...` (mínimo 3 vértices). "
            "Se combina con el resto de filtros y, si además hay bounding box, con él."
        ),
    )

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        """(lat_min, lat_max, lon_min, lon_max) when the box is complete."""
        parts = (self.lat_min, self.lat_max, self.lon_min, self.lon_max)
        return None if any(part is None for part in parts) else parts  # type: ignore[return-value]

    @property
    def polygon_bbox(self) -> tuple[float, float, float, float] | None:
        """The drawn area's bounding box, used as the indexed prefilter in SQL."""
        return None if self.polygon is None else polygon_bounds(self.polygon)

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        if self.price_min is not None and self.price_max is not None:
            if self.price_min > self.price_max:
                raise ValueError("precio_min no puede ser mayor que precio_max")

        if self.size_min is not None and self.size_max is not None:
            if self.size_min > self.size_max:
                raise ValueError("m2_min no puede ser mayor que m2_max")

        if self.floor_min is not None and self.floor_max is not None:
            if self.floor_min > self.floor_max:
                raise ValueError("planta_min no puede ser mayor que planta_max")

        if self.rooms is not None and self.rooms_min is not None:
            raise ValueError("usa habitaciones o habitaciones_min, no ambos")

        provided = [
            name
            for name in ("lat_min", "lat_max", "lon_min", "lon_max")
            if getattr(self, name) is not None
        ]
        if provided and len(provided) != 4:
            missing = {"lat_min", "lat_max", "lon_min", "lon_max"} - set(provided)
            raise ValueError(
                "el bounding box necesita lat_min, lat_max, lon_min y lon_max; "
                f"faltan: {', '.join(sorted(missing))}"
            )

        if self.lat_min is not None and self.lat_min > self.lat_max:  # type: ignore[operator]
            raise ValueError("lat_min no puede ser mayor que lat_max")
        if self.lon_min is not None and self.lon_min > self.lon_max:  # type: ignore[operator]
            raise ValueError("lon_min no puede ser mayor que lon_max")

        if self.bargains_only and self.deviation_max is None:
            # Resolved here, once, so every consumer of the filters sees a single
            # numeric threshold instead of having to know about the flag.
            self.deviation_max = BARGAIN_THRESHOLD

        if self.polygon is not None:
            # Parsed here rather than at query time so a malformed drawing comes
            # back as a 422 with a useful message instead of a 500 from SQLite.
            try:
                parse_polygon(self.polygon)
            except PolygonError as exc:
                raise ValueError(f"poligono inválido: {exc}") from None

        return self


class ListingQuery(ListingFilters):
    """Filters plus pagination, for the listing endpoint."""

    limit: int = Field(default=100, ge=1, le=1000, description="Anuncios por página")
    offset: int = Field(default=0, ge=0, description="Anuncios a saltar")
    order: ListingOrder = Field(
        default=ListingOrder.RECENT,
        alias="orden",
        description=(
            "`reciente`, `precio`, `precio_desc` o `desviacion`. Con `desviacion` "
            "los más baratos respecto a la estimación salen primero, y los que no "
            "tienen estimación quedan al final"
        ),
    )


class MapQuery(ListingFilters):
    """Filters plus what the map needs to choose its drawing resolution."""

    zoom: int = Field(
        default=6,
        ge=0,
        le=20,
        description="Nivel de zoom de Leaflet; fija el tamaño de la celda al agregar",
    )
    heat: bool = Field(
        default=False,
        alias="calor",
        description=(
            "Devuelve siempre celdas agregadas, con su precio medio por m², "
            "aunque los anuncios cupieran uno a uno. Es lo que necesita el mapa "
            "de calor: si cambiara a marcadores al acercarse, la capa "
            "desaparecería justo cuando se quiere comparar barrio con barrio"
        ),
    )
    max_points: int = Field(
        default=6000,
        alias="max_puntos",
        ge=1,
        le=50_000,
        description=(
            "A partir de cuántas coincidencias se pasa de marcadores individuales "
            "a celdas agregadas. `max_puntos=1` fuerza siempre la agregación"
        ),
    )


class StatsQuery(ListingFilters):
    """Filters plus histogram resolution, for the stats endpoint.

    ``bins`` lives inside the model rather than as a separate query parameter:
    FastAPI stops treating a Pydantic model as a query model as soon as another
    scalar ``Query`` parameter with a default sits beside it, and the whole
    model then arrives as a single missing field.
    """

    bins: int = Field(
        default=20,
        alias="intervalos",
        ge=1,
        le=100,
        description="Número de intervalos del histograma de precios",
    )
