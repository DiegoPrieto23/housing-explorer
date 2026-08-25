/**
 * De dónde salen los datos. Se decide aquí, una vez, y nadie más se entera.
 *
 * Hay dos modos y comparten interfaz:
 *
 * `api` (por defecto)
 *     Peticiones HTTP a FastAPI. Es lo que corre en desarrollo y con
 *     `docker compose`, y lo que hace que el filtrado se resuelva en SQL sobre
 *     los índices de SQLite.
 *
 * `static`
 *     Sin servidor. Los 149.923 anuncios se compilan a un paquete binario
 *     (`scripts/build_static_data.py`), el navegador se lo descarga una vez y
 *     un worker resuelve los mismos filtros y las mismas agregaciones en
 *     memoria. Es lo que se publica en GitHub Pages, donde no hay ningún
 *     proceso al que preguntar.
 *
 * El modo llega en `VITE_DATA_MODE`, que Vite sustituye **en tiempo de
 * compilación**. Por eso la elección es un `if` sobre una constante y no una
 * lectura de configuración: el empaquetador puede ver cuál de las dos ramas
 * sobra y dejar fuera del bundle el cliente que no se use.
 *
 * Lo que no cambia entre modos es lo que se ve. Las dos implementaciones
 * responden a los mismos filtros con los mismos números, hasta la mediana y el
 * umbral de chollo, porque el motor estático es un puerto del repositorio de
 * Python y no una segunda versión de él.
 */

import { createHttpClient } from "./client";
import { createStaticClient } from "./static";
import type { DataClient } from "./types";

export const IS_STATIC = import.meta.env.VITE_DATA_MODE === "static";

const client: DataClient = IS_STATIC ? createStaticClient() : createHttpClient();

export const fetchListings: DataClient["fetchListings"] = (...args) =>
  client.fetchListings(...args);
export const fetchListingsByIds: DataClient["fetchListingsByIds"] = (...args) =>
  client.fetchListingsByIds(...args);
export const fetchStats: DataClient["fetchStats"] = (...args) => client.fetchStats(...args);
export const fetchMapData: DataClient["fetchMapData"] = (...args) => client.fetchMapData(...args);
export const fetchFacets: DataClient["fetchFacets"] = (...args) => client.fetchFacets(...args);
export const fetchListing: DataClient["fetchListing"] = (...args) => client.fetchListing(...args);
export const fetchSources: DataClient["fetchSources"] = (...args) => client.fetchSources(...args);
export const fetchNeighbourhoods: DataClient["fetchNeighbourhoods"] = (...args) =>
  client.fetchNeighbourhoods(...args);
export const fetchPointsOfInterest: DataClient["fetchPointsOfInterest"] = (...args) =>
  client.fetchPointsOfInterest(...args);

export { ApiError } from "./types";
export type { DataClient, Order, PageRequest } from "./types";
