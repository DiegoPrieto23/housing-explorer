"""The exported model is only useful if it can be loaded and fed correctly.

These tests are the reason the feature contract lives in `app.pricing` and not
in a comment: they fail if the notebook changes the column layout without the
loader following.
"""

from __future__ import annotations

import pytest

from app.pricing import PriceModel, PriceModelUnavailable, Property, default_model_path

pytest.importorskip("sklearn", reason="la inferencia necesita el extra [serving]")
pytest.importorskip("pandas", reason="la inferencia necesita el extra [serving]")

pytestmark = pytest.mark.skipif(
    not default_model_path().is_file(),
    reason="no hay modelo exportado; ejecuta notebooks/analisis.ipynb",
)

#: Roughly the centre of Madrid, Barcelona and Valencia.
CENTROS = {
    "Madrid": (40.4168, -3.7038),
    "Barcelona": (41.3874, 2.1686),
    "Valencia": (39.4699, -0.3763),
}


@pytest.fixture(scope="module")
def model() -> PriceModel:
    return PriceModel.load()


def piso(city: str = "Madrid", **overrides: object) -> Property:
    latitude, longitude = CENTROS[city]
    defaults: dict[str, object] = {
        "city": city,
        "latitude": latitude,
        "longitude": longitude,
        "size_m2": 90.0,
        "rooms": 3,
        "bathrooms": 2,
        "floor": 3,
        "construction_year": 1980,
        "distance_to_city_center_km": 1.5,
        "distance_to_metro_km": 0.3,
    }
    defaults.update(overrides)
    return Property(**defaults)  # type: ignore[arg-type]


def test_missing_model_says_how_to_build_it(tmp_path) -> None:
    with pytest.raises(PriceModelUnavailable, match="analisis.ipynb"):
        PriceModel.load(tmp_path / "no-existe.joblib")


def test_metadata_travels_with_the_model(model: PriceModel) -> None:
    assert sorted(model.cities) == ["Barcelona", "Madrid", "Valencia"]
    assert model.trained_on["filas"] > 100_000
    # The metrics are what justify using it at all; they must be readable.
    assert model.metrics["Gradient boosting"]["R² (log)"] > 0.9


def test_estimate_returns_a_plausible_price(model: PriceModel) -> None:
    price = model.estimate(piso())
    assert 50_000 < price < 2_000_000


def test_only_city_size_and_position_are_required(model: PriceModel) -> None:
    """Everything else is optional: unknown must not mean zero."""
    minimo = Property(city="Madrid", latitude=40.4168, longitude=-3.7038, size_m2=90)
    assert 50_000 < model.estimate(minimo) < 2_000_000


def test_bigger_is_worth_more(model: PriceModel) -> None:
    pequeno = model.estimate(piso(size_m2=50, rooms=1, bathrooms=1))
    grande = model.estimate(piso(size_m2=150, rooms=4, bathrooms=2))
    assert grande > pequeno


def test_the_same_flat_is_worth_less_in_valencia(model: PriceModel) -> None:
    """§2 of the notebook: Valencia sits at ~38% of Barcelona's price per m2."""
    barcelona = model.estimate(piso("Barcelona"))
    valencia = model.estimate(piso("Valencia"))
    assert valencia < barcelona


def test_estimates_are_deterministic(model: PriceModel) -> None:
    assert model.estimate(piso()) == model.estimate(piso())


def test_batch_matches_one_by_one(model: PriceModel) -> None:
    lote = [piso("Madrid"), piso("Barcelona"), piso("Valencia")]
    assert model.estimate_many(lote) == [model.estimate(item) for item in lote]


def test_empty_batch_is_empty(model: PriceModel) -> None:
    assert model.estimate_many([]) == []


def test_optional_flags_are_accepted(model: PriceModel) -> None:
    con = model.estimate(piso(features={"HASLIFT": 1, "HASPARKINGSPACE": 1}))
    sin = model.estimate(piso(features={"HASLIFT": 0, "HASPARKINGSPACE": 0}))
    assert con != sin
