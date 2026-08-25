"""Pydantic response models. Every endpoint returns one of these."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.listing import Amenity, Listing, Operation, PropertyType


class ListingPage(BaseModel):
    """One page of listings plus the counters a UI needs to paginate."""

    items: list[Listing] = Field(description="Anuncios de esta página")
    total: int = Field(description="Anuncios que cumplen el filtro, ignorando la paginación")
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:  # pragma: no cover - derived in the serialiser below
        return self.offset + len(self.items) < self.total

    @classmethod
    def build(cls, items: list[Listing], total: int, limit: int, offset: int) -> ListingPage:
        return cls(items=items, total=total, limit=limit, offset=offset)


class PriceBucket(BaseModel):
    """One bar of the price histogram."""

    lower: float = Field(description="Límite inferior del intervalo, incluido")
    upper: float | None = Field(
        default=None,
        description="Límite superior, excluido. Nulo en el último intervalo, que es abierto",
    )
    count: int


class ZoneStats(BaseModel):
    """Aggregates for one zone: a city, or a neighbourhood inside a city."""

    zone: str = Field(description="Nombre de la ciudad o del barrio, según el corte")
    neighbourhood_id: str | None = Field(
        default=None,
        description=(
            "`LOCATIONID` cuando la fila es un barrio, para poder filtrar por ella. "
            "Nulo cuando el corte es por ciudad"
        ),
    )
    count: int
    avg_price: float = Field(description="Precio medio en euros")
    median_price: float | None = Field(default=None, description="Mediana en euros")
    min_price: float
    max_price: float
    avg_price_per_m2: float | None = Field(
        default=None, description="Media de precio/m2, sobre los anuncios que declaran superficie"
    )


class OverallStats(BaseModel):
    """Aggregates over the whole filtered set."""

    count: int
    avg_price: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    avg_price_per_m2: float | None = None
    p25_price: float | None = Field(default=None, description="Percentil 25")
    median_price: float | None = Field(default=None, description="Percentil 50")
    p75_price: float | None = Field(default=None, description="Percentil 75")
    p90_price: float | None = Field(default=None, description="Percentil 90")
    p99_price: float | None = Field(default=None, description="Percentil 99")


class MapPoint(BaseModel):
    """One listing as the map needs it: where it is and what it looks like.

    Deliberately not a full :class:`Listing`. The map may draw thousands of
    these at once, and the title, address and timestamps would triple the
    payload for information no marker shows. The detail panel fetches the whole
    listing from `/listings/{id}` when one is actually clicked.
    """

    global_id: str
    latitude: float
    longitude: float
    price: float
    property_type: PropertyType
    operation: Operation
    price_deviation: float | None = Field(
        default=None,
        description="Desviación sobre el precio estimado, en %. Negativo = más barato",
    )


class MapCluster(BaseModel):
    """An aggregated grid cell: many listings counted rather than drawn."""

    latitude: float = Field(description="Centroide de los anuncios de la celda")
    longitude: float
    count: int
    avg_price: float | None = None
    avg_price_per_m2: float | None = Field(
        default=None,
        description=(
            "Media de precio/m² de la celda, sobre los anuncios que declaran "
            "superficie. Nulo si ninguno lo hace"
        ),
    )
    with_size: int = Field(
        default=0, description="Cuántos anuncios de la celda declaran superficie"
    )
    lat_min: float | None = None
    lat_max: float | None = None
    lon_min: float | None = None
    lon_max: float | None = None
    zone: str | None = Field(
        default=None,
        description=(
            "Nombre de la zona, cuando la agrupación ha sido por zona y no por "
            "rejilla. Nulo en las celdas de rejilla, que pueden abarcar varias"
        ),
    )


class MapData(BaseModel):
    """What to draw on the map at the current zoom, without ever truncating.

    ``total`` is always the exact number of matching listings. When they are
    few enough they arrive one by one in ``points``; when they are not, they
    arrive counted into ``clusters``. Nothing is dropped in either case.
    """

    mode: Literal["points", "clusters"] = Field(
        description="`points` = un marcador por anuncio; `clusters` = celdas agregadas"
    )
    total: int = Field(description="Anuncios que cumplen el filtro. Exacto, nunca truncado")
    points: list[MapPoint] = Field(default_factory=list)
    clusters: list[MapCluster] = Field(default_factory=list)


class Bucket(BaseModel):
    """One bar of a distribution: a band of rooms or of floor area."""

    bucket: int = Field(description="Índice del tramo, para ordenar")
    label: str = Field(description="Etiqueta legible, p. ej. `80–100`")
    count: int
    avg_price: float
    avg_price_per_m2: float


class AmenityImpact(BaseModel):
    """What listings with a given extra cost, next to those without it.

    A correlation, not a valuation. The gap for a swimming pool says where flats
    with pools are, not what a pool is worth: they sit in neighbourhoods where
    the square metre already costs that.
    """

    amenity: Amenity
    count: int = Field(description="Anuncios que lo tienen")
    share: float = Field(description="Porcentaje del conjunto filtrado que lo tiene")
    with_it: float = Field(description="€/m² medio de los que lo tienen")
    without_it: float = Field(description="€/m² medio de los que no")
    difference: float = Field(description="Diferencia entre ambos, en %")


class StatsResponse(BaseModel):
    """Everything /stats returns, for the same filters /listings accepts."""

    overall: OverallStats
    by_zone: list[ZoneStats] = Field(description="Ordenado por número de anuncios, descendente")
    by_zone_is_neighbourhood: bool = Field(
        default=False,
        description=(
            "Si `by_zone` está cortado por barrio en vez de por ciudad. Ocurre en "
            "cuanto la búsqueda ya está acotada a una ciudad o a unos barrios, "
            "porque entonces cortar por ciudad daría una sola fila"
        ),
    )
    by_rooms: list[Bucket] = Field(
        default_factory=list,
        description="Precio medio y €/m² por número de habitaciones",
    )
    by_size: list[Bucket] = Field(
        default_factory=list,
        description="Lo mismo por tramos de superficie construida",
    )
    by_distance: list[Bucket] = Field(
        default_factory=list,
        description=(
            "€/m² por distancia al centro, en tramos de km. La pendiente no es la "
            "misma en cada ciudad, así que con varias mezcladas la curva no "
            "describe ninguna"
        ),
    )
    amenities: list[AmenityImpact] = Field(
        default_factory=list,
        description="Diferencia de €/m² entre tener cada extra y no tenerlo",
    )
    price_distribution: list[PriceBucket] = Field(
        description=(
            "Histograma de precios. Los intervalos son de ancho constante entre el mínimo "
            "y el percentil 99; el último es abierto y recoge la cola larga, de modo que "
            "un solo anuncio caro no aplasta el gráfico"
        )
    )


class FacetValue(BaseModel):
    """One option of a categorical filter, with how many listings carry it."""

    value: str
    count: int


class NeighbourhoodFacet(BaseModel):
    """One neighbourhood, as the sidebar picker needs it.

    The bounds are the polygon's, so they are the neighbourhood's real extent
    and not an estimate from where its listings happen to be.
    """

    id: str = Field(description="`LOCATIONID` del dataset. Es lo que acepta el filtro `barrio`")
    name: str
    city: str
    count: int = Field(description="Anuncios dentro del polígono, sin filtrar")
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


class ZoneFacet(FacetValue):
    """A zone, plus the box the map should fly to when it is picked.

    Not the full extent of the zone: a single listing filed under the wrong
    city would open the map on half the country. The box covers the mean plus
    or minus three standard deviations, clipped to the real extent.
    """

    lat_min: float | None = None
    lat_max: float | None = None
    lon_min: float | None = None
    lon_max: float | None = None

    neighbourhoods: list[NeighbourhoodFacet] = Field(
        default_factory=list,
        description=(
            "Los barrios de esta ciudad, **por orden alfabético**, con cuántos "
            "anuncios tiene cada uno. Anidados dentro de su ciudad porque así es "
            "como se eligen: primero la ciudad, luego el barrio. Vacío si los "
            "polígonos no están disponibles"
        ),
    )


class Facets(BaseModel):
    """Options and bounds a filter panel needs, over the whole stored set."""

    total: int = Field(description="Anuncios almacenados, sin filtrar")
    amenities: list[FacetValue] = Field(
        default_factory=list, description="Extras disponibles y cuántos los tienen"
    )
    conditions: list[FacetValue] = Field(
        default_factory=list, description="Estados de conservación disponibles"
    )
    zones: list[ZoneFacet] = Field(
        description="Ciudades o barrios, del más poblado al menos, con sus límites geográficos"
    )
    operations: list[FacetValue] = Field(description="venta / alquiler")
    property_types: list[FacetValue] = Field(description="piso, casa, estudio...")
    price_min: float | None = None
    price_max: float | None = None
    size_min: float | None = None
    size_max: float | None = None
    rooms_min: int | None = None
    rooms_max: int | None = None
    bathrooms_min: int | None = None
    bathrooms_max: int | None = None
    floor_min: int | None = None
    floor_max: int | None = None
    year_min: int | None = None
    year_max: int | None = None
    center_max_km: float | None = Field(
        default=None, description="Distancia máxima al centro que hay en los datos"
    )
    metro_max_km: float | None = None


class GeoFeature(BaseModel):
    """One GeoJSON feature, as RFC 7946 defines it.

    Declared for the documentation only: `/neighbourhoods` and
    `/points-of-interest` return bytes read straight off disk, because putting
    12.101 vertices through Pydantic on every request costs more than reading
    the file did. Keeping the model here means the OpenAPI page still describes
    the shape honestly -- see `app.geodata` for the whole argument.
    """

    type: Literal["Feature"] = "Feature"
    properties: dict[str, str | None] = Field(
        description=(
            "En los barrios: `location_id`, `name`, `city`. En los puntos de "
            "interés: `kind` (centro / metro / calle), `city` y, salvo en el "
            "metro, `name`"
        )
    )
    geometry: dict[str, Any] = Field(
        description="`MultiPolygon`, `Point` o `LineString`, en EPSG:4326 y con [lon, lat]"
    )


class FeatureCollection(BaseModel):
    """A GeoJSON FeatureCollection: what both geography endpoints return."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[GeoFeature]
