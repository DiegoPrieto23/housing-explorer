"""Aggregated statistics over the same filtered set that /listings returns."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import RepositoryDep
from app.models.filters import ListingFilters, StatsQuery
from app.models.responses import (
    AmenityImpact,
    Bucket,
    OverallStats,
    PriceBucket,
    StatsResponse,
    ZoneStats,
)
from app.storage.repository import ListingRepository

router = APIRouter(prefix="/stats", tags=["stats"])


def _price_distribution(
    repository: ListingRepository,
    filters: ListingFilters,
    overall: OverallStats,
    bins: int,
) -> list[PriceBucket]:
    """Build the histogram, clipping the long tail into one open bucket.

    Equal-width buckets between the minimum and the 99th percentile. Without
    the clip a single multi-million-euro listing would stretch the axis and
    leave every other bucket invisible.
    """
    if not overall.count or overall.min_price is None:
        return []

    lower = overall.min_price
    upper = overall.p99_price if overall.p99_price is not None else overall.max_price

    if upper is None or upper <= lower:
        # Every listing has (near enough) the same price: one bucket says it all.
        return [PriceBucket(lower=lower, upper=None, count=overall.count)]

    counts = repository.price_histogram(filters, lower=lower, upper=upper, bins=bins)
    width = (upper - lower) / bins

    buckets = [
        PriceBucket(
            lower=lower + index * width,
            upper=lower + (index + 1) * width,
            count=counts.get(index, 0),
        )
        for index in range(bins)
    ]
    # The overflow bucket is open-ended: everything at or above the p99.
    buckets.append(PriceBucket(lower=upper, upper=None, count=counts.get(bins, 0)))
    return buckets


@router.get(
    "",
    response_model=StatsResponse,
    summary="Estadísticas agregadas",
    description=(
        "Acepta exactamente los mismos filtros que `/listings`, así que se puede "
        "pedir la estadística del área visible del mapa moviendo el bounding box.\n\n"
        "Devuelve totales y percentiles del conjunto filtrado, medias por zona, y "
        "un histograma de precios listo para pintar."
    ),
)
def get_stats(query: Annotated[StatsQuery, Query()], repository: RepositoryDep) -> StatsResponse:
    overall = OverallStats(**repository.overall_stats(query))

    # Con la búsqueda ya acotada a una ciudad o a unos barrios, cortar por
    # ciudad devuelve una fila que repite la cabecera. El mismo agregado por
    # barrio es lo que responde a "¿dónde dentro de Madrid sale a cuenta?".
    by_neighbourhood = query.zone is not None or bool(query.neighbourhoods)

    # overall already counted the rows; reuse it to pick the zone strategy.
    by_zone = [
        ZoneStats(**row)
        for row in repository.zone_stats(
            query, total=overall.count, by_neighbourhood=by_neighbourhood
        )
    ]
    distribution = _price_distribution(repository, query, overall, query.bins)

    return StatsResponse(
        overall=overall,
        by_zone=by_zone,
        by_zone_is_neighbourhood=by_neighbourhood,
        by_rooms=[Bucket(**row) for row in repository.by_rooms(query)],
        by_size=[Bucket(**row) for row in repository.by_size(query)],
        by_distance=[Bucket(**row) for row in repository.by_distance(query)],
        amenities=[AmenityImpact(**row) for row in repository.amenity_impact(query)],
        price_distribution=distribution,
    )
