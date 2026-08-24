"""Normalised domain schemas shared by ingestion, storage and the API.

Every :class:`~app.ingestion.base.ListingSource` must emit :class:`Listing`
objects, so the rest of the system never sees a provider-specific payload.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Operation(StrEnum):
    """Whether the listing is offered for sale or for rent."""

    SALE = "venta"
    RENT = "alquiler"


class PropertyType(StrEnum):
    """Coarse property taxonomy every source is expected to map onto."""

    FLAT = "piso"
    HOUSE = "casa"
    STUDIO = "estudio"
    DUPLEX = "duplex"
    PENTHOUSE = "atico"
    ROOM = "habitacion"
    LAND = "terreno"
    GARAGE = "garaje"
    PREMISES = "local"
    OTHER = "otro"


#: How far below the estimate a listing has to be before it is worth flagging,
#: as a percentage. Not a round number picked by taste: the model's median
#: absolute error is 10.3% and its MAPE 14.1% (see notebooks/analisis.ipynb), so
#: anything inside ±15% is indistinguishable from the model being wrong. -25% is
#: roughly 2.4x the median error, which keeps the flag rare enough to mean
#: something. It stays a constant, and a query parameter, because the right
#: threshold depends on how much noise the person searching will tolerate.
BARGAIN_THRESHOLD = -25.0


class Condition(StrEnum):
    """State of the dwelling, as the source classifies it.

    Not an inspection: it is whatever the portal was told. `idealista18` encodes
    it in three mutually exclusive flags, and the real API has the same notion.
    """

    NEW = "obra_nueva"
    GOOD = "buen_estado"
    NEEDS_WORK = "a_reformar"


class Amenity(StrEnum):
    """Things a home either has or does not.

    Deliberately a short, portal-agnostic list rather than every flag the dataset
    happens to carry. These are the ones a search panel actually offers, and the
    ones the official Idealista API also reports, so a second source can fill the
    same set without the vocabulary having to change.
    """

    LIFT = "ascensor"
    TERRACE = "terraza"
    PARKING = "garaje"
    AIR_CONDITIONING = "aire_acondicionado"
    POOL = "piscina"
    DOORMAN = "portero"
    GARDEN = "jardin"
    STORAGE = "trastero"
    WARDROBES = "armarios"


#: Column name in `listings` for each amenity. The storage layer is the only
#: thing that should ever see these; everything above it works with the enum.
AMENITY_COLUMNS: dict[Amenity, str] = {
    Amenity.LIFT: "has_lift",
    Amenity.TERRACE: "has_terrace",
    Amenity.PARKING: "has_parking",
    Amenity.AIR_CONDITIONING: "has_air_conditioning",
    Amenity.POOL: "has_pool",
    Amenity.DOORMAN: "has_doorman",
    Amenity.GARDEN: "has_garden",
    Amenity.STORAGE: "has_storage",
    Amenity.WARDROBES: "has_wardrobes",
}


class Listing(BaseModel):
    """A single housing advert, normalised across sources.

    ``id`` is the source-local identifier; the pair ``(source, id)`` is what
    makes a listing globally unique, which is how storage deduplicates.
    """

    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    id: str = Field(description="Identifier of the advert within its source")
    source: str = Field(description="Name of the ListingSource that produced it")

    title: str
    url: str | None = Field(default=None, description="Canonical link to the advert")

    operation: Operation
    property_type: PropertyType = PropertyType.OTHER

    price: float = Field(ge=0, description="EUR: total for sale, monthly for rent")
    size_m2: float | None = Field(default=None, ge=0)
    rooms: int | None = Field(default=None, ge=0)

    bathrooms: int | None = Field(default=None, ge=0)
    floor: int | None = Field(
        default=None,
        ge=-5,
        le=60,
        description="Planta. 0 es bajo y los negativos, sótano o semisótano",
    )
    year_built: int | None = Field(
        default=None, ge=1000, le=2100, description="Año de construcción del edificio"
    )
    condition: Condition | None = Field(
        default=None, description="obra_nueva, buen_estado o a_reformar, según la fuente"
    )

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    address: str | None = None
    zone: str | None = Field(default=None, description="District / neighbourhood")

    distance_to_center_km: float | None = Field(
        default=None, ge=0, description="Distancia en línea recta al centro de la ciudad"
    )
    distance_to_metro_km: float | None = Field(
        default=None, ge=0, description="Distancia a la boca de metro más cercana"
    )

    amenities: list[Amenity] = Field(
        default_factory=list,
        description="Extras que tiene la vivienda: ascensor, garaje, piscina…",
    )

    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # -- derived by the price model, not by the source ---------------------

    expected_price: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Precio que el modelo estima para esta vivienda, en euros. "
            "Nulo si no se ha podido estimar"
        ),
    )
    price_deviation: float | None = Field(
        default=None,
        description=(
            "Cuánto se aparta el precio pedido del estimado, en tanto por ciento. "
            "**Negativo = más barato de lo esperado.** -30 significa que se pide un "
            "30 % menos de lo que el modelo estima"
        ),
    )

    @property
    def global_id(self) -> str:
        """Stable cross-source key, e.g. ``sample_csv:1234``."""
        return f"{self.source}:{self.id}"

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def is_bargain(self, threshold: float = BARGAIN_THRESHOLD) -> bool:
        """Whether the asking price is far enough below the estimate to stand out."""
        return self.price_deviation is not None and self.price_deviation <= threshold
