import { toSearchParams, type Filters } from "../filters";
import type {
  Facets,
  Listing,
  ListingPage,
  MapData,
  NeighbourhoodCollection,
  PoiCollection,
  SourceStatus,
  Stats,
} from "../types/listing";

/** Empty by default: the Vite dev server proxies /api to FastAPI. */
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

/** Thrown for any non-2xx, carrying the message FastAPI put in the body. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${BASE_URL}/api${path}`, { signal });

  if (!response.ok) {
    throw new ApiError(response.status, await describe(response));
  }
  return (await response.json()) as T;
}

/**
 * The most useful sentence in an error body.
 *
 * A 422 from a Pydantic query model arrives as `detail: [{msg, loc}, ...]`; a
 * deliberate `HTTPException` as `detail: "..."`. Both are worth showing; the
 * raw JSON is not.
 */
async function describe(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    const { detail } = body;

    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const messages = detail
        .map((entry) => (entry as { msg?: string }).msg)
        .filter((message): message is string => Boolean(message));
      if (messages.length) return messages.join("; ");
    }
  } catch {
    // Not JSON, or an empty body: fall through to the status line.
  }
  return `${response.status} ${response.statusText}`;
}

function withFilters(path: string, filters: Filters, extra: Record<string, number> = {}): string {
  const params = toSearchParams(filters);
  for (const [key, value] of Object.entries(extra)) params.set(key, String(value));
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

export interface PageRequest {
  limit: number;
  offset: number;
}

/** How a page of listings is sorted. Mirrors the API's `orden`. */
export type Order = "reciente" | "precio" | "precio_desc" | "desviacion";

export function fetchListings(
  filters: Filters,
  page: PageRequest,
  order: Order = "reciente",
  signal?: AbortSignal,
): Promise<ListingPage> {
  const path = withFilters("/listings", filters, { ...page });
  return request<ListingPage>(`${path}${path.includes("?") ? "&" : "?"}orden=${order}`, signal);
}

export function fetchStats(
  filters: Filters,
  bins: number,
  signal?: AbortSignal,
): Promise<Stats> {
  return request<Stats>(withFilters("/stats", filters, { intervalos: bins }), signal);
}

/**
 * Everything the map should draw, at the resolution the zoom can take.
 *
 * There is no page size on purpose: the server decides between individual
 * markers and aggregated cells based on how many listings match, so the map
 * never has to admit it is hiding some of them.
 */
export function fetchMapData(
  filters: Filters,
  zoom: number,
  heat = false,
  signal?: AbortSignal,
): Promise<MapData> {
  const path = withFilters("/listings/map", filters, { zoom });
  return request<MapData>(heat ? `${path}&calor=true` : path, signal);
}

/**
 * The listings behind a set of favourites.
 *
 * Favourites live in localStorage, so the only thing the browser can tell the
 * API is "these ids". Asking for them one by one would be one request per
 * favourite; `ids` is repeatable so it is one request for all of them.
 */
export function fetchListingsByIds(
  globalIds: string[],
  page: PageRequest,
  signal?: AbortSignal,
): Promise<ListingPage> {
  const params = new URLSearchParams();
  for (const id of globalIds) params.append("ids", id);
  params.set("limit", String(page.limit));
  params.set("offset", String(page.offset));
  return request<ListingPage>(`/listings?${params.toString()}`, signal);
}

export function fetchFacets(signal?: AbortSignal): Promise<Facets> {
  return request<Facets>("/listings/facets", signal);
}

export function fetchListing(id: string, signal?: AbortSignal): Promise<Listing> {
  return request<Listing>(`/listings/${encodeURIComponent(id)}`, signal);
}

export function fetchSources(signal?: AbortSignal): Promise<SourceStatus[]> {
  return request<SourceStatus[]>("/sources", signal);
}

/**
 * Los polígonos de barrio, en GeoJSON, tal cual los quiere `L.geoJSON`.
 *
 * Sin filtros y sin paginar: son 277 barrios que no cambian nunca, así que se
 * piden una vez al arrancar y ya. El servidor los manda comprimidos (279 kB
 * pasan a 67) y con `Cache-Control`, de modo que una recarga no vuelve a
 * traerlos.
 */
export function fetchNeighbourhoods(signal?: AbortSignal): Promise<NeighbourhoodCollection> {
  return request<NeighbourhoodCollection>("/neighbourhoods", signal);
}

/** El centro de cada ciudad, sus bocas de metro y su calle principal. */
export function fetchPointsOfInterest(signal?: AbortSignal): Promise<PoiCollection> {
  return request<PoiCollection>("/points-of-interest", signal);
}
