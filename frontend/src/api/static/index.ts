/**
 * El cliente estático: la misma API, sin servidor detrás.
 *
 * Cumple exactamente el mismo `DataClient` que el cliente HTTP, así que la
 * aplicación no sabe cuál de los dos tiene enchufado. Lo que cambia es de dónde
 * salen las respuestas: en vez de una petición a FastAPI, un mensaje al worker
 * que tiene los 149.923 anuncios en memoria.
 *
 * Esto es lo que permite publicar el visor en GitHub Pages. El conjunto de datos
 * es una foto de 2018 que no va a cambiar, así que no hay nada que un servidor
 * pueda responder que el navegador no pueda responderse solo — y sin servidor no
 * hay nada que se caiga, que caduque ni que haya que pagar.
 */

import type { Filters } from "../../filters";
import type {
  Facets,
  Listing,
  ListingPage,
  MapData,
  NeighbourhoodCollection,
  PoiCollection,
  SourceStatus,
  Stats,
} from "../../types/listing";
import { ApiError, type DataClient, type Order, type PageRequest } from "../types";
import { fetchJson } from "./payload";
import type { Request, RequestBody, Response } from "./protocol";
import { BARGAIN_THRESHOLD, type QueryFilters } from "./query";

/**
 * Dónde están los ficheros compilados.
 *
 * Sobre `import.meta.env.BASE_URL` y no sobre la raíz: en GitHub Pages la web
 * cuelga de `/housing-explorer/`, y una ruta absoluta buscaría los datos en el
 * dominio en vez de en el proyecto.
 */
const DATA_BASE = `${import.meta.env.BASE_URL}data/`;

/**
 * Traduce los filtros de la interfaz a los que el motor entiende.
 *
 * Es el equivalente del `model_validator` de Pydantic, y hace lo mismo que él:
 * resolver `solo_chollos` a un umbral numérico **una vez**, aquí, para que nadie
 * más abajo tenga que saber que la casilla existe.
 */
function normalize(filters: Filters): QueryFilters {
  return {
    priceMin: filters.priceMin,
    priceMax: filters.priceMax,
    sizeMin: filters.sizeMin,
    sizeMax: filters.sizeMax,
    roomsMin: filters.roomsMin,
    operation: filters.operation,
    propertyType: filters.propertyType,
    zone: filters.zone,
    neighbourhoods: filters.neighbourhoods,
    bbox: filters.bbox,
    polygon: filters.polygon,
    bathroomsMin: filters.bathroomsMin,
    floorMin: filters.floorMin,
    yearMin: filters.yearMin,
    condition: filters.condition,
    amenities: filters.amenities,
    centerMaxKm: filters.centerMaxKm,
    metroMaxKm: filters.metroMaxKm,
    deviationMax: filters.bargainsOnly ? BARGAIN_THRESHOLD : null,
  };
}

/** Una petición al worker esperando respuesta. */
interface Pending {
  resolve: (value: unknown) => void;
  reject: (cause: unknown) => void;
}

class WorkerClient implements DataClient {
  private readonly worker: Worker;
  private readonly pending = new Map<number, Pending>();
  private nextId = 1;

  /** Los GeoJSON no pasan por el worker: se piden una vez y se quedan aquí. */
  private geography = new Map<string, Promise<unknown>>();

  constructor() {
    this.worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
    this.worker.onmessage = (event: MessageEvent<Response>) => {
      const { id } = event.data;
      const waiting = this.pending.get(id);
      if (!waiting) return;
      this.pending.delete(id);
      if (event.data.ok) waiting.resolve(event.data.value);
      else waiting.reject(new ApiError(event.data.status, event.data.message));
    };
    this.worker.onerror = (event) => {
      // Un worker que se cae deja colgada a toda la aplicación si no se avisa.
      const error = new ApiError(500, event.message || "El worker de datos ha fallado");
      for (const waiting of this.pending.values()) waiting.reject(error);
      this.pending.clear();
    };

    // La descarga arranca con la aplicación, no con la primera consulta: son
    // 4 MB, y esperar a que alguien pregunte para empezar a bajarlos añadiría
    // esa espera al primer render en vez de solaparla con él.
    void this.send({ op: "load", payloadUrl: `${DATA_BASE}listings.bin.gz` }).catch(() => {
      // El fallo se le cuenta a quien pregunte; aquí solo se evita un rechazo
      // sin capturar en la consola.
    });
  }

  private send(request: RequestBody, signal?: AbortSignal): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const id = this.nextId++;

      if (signal) {
        if (signal.aborted) {
          reject(new DOMException("Aborted", "AbortError"));
          return;
        }
        // El worker no se puede interrumpir a mitad de una consulta, y tampoco
        // hace falta: lo que la interfaz quiere de un `abort` es dejar de
        // esperar una respuesta que ya no le sirve, y eso es exactamente lo que
        // pasa aquí. La consulta abandonada termina y su resultado se descarta.
        signal.addEventListener(
          "abort",
          () => {
            this.pending.delete(id);
            reject(new DOMException("Aborted", "AbortError"));
          },
          { once: true },
        );
      }

      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ ...request, id } as Request);
    });
  }

  fetchListings(
    filters: Filters,
    page: PageRequest,
    order: Order = "reciente",
    signal?: AbortSignal,
  ): Promise<ListingPage> {
    return this.send(
      { op: "listings", filters: normalize(filters), ...page, order },
      signal,
    ) as Promise<ListingPage>;
  }

  fetchListingsByIds(
    globalIds: string[],
    page: PageRequest,
    signal?: AbortSignal,
  ): Promise<ListingPage> {
    return this.send(
      { op: "listings", filters: { ids: globalIds }, ...page, order: "reciente" },
      signal,
    ) as Promise<ListingPage>;
  }

  fetchStats(filters: Filters, bins: number, signal?: AbortSignal): Promise<Stats> {
    return this.send(
      { op: "stats", filters: normalize(filters), bins },
      signal,
    ) as Promise<Stats>;
  }

  fetchMapData(
    filters: Filters,
    zoom: number,
    heat = false,
    signal?: AbortSignal,
  ): Promise<MapData> {
    return this.send(
      { op: "map", filters: normalize(filters), zoom, heat },
      signal,
    ) as Promise<MapData>;
  }

  fetchFacets(signal?: AbortSignal): Promise<Facets> {
    return this.send({ op: "facets" }, signal) as Promise<Facets>;
  }

  fetchListing(id: string, signal?: AbortSignal): Promise<Listing> {
    return this.send({ op: "listing", globalId: id }, signal) as Promise<Listing>;
  }

  async fetchSources(): Promise<SourceStatus[]> {
    const facets = (await this.send({ op: "facets" })) as Facets;
    return [{ name: "idealista18", healthy: true, listings: facets.total }];
  }

  /**
   * La geografía, cacheada por la promesa.
   *
   * Son 277 polígonos que no cambian nunca, y la aplicación los pide con clave
   * constante; guardar la promesa —y no el resultado— hace que dos peticiones
   * simultáneas compartan una sola descarga.
   */
  private geo<T>(name: string, signal?: AbortSignal): Promise<T> {
    let cached = this.geography.get(name);
    if (!cached) {
      cached = fetchJson<T>(`${DATA_BASE}${name}.gz`, signal).catch((cause: unknown) => {
        this.geography.delete(name);
        throw cause;
      });
      this.geography.set(name, cached);
    }
    return cached as Promise<T>;
  }

  fetchNeighbourhoods(signal?: AbortSignal): Promise<NeighbourhoodCollection> {
    return this.geo<NeighbourhoodCollection>("neighbourhoods.geojson", signal);
  }

  fetchPointsOfInterest(signal?: AbortSignal): Promise<PoiCollection> {
    return this.geo<PoiCollection>("points_of_interest.geojson", signal);
  }
}

export function createStaticClient(): DataClient {
  return new WorkerClient();
}
