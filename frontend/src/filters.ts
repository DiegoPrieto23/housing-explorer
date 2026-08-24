/** The single filter state both views read from, and its URL encoding.
 *
 * Kept out of the components so that "the list and the map show the same
 * filtered set" is a property of the code rather than a thing to remember: the
 * two views receive the same object and turn it into the same query string.
 */

import type { Amenity, Condition, Operation, PropertyType } from "./types/listing";

/** Geographic bounds of the visible map, in the order the API expects. */
export interface BoundingBox {
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
}

export interface Filters {
  priceMin: number | null;
  priceMax: number | null;
  sizeMin: number | null;
  sizeMax: number | null;
  /** Minimum, not exact: "3+ habitaciones" is what a search panel means. */
  roomsMin: number | null;
  operation: Operation | null;
  propertyType: PropertyType | null;
  zone: string | null;
  /** Set while the map is the active view and "buscar en el área" is on. */
  bbox: BoundingBox | null;
  bathroomsMin: number | null;
  /** Planta mínima. 0 es bajo, así que 1 significa "sin bajos". */
  floorMin: number | null;
  yearMin: number | null;
  condition: Condition | null;
  /** Todos los extras marcados son obligatorios, no opcionales. */
  amenities: Amenity[];
  centerMaxKm: number | null;
  metroMaxKm: number | null;
  /** Only listings priced well below the model's estimate. */
  bargainsOnly: boolean;
  /**
   * Area drawn by hand on the map, as `lat,lon;lat,lon;...`.
   *
   * Independent of `bbox`: the drawing stays put while the map is panned, which
   * is the whole point of drawing it. The two combine when both are set.
   */
  polygon: string | null;
}

export const EMPTY_FILTERS: Filters = {
  priceMin: null,
  priceMax: null,
  sizeMin: null,
  sizeMax: null,
  roomsMin: null,
  operation: null,
  propertyType: null,
  zone: null,
  bbox: null,
  polygon: null,
  bathroomsMin: null,
  floorMin: null,
  yearMin: null,
  condition: null,
  amenities: [],
  centerMaxKm: null,
  metroMaxKm: null,
  bargainsOnly: false,
};

/** How many filters the user has set. Drives the "limpiar (n)" button. */
export function activeFilterCount(filters: Filters): number {
  // bbox is the map viewport, not a choice the user made; the drawn polygon is.
  // Booleans and lists need their own test: `[] !== null` is true, and an empty
  // list of amenities is not a filter anyone set.
  return (Object.keys(EMPTY_FILTERS) as (keyof Filters)[]).reduce((total, key) => {
    if (key === "bbox") return total;
    if (key === "bargainsOnly") return total + (filters.bargainsOnly ? 1 : 0);
    // Cada extra cuenta por separado, no la lista como uno solo: el panel
    // enseña el número de extras marcados junto a este, y dos contadores que
    // dicen cosas distintas de lo mismo se leen como un error.
    if (key === "amenities") return total + filters.amenities.length;
    return total + (filters[key] !== null ? 1 : 0);
  }, 0);
}

/**
 * Filters -> query string, with the Spanish parameter names the API documents.
 *
 * Nulls are dropped rather than sent empty: the backend forbids unknown
 * parameters and validates the ones it knows, so an empty `precio_min=` would
 * come back as a 422 instead of "no minimum".
 */
export function toSearchParams(filters: Filters): URLSearchParams {
  const params = new URLSearchParams();
  const set = (key: string, value: number | string | null) => {
    if (value !== null && value !== "") params.set(key, String(value));
  };

  set("precio_min", filters.priceMin);
  set("precio_max", filters.priceMax);
  set("m2_min", filters.sizeMin);
  set("m2_max", filters.sizeMax);
  set("habitaciones_min", filters.roomsMin);
  set("tipo_operacion", filters.operation);
  set("tipo_inmueble", filters.propertyType);
  set("zona", filters.zone);
  set("poligono", filters.polygon);
  set("banos_min", filters.bathroomsMin);
  set("planta_min", filters.floorMin);
  set("anio_min", filters.yearMin);
  set("estado", filters.condition);
  set("centro_max_km", filters.centerMaxKm);
  set("metro_max_km", filters.metroMaxKm);
  // Repetido, no separado por comas: es como el backend declara la lista, y
  // evita tener que elegir un separador que ningún valor pueda contener.
  for (const amenity of filters.amenities) params.append("extras", amenity);
  if (filters.bargainsOnly) params.set("solo_chollos", "true");

  if (filters.bbox) {
    // All four or none: a partial box is a 422.
    set("lat_min", filters.bbox.lat_min);
    set("lat_max", filters.bbox.lat_max);
    set("lon_min", filters.bbox.lon_min);
    set("lon_max", filters.bbox.lon_max);
  }

  return params;
}

/** Stable identity for the effects that refetch when the filters change. */
export function filtersKey(filters: Filters): string {
  const params = toSearchParams(filters);
  params.sort();
  return params.toString();
}
