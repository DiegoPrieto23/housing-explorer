"""Load the exported price model and estimate what a property is worth.

The notebook trains the model; this module is the only place that knows how to
*use* it. Keeping the contract here rather than in prose means a change to the
feature layout breaks a test instead of silently returning wrong numbers -- and
a model fed columns in the wrong order does return numbers, just wrong ones.

Serving needs scikit-learn (to unpickle the estimator) and pandas (to build the
frame it expects), which the API does not otherwise depend on. They live in the
``serving`` extra and are imported lazily, so importing this module in an
environment without them costs nothing and fails only when someone actually asks
for a prediction:

    pip install -e "backend[serving]"

    model = PriceModel.load()
    model.estimate(Property(city="Madrid", latitude=40.42, longitude=-3.70,
                            size_m2=90, rooms=3, bathrooms=2))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

#: What the notebook writes, and what the API reads.
MODEL_FILENAME = "price_model.joblib"


class PriceModelUnavailable(RuntimeError):
    """The model file is missing, or the libraries needed to run it are not installed."""


def default_model_path() -> Path:
    return BACKEND_DIR / "models" / MODEL_FILENAME


@dataclass
class Property:
    """The characteristics an estimate can be asked for.

    Only city, coordinates and size are required; the model handles missing
    values natively, so an unknown floor or construction year is left as
    ``None`` rather than guessed at. That is the honest encoding: "not stated"
    is a different thing from "ground floor".
    """

    city: str
    latitude: float
    longitude: float
    size_m2: float

    rooms: int | None = None
    bathrooms: int | None = None
    floor: int | None = None
    construction_year: int | None = None
    distance_to_city_center_km: float | None = None
    distance_to_metro_km: float | None = None

    #: Any of the dataset's HAS*/IS* flags, e.g. {"HASLIFT": 1, "HASTERRACE": 0}.
    features: dict[str, int] = field(default_factory=dict)

    def to_columns(self) -> dict[str, Any]:
        """Map the friendly names onto the dataset's column names."""
        return {
            "CONSTRUCTEDAREA": self.size_m2,
            "ROOMNUMBER": self.rooms,
            "BATHNUMBER": self.bathrooms,
            "FLOORCLEAN": self.floor,
            "CONSTRUCTIONYEAR": self.construction_year,
            "CADCONSTRUCTIONYEAR": self.construction_year,
            "DISTANCE_TO_CITY_CENTER": self.distance_to_city_center_km,
            "DISTANCE_TO_METRO": self.distance_to_metro_km,
            "LATITUDE": self.latitude,
            "LONGITUDE": self.longitude,
            "CITY": self.city,
            **self.features,
        }


class PriceModel:
    """The trained model plus everything needed to feed it correctly."""

    def __init__(self, bundle: dict[str, Any]) -> None:
        self._bundle = bundle
        self._model = bundle["modelo"]
        self._kmeans = bundle["kmeans"]
        self._columns: list[str] = bundle["variables"]
        self._categories: dict[str, list[Any]] = bundle["categorias"]

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str | None = None) -> PriceModel:
        """Read the bundle from disk. Call once at startup, not per request."""
        target = Path(path) if path is not None else default_model_path()
        if not target.is_file():
            raise PriceModelUnavailable(
                f"No existe {target}. Ejecuta notebooks/analisis.ipynb para generarlo."
            )
        try:
            import joblib
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise PriceModelUnavailable(
                'Faltan las dependencias de inferencia: pip install -e "backend[serving]"'
            ) from exc

        return cls(joblib.load(target))

    # -- metadata ----------------------------------------------------------

    @property
    def metrics(self) -> dict[str, dict[str, float]]:
        """Test-set metrics of every model the notebook compared."""
        return self._bundle["metricas"]

    @property
    def trained_on(self) -> dict[str, Any]:
        return self._bundle["entrenado_con"]

    @property
    def cities(self) -> list[str]:
        """Cities the model knows. Anything else is extrapolation."""
        return list(self._categories["CITY"])

    # -- inference ---------------------------------------------------------

    def _frame(self, properties: list[Property]) -> pd.DataFrame:
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise PriceModelUnavailable(
                'Faltan las dependencias de inferencia: pip install -e "backend[serving]"'
            ) from exc

        rows = [item.to_columns() for item in properties]
        frame = pd.DataFrame(rows)

        # Every column the model saw in training must be present, in the same
        # order. Absent flags default to 0 (the listing does not have it);
        # absent measurements stay NaN, which the model handles natively.
        for column in self._columns:
            if column not in frame.columns:
                frame[column] = 0 if column.startswith(("HAS", "IS", "BUILTTYPEID")) else None

        # BARRIO is derived, not given: the same k-means that produced the
        # training zones has to place a new property in one of them. It is given
        # a named frame, not an array, because that is how it was fitted --
        # scikit-learn warns about the mismatch otherwise.
        frame["BARRIO"] = self._kmeans.predict(frame[["LATITUDE", "LONGITUDE"]])

        for column, categories in self._categories.items():
            frame[column] = pd.Categorical(frame[column], categories=categories)

        return frame[self._columns]

    def estimate_raw(self, frame: pd.DataFrame):
        """Estimate straight from a dataset-shaped frame, for batch scoring.

        `estimate` is the friendly, one-property-at-a-time door; this is the one
        the scoring job uses, because it already holds the source rows with the
        original column names and going through `Property` would throw away the
        30 variables that make the estimate worth having.

        Returns a numpy array of euros, aligned with ``frame``.
        """
        import numpy as np
        import pandas as pd

        prepared = pd.DataFrame(index=frame.index)
        for column in self._columns:
            if column in frame.columns:
                prepared[column] = frame[column]
            else:
                # Same convention as `_frame`: an absent flag means the listing
                # does not have it; an absent measurement is genuinely unknown
                # and stays NaN for the model to handle.
                prepared[column] = 0 if column.startswith(("HAS", "IS", "BUILTTYPEID")) else np.nan

        prepared["BARRIO"] = self._kmeans.predict(frame[["LATITUDE", "LONGITUDE"]])
        for column, categories in self._categories.items():
            prepared[column] = pd.Categorical(prepared[column], categories=categories)

        return np.exp(self._model.predict(prepared[self._columns]))

    def estimate_many(self, properties: list[Property]) -> list[float]:
        """Estimated asking price in euros, one per property."""
        if not properties:
            return []

        import numpy as np

        # The model predicts log(price); see `objetivo` in the bundle.
        return [float(value) for value in np.exp(self._model.predict(self._frame(properties)))]

    def estimate(self, property: Property) -> float:
        return self.estimate_many([property])[0]
