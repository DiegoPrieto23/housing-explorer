/**
 * El contrato de la capa de datos, independiente de quién lo cumpla.
 *
 * Hay dos implementaciones —FastAPI por HTTP y el motor estático dentro del
 * navegador— y esto es lo que las dos tienen que respetar. Vive en su propio
 * módulo, y no en una de ellas, para que la otra no tenga que importar de su
 * hermana solo para saber cómo se llama una ordenación.
 */

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
import type { Filters } from "../filters";

/** Thrown for any non-2xx, carrying the message the server put in the body. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface PageRequest {
  limit: number;
  offset: number;
}

/** How a page of listings is sorted. Mirrors the API's `orden`. */
export type Order = "reciente" | "precio" | "precio_desc" | "desviacion";

/**
 * Todo lo que la aplicación pide a sus datos.
 *
 * Las firmas llevan `AbortSignal` porque la interfaz cancela: arrastrar un
 * deslizador dispara una petición por cambio, y la penúltima no interesa en
 * cuanto sale la última.
 */
export interface DataClient {
  fetchListings(
    filters: Filters,
    page: PageRequest,
    order?: Order,
    signal?: AbortSignal,
  ): Promise<ListingPage>;

  fetchListingsByIds(
    globalIds: string[],
    page: PageRequest,
    signal?: AbortSignal,
  ): Promise<ListingPage>;

  fetchStats(filters: Filters, bins: number, signal?: AbortSignal): Promise<Stats>;

  fetchMapData(
    filters: Filters,
    zoom: number,
    heat?: boolean,
    signal?: AbortSignal,
  ): Promise<MapData>;

  fetchFacets(signal?: AbortSignal): Promise<Facets>;
  fetchListing(id: string, signal?: AbortSignal): Promise<Listing>;
  fetchSources(signal?: AbortSignal): Promise<SourceStatus[]>;
  fetchNeighbourhoods(signal?: AbortSignal): Promise<NeighbourhoodCollection>;
  fetchPointsOfInterest(signal?: AbortSignal): Promise<PoiCollection>;
}
