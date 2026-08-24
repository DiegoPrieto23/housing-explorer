import { useState } from "react";

import { activeFilterCount, EMPTY_FILTERS, type Filters } from "../filters";
import { count, shortEuros } from "../format";
import type { Amenity, Condition, Facets, Operation, PropertyType } from "../types/listing";
import { AMENITY_LABELS, CONDITION_LABELS } from "../types/listing";

interface FilterPanelProps {
  filters: Filters;
  onChange: (filters: Filters | ((previous: Filters) => Filters)) => void;
  /** Zones are separate: picking one also flies the map to that city. */
  onZoneChange: (zone: string | null) => void;
  onClearPolygon: () => void;
  facets: Facets | null;
  /** Anuncios que cumplen los filtros actuales, o null mientras se cuenta. */
  total: number | null;
  loading: boolean;
  /** Only meaningful on the map view; hidden elsewhere. */
  searchInView: boolean;
  onSearchInViewChange: (value: boolean) => void;
  showSearchInView: boolean;
}

const ROOM_OPTIONS = [1, 2, 3, 4, 5];
const BATH_OPTIONS = [1, 2, 3];

/** Distancias en km que ofrece el desplegable, con su etiqueta. */
const DISTANCES: { value: number; label: string }[] = [
  { value: 0.5, label: "500 m" },
  { value: 1, label: "1 km" },
  { value: 2, label: "2 km" },
  { value: 3, label: "3 km" },
  { value: 5, label: "5 km" },
];

/** Lo mismo para el metro, donde las distancias útiles son mucho más cortas. */
const METRO_DISTANCES: { value: number; label: string }[] = [
  { value: 0.25, label: "250 m" },
  { value: 0.5, label: "500 m" },
  { value: 1, label: "1 km" },
];

/** Number inputs return "" when cleared; the API wants an absent parameter. */
function toNumber(value: string): number | null {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export default function FilterPanel({
  filters,
  onChange,
  onZoneChange,
  onClearPolygon,
  facets,
  total,
  loading,
  searchInView,
  onSearchInViewChange,
  showSearchInView,
}: FilterPanelProps) {
  /*
   * Funcional a propósito. Con `{ ...filters, [key]: value }` dos clics seguidos
   * en dos fichas leen el mismo `filters` del render en que se pintaron, y el
   * segundo borra lo que hizo el primero.
   */
  const update = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    onChange((previous) => ({ ...previous, [key]: value }));

  const active = activeFilterCount(filters);

  /*
   * Los filtros de la sección plegable, contados aparte. Sin esto, marcar
   * "ascensor" y luego plegar la sección deja un filtro activo que no se ve por
   * ningún sitio, y el usuario se queda mirando por qué faltan resultados.
   */
  const advanced =
    (filters.bathroomsMin !== null ? 1 : 0) +
    (filters.floorMin !== null ? 1 : 0) +
    (filters.yearMin !== null ? 1 : 0) +
    (filters.condition !== null ? 1 : 0) +
    (filters.centerMaxKm !== null ? 1 : 0) +
    (filters.metroMaxKm !== null ? 1 : 0) +
    filters.amenities.length;

  // Abierta de entrada si ya hay algo dentro, para que nunca haya un filtro
  // activo escondido detrás de un triángulo.
  const [showMore, setShowMore] = useState(advanced > 0);

  return (
    <form className="filters" onSubmit={(event) => event.preventDefault()}>
      <header className="filters__header">
        <div>
          <h1>Housing Explorer</h1>
          <p className="muted">
            {total === null ? "…" : `${count(total)} anuncios`}
            {loading ? " · actualizando" : ""}
          </p>
        </div>
        <button
          type="button"
          className="button button--ghost"
          disabled={active === 0}
          onClick={() => onChange((previous) => ({ ...EMPTY_FILTERS, bbox: previous.bbox }))}
        >
          Limpiar{active ? ` (${active})` : ""}
        </button>
      </header>

      <fieldset className="field">
        <legend>Operación</legend>
        <div className="segmented">
          <button
            type="button"
            className={filters.operation === null ? "is-active" : ""}
            onClick={() => update("operation", null)}
          >
            Todas
          </button>
          {(facets?.operations ?? []).map((option) => (
            <button
              key={option.value}
              type="button"
              className={filters.operation === option.value ? "is-active" : ""}
              onClick={() => update("operation", option.value as Operation)}
            >
              {option.value === "venta" ? "Venta" : "Alquiler"}
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset className="field">
        <legend>Dónde buscar</legend>
        <select
          value={filters.zone ?? ""}
          onChange={(event) => onZoneChange(event.target.value || null)}
          aria-label="Ciudad"
        >
          <option value="">Toda España</option>
          {(facets?.zones ?? []).map((zone) => (
            <option key={zone.value} value={zone.value}>
              {zone.value} ({count(zone.count)})
            </option>
          ))}
        </select>

        {filters.polygon ? (
          <p className="drawn">
            <span>✏ Buscando en la zona dibujada</span>
            <button type="button" className="button button--ghost" onClick={onClearPolygon}>
              Quitar
            </button>
          </p>
        ) : (
          <p className="muted">
            O dibuja el área que te interesa con <strong>Dibujar zona</strong>, sobre el mapa.
          </p>
        )}
      </fieldset>

      <fieldset className="field">
        <legend>
          Precio
          {facets?.price_min != null && facets.price_max != null ? (
            <span className="muted">
              {" "}
              {shortEuros(facets.price_min)} – {shortEuros(facets.price_max)}
            </span>
          ) : null}
        </legend>
        <div className="range">
          <label>
            <span className="muted">Desde</span>
            <input
              type="number"
              inputMode="numeric"
              min={0}
              step={10000}
              placeholder="mín."
              value={filters.priceMin ?? ""}
              onChange={(event) => update("priceMin", toNumber(event.target.value))}
            />
          </label>
          <label>
            <span className="muted">Hasta</span>
            <input
              type="number"
              inputMode="numeric"
              min={0}
              step={10000}
              placeholder="máx."
              value={filters.priceMax ?? ""}
              onChange={(event) => update("priceMax", toNumber(event.target.value))}
            />
          </label>
        </div>
      </fieldset>

      <fieldset className="field">
        <legend>Superficie (m²)</legend>
        <div className="range">
          <label>
            <span className="muted">Desde</span>
            <input
              type="number"
              inputMode="numeric"
              min={0}
              step={10}
              placeholder="mín."
              value={filters.sizeMin ?? ""}
              onChange={(event) => update("sizeMin", toNumber(event.target.value))}
            />
          </label>
          <label>
            <span className="muted">Hasta</span>
            <input
              type="number"
              inputMode="numeric"
              min={0}
              step={10}
              placeholder="máx."
              value={filters.sizeMax ?? ""}
              onChange={(event) => update("sizeMax", toNumber(event.target.value))}
            />
          </label>
        </div>
      </fieldset>

      <fieldset className="field">
        <legend>Habitaciones (mínimo)</legend>
        <div className="segmented">
          <button
            type="button"
            className={filters.roomsMin === null ? "is-active" : ""}
            onClick={() => update("roomsMin", null)}
          >
            Todas
          </button>
          {ROOM_OPTIONS.map((value) => (
            <button
              key={value}
              type="button"
              className={filters.roomsMin === value ? "is-active" : ""}
              // Clicking the active option clears it: no dead-end selections.
              onClick={() => update("roomsMin", filters.roomsMin === value ? null : value)}
            >
              {value}+
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset className="field">
        <legend>Tipo de inmueble</legend>
        <select
          value={filters.propertyType ?? ""}
          onChange={(event) =>
            update("propertyType", (event.target.value || null) as PropertyType | null)
          }
        >
          <option value="">Todos</option>
          {(facets?.property_types ?? []).map((type) => (
            <option key={type.value} value={type.value}>
              {type.value.charAt(0).toUpperCase() + type.value.slice(1)} ({count(type.count)})
            </option>
          ))}
        </select>
      </fieldset>

      <fieldset className="field">
        <legend>
          <button
            type="button"
            className="field__toggle"
            aria-expanded={showMore}
            onClick={() => setShowMore((open) => !open)}
          >
            {showMore ? "▾" : "▸"} Más filtros
            {advanced > 0 ? <span className="field__badge">{advanced}</span> : null}
          </button>
        </legend>

        {showMore ? (
          <div className="more">
            <label className="more__row">
              <span className="muted">Baños (mínimo)</span>
              <div className="segmented">
                <button
                  type="button"
                  className={filters.bathroomsMin === null ? "is-active" : ""}
                  onClick={() => update("bathroomsMin", null)}
                >
                  Todos
                </button>
                {BATH_OPTIONS.map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={filters.bathroomsMin === value ? "is-active" : ""}
                    onClick={() =>
                      update("bathroomsMin", filters.bathroomsMin === value ? null : value)
                    }
                  >
                    {value}+
                  </button>
                ))}
              </div>
            </label>

            <label className="more__row">
              <span className="muted">Estado</span>
              <select
                value={filters.condition ?? ""}
                onChange={(event) =>
                  update("condition", (event.target.value || null) as Condition | null)
                }
              >
                <option value="">Cualquiera</option>
                {(facets?.conditions ?? []).map((option) => (
                  <option key={option.value} value={option.value}>
                    {CONDITION_LABELS[option.value as Condition] ?? option.value} (
                    {count(option.count)})
                  </option>
                ))}
              </select>
            </label>

            <label className="more__row">
              <span className="muted">Construido desde</span>
              <input
                type="number"
                inputMode="numeric"
                min={facets?.year_min ?? 1500}
                max={facets?.year_max ?? 2100}
                step={10}
                placeholder="cualquier año"
                value={filters.yearMin ?? ""}
                onChange={(event) => update("yearMin", toNumber(event.target.value))}
              />
            </label>

            <label className="more__row">
              <span className="muted">Como mucho a…</span>
              <select
                value={filters.centerMaxKm ?? ""}
                onChange={(event) =>
                  update("centerMaxKm", event.target.value ? Number(event.target.value) : null)
                }
              >
                <option value="">cualquier distancia del centro</option>
                {DISTANCES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label} del centro
                  </option>
                ))}
              </select>
            </label>

            <label className="more__row">
              <span className="muted">Y del metro</span>
              <select
                value={filters.metroMaxKm ?? ""}
                onChange={(event) =>
                  update("metroMaxKm", event.target.value ? Number(event.target.value) : null)
                }
              >
                <option value="">cualquier distancia</option>
                {METRO_DISTANCES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label} de una boca de metro
                  </option>
                ))}
              </select>
            </label>

            <label className="checkbox checkbox--inline">
              <input
                type="checkbox"
                checked={filters.floorMin === 1}
                onChange={(event) => update("floorMin", event.target.checked ? 1 : null)}
              />
              <span>
                Sin bajos ni sótanos
                <span className="muted"> · el bajo se paga un 14 % menos por m²</span>
              </span>
            </label>

            <div className="chips" role="group" aria-label="Extras">
              {(facets?.amenities ?? []).map((option) => {
                const amenity = option.value as Amenity;
                const on = filters.amenities.includes(amenity);
                return (
                  <button
                    key={amenity}
                    type="button"
                    className={`chip${on ? " chip--on" : ""}`}
                    aria-pressed={on}
                    title={`${count(option.count)} anuncios lo tienen`}
                    onClick={() =>
                      onChange((previous) => ({
                        ...previous,
                        // Sobre `previous`, no sobre `filters`: marcar dos
                        // fichas seguidas tiene que sumar las dos.
                        // Todos los marcados son obligatorios: quitar uno es
                        // sacarlo de la lista, no invertir nada.
                        amenities: previous.amenities.includes(amenity)
                          ? previous.amenities.filter((item) => item !== amenity)
                          : [...previous.amenities, amenity],
                      }))
                    }
                  >
                    {AMENITY_LABELS[amenity] ?? amenity}
                  </button>
                );
              })}
            </div>
            {filters.amenities.length > 1 ? (
              <p className="muted more__note">
                Se piden <strong>todos</strong> los extras marcados, no cualquiera de ellos.
              </p>
            ) : null}
          </div>
        ) : null}
      </fieldset>

      <label className={`checkbox checkbox--deal${filters.bargainsOnly ? " is-on" : ""}`}>
        <input
          type="checkbox"
          checked={filters.bargainsOnly}
          onChange={(event) => update("bargainsOnly", event.target.checked)}
        />
        <span>
          ★ Solo chollos
          <span className="muted">
            {" "}
            · se pide un 25 % o más por debajo de lo que estima el modelo de precios
          </span>
        </span>
      </label>

      {showSearchInView ? (
        <label className="checkbox">
          <input
            type="checkbox"
            checked={searchInView}
            onChange={(event) => onSearchInViewChange(event.target.checked)}
          />
          <span>
            Buscar solo en el área visible
            <span className="muted"> · el mapa acota también la lista y las estadísticas</span>
          </span>
        </label>
      ) : null}
    </form>
  );
}
