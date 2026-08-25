/** Mirrors backend/app/models/. Keep both sides in sync. */

export type Operation = "venta" | "alquiler";

export type PropertyType =
  | "piso"
  | "casa"
  | "estudio"
  | "duplex"
  | "atico"
  | "habitacion"
  | "terreno"
  | "garaje"
  | "local"
  | "otro";

/** Extras que una vivienda tiene o no tiene. */
export type Amenity =
  | "ascensor"
  | "terraza"
  | "garaje"
  | "aire_acondicionado"
  | "piscina"
  | "portero"
  | "jardin"
  | "trastero"
  | "armarios";

export type Condition = "obra_nueva" | "buen_estado" | "a_reformar";

/** Etiquetas para pintar; el valor crudo ya viene en castellano pero sin acentos. */
export const AMENITY_LABELS: Record<Amenity, string> = {
  ascensor: "Ascensor",
  terraza: "Terraza",
  garaje: "Garaje",
  aire_acondicionado: "Aire ac.",
  piscina: "Piscina",
  portero: "Portero",
  jardin: "Jardín",
  trastero: "Trastero",
  armarios: "Armarios",
};

export const CONDITION_LABELS: Record<Condition, string> = {
  obra_nueva: "Obra nueva",
  buen_estado: "Buen estado",
  a_reformar: "A reformar",
};

export interface Listing {
  id: string;
  source: string;
  title: string;
  url: string | null;
  operation: Operation;
  property_type: PropertyType;
  price: number;
  size_m2: number | null;
  rooms: number | null;
  latitude: number | null;
  longitude: number | null;
  address: string | null;
  zone: string | null;
  /** `LOCATIONID` del barrio que lo contiene, o null si cae fuera de todos. */
  neighbourhood_id: string | null;
  neighbourhood: string | null;
  ingested_at: string;

  bathrooms: number | null;
  floor: number | null;
  year_built: number | null;
  condition: Condition | null;
  distance_to_center_km: number | null;
  distance_to_metro_km: number | null;
  amenities: Amenity[];

  /** Lo que el modelo estima que debería costar. Null = no se ha podido estimar. */
  expected_price: number | null;
  /**
   * Cuánto se aparta el precio pedido del estimado, en %.
   * **Negativo = más barato de lo esperado.**
   */
  price_deviation: number | null;
}

/**
 * Cuánto por debajo de la estimación hay que estar para llamarlo chollo.
 *
 * Tiene que coincidir con BARGAIN_THRESHOLD del backend. El número no es
 * arbitrario: el error mediano del modelo es del 10,3 % y su MAPE del 14,1 %,
 * así que dentro de un ±15 % no se distingue una ganga de una equivocación del
 * modelo. -25 % es unas 2,4 veces el error mediano.
 */
export const BARGAIN_THRESHOLD = -25;

/** Si el anuncio se pide bastante por debajo de lo que el modelo estima. */
export function isBargain(listing: { price_deviation: number | null }): boolean {
  return listing.price_deviation !== null && listing.price_deviation <= BARGAIN_THRESHOLD;
}

/** One page of `GET /listings`. `total` ignores the paging window. */
export interface ListingPage {
  items: Listing[];
  total: number;
  limit: number;
  offset: number;
}

export interface FacetValue {
  value: string;
  count: number;
}

/** A zone plus the box that contains it, so picking a city can fly the map. */
/**
 * Un barrio, tal y como lo necesita el selector de la izquierda.
 *
 * El identificador es el `LOCATIONID` del dataset y no el nombre porque los
 * nombres se repiten: hay un «Sant Antoni» en Barcelona y otro en Valencia.
 */
export interface NeighbourhoodFacet {
  id: string;
  name: string;
  city: string;
  count: number;
  /** Del polígono, no de dónde caigan sus anuncios: es su extensión real. */
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
}

export interface ZoneFacet extends FacetValue {
  lat_min: number | null;
  lat_max: number | null;
  lon_min: number | null;
  lon_max: number | null;
  /** Nombre de la zona cuando la agrupación fue por zona, no por rejilla. */
  zone: string | null;
  /** Los barrios de esta ciudad, por orden alfabético. */
  neighbourhoods: NeighbourhoodFacet[];
}

/** One listing as the map draws it. Not a full `Listing`: see MapData. */
export interface MapPoint {
  global_id: string;
  latitude: number;
  longitude: number;
  price: number;
  property_type: PropertyType;
  operation: Operation;
  price_deviation: number | null;
}

/** An aggregated grid cell: many listings counted rather than drawn. */
export interface MapCluster {
  latitude: number;
  longitude: number;
  count: number;
  avg_price: number | null;
  /** Media de €/m² de la celda. Null si nadie declara superficie. */
  avg_price_per_m2: number | null;
  with_size: number;
  /** Extensión real de la celda, para dibujarla como rectángulo. */
  lat_min: number | null;
  lat_max: number | null;
  lon_min: number | null;
  lon_max: number | null;
  /** Nombre de la zona cuando la agrupación fue por zona, no por rejilla. */
  zone: string | null;
}

/**
 * `GET /listings/map`. `total` is always exact.
 *
 * When the matches fit, they arrive one by one in `points`; when they do not,
 * they arrive counted into `clusters`. Nothing is ever truncated, which is why
 * there is no `limit` here.
 */
export interface MapData {
  mode: "points" | "clusters";
  total: number;
  points: MapPoint[];
  clusters: MapCluster[];
}

/** `GET /listings/facets`: the options and bounds the filter panel offers. */
export interface Facets {
  total: number;
  zones: ZoneFacet[];
  operations: FacetValue[];
  property_types: FacetValue[];
  price_min: number | null;
  price_max: number | null;
  size_min: number | null;
  size_max: number | null;
  rooms_min: number | null;
  rooms_max: number | null;
  amenities: FacetValue[];
  conditions: FacetValue[];
  bathrooms_min: number | null;
  bathrooms_max: number | null;
  floor_min: number | null;
  floor_max: number | null;
  year_min: number | null;
  year_max: number | null;
  center_max_km: number | null;
  metro_max_km: number | null;
}

export interface OverallStats {
  count: number;
  avg_price: number | null;
  min_price: number | null;
  max_price: number | null;
  avg_price_per_m2: number | null;
  p25_price: number | null;
  median_price: number | null;
  p75_price: number | null;
  p90_price: number | null;
  p99_price: number | null;
}

export interface ZoneStats {
  /** Nombre de la ciudad o del barrio, según cómo esté cortada la tabla. */
  zone: string;
  /** `LOCATIONID` cuando la fila es un barrio; null cuando es una ciudad. */
  neighbourhood_id: string | null;
  count: number;
  avg_price: number;
  median_price: number | null;
  min_price: number;
  max_price: number;
  avg_price_per_m2: number | null;
}

/** One bar of the histogram. `upper` is null on the open-ended last bucket. */
export interface PriceBucket {
  lower: number;
  upper: number | null;
  count: number;
}

/** Una barra de una distribución: un tramo de habitaciones o de superficie. */
export interface Bucket {
  bucket: number;
  label: string;
  count: number;
  avg_price: number;
  avg_price_per_m2: number;
}

/** Lo que cuesta el m² con y sin cada extra. Correlación, no valoración. */
export interface AmenityImpact {
  amenity: Amenity;
  count: number;
  share: number;
  with_it: number;
  without_it: number;
  difference: number;
}

export interface Stats {
  overall: OverallStats;
  by_zone: ZoneStats[];
  /** Si `by_zone` viene cortado por barrio en vez de por ciudad. */
  by_zone_is_neighbourhood: boolean;
  by_rooms: Bucket[];
  by_size: Bucket[];
  by_distance: Bucket[];
  amenities: AmenityImpact[];
  price_distribution: PriceBucket[];
}

export interface SourceStatus {
  name: string;
  healthy: boolean;
  listings: number;
}

/* -------------------------------------------------------------------------
 * Geografía fija: los barrios y los puntos de interés del dataset.
 *
 * Va tipada sobre `GeoJSON.FeatureCollection`, que llega con @types/leaflet,
 * en vez de con interfaces propias. Así lo que sale de `fetch` es exactamente
 * lo que `L.geoJSON` acepta, sin conversión ni casts por el camino.
 * ---------------------------------------------------------------------- */

/** Un barrio del dataset: el LOCATIONID original, su nombre y su ciudad. */
export interface NeighbourhoodProperties {
  location_id: string;
  name: string;
  city: string;
}

export type NeighbourhoodCollection = GeoJSON.FeatureCollection<
  GeoJSON.MultiPolygon,
  NeighbourhoodProperties
>;

/** Los tres tipos de punto de interés, cada uno con su forma en el mapa. */
export type PoiKind = "centro" | "metro" | "calle";

export interface PoiProperties {
  kind: PoiKind;
  city: string;
  /** El metro no lo trae: el dataset da coordenadas, no nombres de estación. */
  name?: string;
}

export type PoiCollection = GeoJSON.FeatureCollection<
  GeoJSON.Point | GeoJSON.LineString,
  PoiProperties
>;

/** The key storage deduplicates on, and what React needs for list keys. */
export function globalId(listing: Listing): string {
  return `${listing.source}:${listing.id}`;
}
