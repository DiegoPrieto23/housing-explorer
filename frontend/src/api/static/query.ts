/**
 * El motor de consulta, en el navegador. Puerto de `backend/app/storage/repository.py`.
 *
 * Cada función de aquí responde a lo mismo que su endpoint equivalente, sobre las
 * mismas 149.923 filas y con las mismas convenciones — incluidas las que en SQL
 * salen gratis y aquí hay que escribir a mano:
 *
 * - **Un nulo no cumple ninguna comparación.** «Al menos 50 m2» no puede incluir
 *   un piso que no declara superficie, igual que `size_m2 >= 50` no lo incluye en
 *   SQL. Los centinelas se descartan explícitamente en cada filtro numérico.
 * - **Los extras se piden todos; los barrios, cualquiera.** Un piso está en un
 *   barrio y no en cinco, pero puede tener ascensor *y* garaje.
 * - **El total es exacto siempre.** No hay tope de filas en ningún sitio: cuando
 *   son demasiadas para dibujarlas, se agregan; nunca se truncan.
 *
 * Que sea un puerto y no una reimplementación es deliberado: el visor tiene que
 * enseñar los mismos números lo levantes con Docker contra FastAPI o lo abras
 * en GitHub Pages. Cuando el original toma una decisión que no es obvia —la
 * mediana por rango más cercano, la caja robusta a tres sigmas, promediar el
 * ratio en vez de dividir los promedios— el comentario correspondiente está en
 * el repositorio de Python y aquí se cita en corto.
 */

import type {
  Amenity,
  AmenityImpact,
  Bucket,
  Condition,
  Listing,
  ListingPage,
  MapCluster,
  MapData,
  MapPoint,
  Operation,
  OverallStats,
  PriceBucket,
  PropertyType,
  Stats,
  ZoneStats,
} from "../../types/listing";
import type { Order } from "../types";
import { Dataset, NULL_I8, NULL_U16, NULL_U8 } from "./dataset";

/** Un rectángulo del mapa, en grados. */
export interface Box {
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
}

/**
 * Lo que se puede pedir. Espejo de `ListingFilters` del backend, con los nombres
 * en inglés que usa el resto del frontend en vez de los alias en castellano de
 * la query string: aquí no hay query string que atravesar.
 */
export interface QueryFilters {
  priceMin?: number | null;
  priceMax?: number | null;
  sizeMin?: number | null;
  sizeMax?: number | null;
  roomsMin?: number | null;
  operation?: Operation | null;
  propertyType?: PropertyType | null;
  zone?: string | null;
  neighbourhoods?: string[];
  bbox?: Box | null;
  polygon?: string | null;
  bathroomsMin?: number | null;
  floorMin?: number | null;
  yearMin?: number | null;
  condition?: Condition | null;
  amenities?: Amenity[];
  centerMaxKm?: number | null;
  metroMaxKm?: number | null;
  /** En %, negativo = más barato de lo estimado. `solo_chollos` se resuelve a esto. */
  deviationMax?: number | null;
  /** Identificadores globales concretos. Lo usa la vista de favoritos. */
  ids?: string[] | null;
}

/** Umbral de chollo. Tiene que coincidir con `BARGAIN_THRESHOLD` del backend. */
export const BARGAIN_THRESHOLD = -25;

/** Por encima de estas coincidencias el mapa pasa de marcadores a celdas. */
export const DEFAULT_MAP_POINT_BUDGET = 6000;

/** Tope de celdas agregadas: por debajo del tamaño del punto que las dibuja. */
export const MAX_MAP_CELLS = 2000;

/** Hasta este zoom se agrupa por ciudad en vez de por rejilla. */
export const ZONE_GROUPING_MAX_ZOOM = 8;

/** Topes superiores de las bandas de distancia al centro, en km. */
const DISTANCE_BANDS = [0.5, 1, 1.5, 2, 3, 4, 6, 8];

/** Topes superiores de las bandas de superficie, en m2. */
const SIZE_BANDS = [40, 60, 80, 100, 130, 170, 220, 300];

/* -------------------------------------------------------------------------
 * Polígonos dibujados a mano
 * ---------------------------------------------------------------------- */

type Vertex = [number, number];

/**
 * Decodifica `"lat,lon;lat,lon;..."`. Puerto de `app/geometry.py`.
 *
 * Cacheado por la cadena cruda: el mismo polígono se consulta una vez por fila
 * y volver a partir la cadena 149.923 veces dominaría el filtro entero.
 */
const polygonCache = new Map<string, Vertex[]>();

function parsePolygon(encoded: string): Vertex[] {
  const cached = polygonCache.get(encoded);
  if (cached) return cached;

  const vertices: Vertex[] = [];
  for (const chunk of encoded.split(";")) {
    const trimmed = chunk.trim();
    if (!trimmed) continue;
    const parts = trimmed.split(",");
    if (parts.length !== 2) throw new Error(`Vértice mal formado: '${trimmed}'`);
    const latitude = Number(parts[0]);
    const longitude = Number(parts[1]);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
      throw new Error(`Vértice no numérico: '${trimmed}'`);
    }
    vertices.push([latitude, longitude]);
  }

  if (vertices.length < 3) throw new Error("Un polígono necesita al menos 3 vértices");
  // Un vértice de cierre igual al primero sobra para el trazado de rayos, que ya
  // trata el anillo como cerrado. Se quita para no contarlo dos veces.
  const first = vertices[0];
  const last = vertices[vertices.length - 1];
  if (vertices.length > 3 && first[0] === last[0] && first[1] === last[1]) vertices.pop();

  polygonCache.set(encoded, vertices);
  return vertices;
}

/** Trazado de rayos. La comparación asimétrica evita contar dos veces un vértice sobre el rayo. */
function pointInPolygon(latitude: number, longitude: number, vertices: Vertex[]): boolean {
  let inside = false;
  let [previousLat, previousLon] = vertices[vertices.length - 1];

  for (const [currentLat, currentLon] of vertices) {
    if (currentLat > latitude !== previousLat > latitude) {
      const crossing =
        currentLon +
        ((latitude - currentLat) / (previousLat - currentLat)) * (previousLon - currentLon);
      if (longitude < crossing) inside = !inside;
    }
    previousLat = currentLat;
    previousLon = currentLon;
  }
  return inside;
}

function polygonBounds(vertices: Vertex[]): Box {
  let latMin = Infinity;
  let latMax = -Infinity;
  let lonMin = Infinity;
  let lonMax = -Infinity;
  for (const [latitude, longitude] of vertices) {
    if (latitude < latMin) latMin = latitude;
    if (latitude > latMax) latMax = latitude;
    if (longitude < lonMin) lonMin = longitude;
    if (longitude > lonMax) lonMax = longitude;
  }
  return { lat_min: latMin, lat_max: latMax, lon_min: lonMin, lon_max: lonMax };
}

/* -------------------------------------------------------------------------
 * El motor
 * ---------------------------------------------------------------------- */

export class QueryEngine {
  constructor(private readonly data: Dataset) {}

  get facets() {
    return this.data.header.facets;
  }

  get total() {
    return this.data.count;
  }

  /**
   * Las filas que cumplen el filtro, en orden de fichero — que es el orden
   * «reciente».
   *
   * Un `Int32Array` y no un array normal: es el resultado intermedio de cada
   * consulta y puede tener 149.923 elementos, y un array de enteros nativos es
   * la diferencia entre 600 kB y varios megas de objetos boxed.
   */
  select(filters: QueryFilters = {}): Int32Array {
    const data = this.data;
    const scale = data.coordScale;

    // Todo lo que se puede resolver una vez se resuelve una vez, fuera del
    // bucle: buscar el índice de una ciudad 149.923 veces cuesta más que el
    // filtro entero.
    const vocabularies = data.header.vocabularies;

    const zoneIndex =
      filters.zone == null
        ? -1
        : vocabularies.cities.findIndex(
            (name) => name.toLowerCase() === filters.zone!.toLowerCase(),
          );
    // Una ciudad que no existe no es «sin filtro»: es cero resultados.
    if (filters.zone != null && zoneIndex === -1) return new Int32Array(0);

    let neighbourhoodMask: Uint8Array | null = null;
    if (filters.neighbourhoods && filters.neighbourhoods.length > 0) {
      neighbourhoodMask = new Uint8Array(vocabularies.neighbourhoods.length);
      const wanted = new Set(filters.neighbourhoods);
      let any = false;
      vocabularies.neighbourhoods.forEach((entry, index) => {
        if (wanted.has(entry.id)) {
          neighbourhoodMask![index] = 1;
          any = true;
        }
      });
      if (!any) return new Int32Array(0);
    }

    const operationIndex =
      filters.operation == null ? -1 : vocabularies.operations.indexOf(filters.operation);
    if (filters.operation != null && operationIndex === -1) return new Int32Array(0);

    const propertyIndex =
      filters.propertyType == null
        ? -1
        : vocabularies.propertyTypes.indexOf(filters.propertyType);
    if (filters.propertyType != null && propertyIndex === -1) return new Int32Array(0);

    const conditionIndex =
      filters.condition == null ? -1 : vocabularies.conditions.indexOf(filters.condition);
    if (filters.condition != null && conditionIndex === -1) return new Int32Array(0);

    // Todos los extras marcados son obligatorios: una sola máscara y un AND.
    let amenityMask = 0;
    for (const amenity of filters.amenities ?? []) {
      const bit = vocabularies.amenities.indexOf(amenity);
      if (bit === -1) return new Int32Array(0);
      amenityMask |= 1 << bit;
    }

    // Las comparaciones geográficas se hacen contra la escala entera de la
    // columna, sin decodificar cada fila a grados.
    const box = filters.bbox ?? null;
    const latLow = box ? box.lat_min * scale : 0;
    const latHigh = box ? box.lat_max * scale : 0;
    const lonLow = box ? box.lon_min * scale : 0;
    const lonHigh = box ? box.lon_max * scale : 0;

    // El polígono lleva su caja delante, igual que en SQL: el recorte barato
    // primero, y el trazado de rayos solo para lo que sobreviva.
    const vertices = filters.polygon ? parsePolygon(filters.polygon) : null;
    const polyBox = vertices ? polygonBounds(vertices) : null;
    const polyLatLow = polyBox ? polyBox.lat_min * scale : 0;
    const polyLatHigh = polyBox ? polyBox.lat_max * scale : 0;
    const polyLonLow = polyBox ? polyBox.lon_min * scale : 0;
    const polyLonHigh = polyBox ? polyBox.lon_max * scale : 0;

    // Los ids llegan de localStorage y son un puñado: se comparan por dígitos
    // contra la columna, sin construir 149.923 cadenas para buscar cuatro.
    let wantedIds: string[] | null = null;
    if (filters.ids) {
      if (filters.ids.length === 0) return new Int32Array(0);
      const prefix = data.header.idPrefix;
      wantedIds = [];
      for (const raw of filters.ids) {
        const bare = raw.includes(":") ? raw.slice(raw.indexOf(":") + 1) : raw;
        if (bare.startsWith(prefix)) wantedIds.push(bare.slice(prefix.length));
      }
      if (wantedIds.length === 0) return new Int32Array(0);
    }

    const centerLimit =
      filters.centerMaxKm == null ? null : filters.centerMaxKm * data.distanceScale;
    const metroLimit =
      filters.metroMaxKm == null ? null : filters.metroMaxKm * data.distanceScale;

    const matches = new Int32Array(data.count);
    let found = 0;

    for (let row = 0; row < data.count; row += 1) {
      if (operationIndex !== -1 && data.operation[row] !== operationIndex) continue;
      if (propertyIndex !== -1 && data.ptype[row] !== propertyIndex) continue;
      if (zoneIndex !== -1 && data.city[row] !== zoneIndex) continue;

      if (neighbourhoodMask) {
        const index = data.neighbourhood[row];
        if (index === NULL_U16 || !neighbourhoodMask[index]) continue;
      }

      const price = data.price[row];
      if (filters.priceMin != null && price < filters.priceMin) continue;
      if (filters.priceMax != null && price > filters.priceMax) continue;

      if (filters.sizeMin != null || filters.sizeMax != null) {
        const size = data.size[row];
        // Un piso sin superficie declarada no cumple «al menos 50 m2».
        if (size === NULL_U16) continue;
        if (filters.sizeMin != null && size < filters.sizeMin) continue;
        if (filters.sizeMax != null && size > filters.sizeMax) continue;
      }

      if (filters.roomsMin != null) {
        const rooms = data.rooms[row];
        if (rooms === NULL_U8 || rooms < filters.roomsMin) continue;
      }

      if (filters.bathroomsMin != null) {
        const baths = data.baths[row];
        if (baths === NULL_U8 || baths < filters.bathroomsMin) continue;
      }

      if (filters.floorMin != null) {
        const floor = data.floor[row];
        if (floor === NULL_I8 || floor < filters.floorMin) continue;
      }

      if (filters.yearMin != null) {
        const year = data.year[row];
        if (year === NULL_U16 || year < filters.yearMin) continue;
      }

      if (conditionIndex !== -1 && data.condition[row] !== conditionIndex) continue;

      if (amenityMask && (data.amenities[row] & amenityMask) !== amenityMask) continue;

      if (centerLimit !== null) {
        const distance = data.distanceCenter[row];
        if (distance === NULL_U16 || distance > centerLimit) continue;
      }
      if (metroLimit !== null) {
        const distance = data.distanceMetro[row];
        if (distance === NULL_U16 || distance > metroLimit) continue;
      }

      // Un anuncio sin estimar no es un chollo: es un desconocido. Su
      // desviación es `NaN`, y `NaN > x` es falso, así que se descarta por la
      // negación — la misma forma en que un NULL falla la comparación en SQL.
      if (filters.deviationMax != null && !(data.deviation[row] <= filters.deviationMax)) continue;

      const latitude = data.lat[row];
      const longitude = data.lon[row];
      if (box && (latitude < latLow || latitude > latHigh)) continue;
      if (box && (longitude < lonLow || longitude > lonHigh)) continue;

      if (vertices) {
        if (latitude < polyLatLow || latitude > polyLatHigh) continue;
        if (longitude < polyLonLow || longitude > polyLonHigh) continue;
        if (!pointInPolygon(latitude / scale, longitude / scale, vertices)) continue;
      }

      if (wantedIds) {
        let hit = false;
        for (const digits of wantedIds) {
          if (data.matchesId(row, digits)) {
            hit = true;
            break;
          }
        }
        if (!hit) continue;
      }

      matches[found] = row;
      found += 1;
    }

    return matches.subarray(0, found);
  }

  // -- listado -----------------------------------------------------------

  /**
   * Una página de anuncios.
   *
   * Cada ordenación termina desempatando por posición de fila. En el backend el
   * desempate es `global_id`; lo que importa de los dos es lo mismo —que sea
   * **total y estable**—, porque sin eso dos anuncios con el mismo precio pueden
   * cambiar de sitio entre la página 1 y la 2 y salir repetidos, o no salir.
   */
  page(filters: QueryFilters, limit: number, offset: number, order: Order): ListingPage {
    const rows = this.select(filters);
    const sorted = this.sortRows(rows, order);
    const page: Listing[] = [];
    const end = Math.min(offset + limit, sorted.length);
    for (let index = offset; index < end; index += 1) {
      page.push(this.data.listingOf(sorted[index]));
    }
    return { items: page, total: sorted.length, limit, offset };
  }

  private sortRows(rows: Int32Array, order: Order): Int32Array {
    const data = this.data;
    // El fichero ya viene en orden «reciente», así que `select` devuelve las
    // filas ordenadas y no hay nada que hacer.
    if (order === "reciente") return rows;

    // Copia: `select` devuelve una vista sobre un búfer reutilizable, y ordenar
    // en el sitio dejaría el resultado en un orden que el llamante no pidió.
    const sorted = rows.slice();

    if (order === "precio") {
      sorted.sort((a, b) => data.price[a] - data.price[b] || a - b);
    } else if (order === "precio_desc") {
      sorted.sort((a, b) => data.price[b] - data.price[a] || a - b);
    } else {
      // Los no estimados al final: no son «el mayor chollo», son un desconocido.
      // Hay que apartarlos a mano — `NaN - NaN` es `NaN`, y un comparador que
      // devuelve `NaN` deja el orden sin definir.
      sorted.sort((a, b) => {
        const left = data.deviation[a];
        const right = data.deviation[b];
        const leftNull = Number.isNaN(left) ? 1 : 0;
        const rightNull = Number.isNaN(right) ? 1 : 0;
        if (leftNull || rightNull) return leftNull - rightNull || a - b;
        return left - right || a - b;
      });
    }
    return sorted;
  }

  listing(globalId: string): Listing | null {
    const row = this.data.rowOfGlobalId(globalId);
    return row === -1 ? null : this.data.listingOf(row);
  }

  // -- mapa --------------------------------------------------------------

  /** Grados de lado de una celda a un zoom dado. Seis celdas por tesela. */
  static gridStep(zoom: number): number {
    return 360 / 2 ** Math.max(zoom, 0) / 6;
  }

  /**
   * Lo que hay que pintar, a la resolución que el zoom puede dibujar.
   *
   * Dos modos, elegidos por cuántos coinciden y no por un tope fijo: cuando
   * caben viajan uno a uno, y cuando no, agregados en celdas. `total` es
   * siempre el número exacto de coincidencias, así que el mapa nunca tiene que
   * decir «1.000 de 149.923».
   */
  map(
    filters: QueryFilters,
    zoom: number,
    heat: boolean,
    pointBudget = DEFAULT_MAP_POINT_BUDGET,
  ): MapData {
    const rows = this.select(filters);
    const total = rows.length;
    const data = this.data;

    if (total === 0) return { mode: "points", total: 0, points: [], clusters: [] };

    if (total <= pointBudget && !heat) {
      const points: MapPoint[] = [];
      for (let index = 0; index < total; index += 1) {
        const row = rows[index];
        const deviation = data.deviation[row];
        points.push({
          global_id: data.globalIdOf(row),
          // 5 decimales de latitud son ~1,1 m sobre el terreno: más dígitos son
          // precisión que nadie puede ver ni usar, en 6.000 marcadores.
          latitude: round(data.latitudeOf(row), 5),
          longitude: round(data.longitudeOf(row), 5),
          price: Math.round(data.price[row]),
          property_type: data.propertyTypeOf(row),
          operation: data.operationOf(row),
          price_deviation: Number.isNaN(deviation) ? null : round(deviation, 1),
        });
      }
      return { mode: "points", total, points, clusters: [] };
    }

    const step = QueryEngine.gridStep(zoom);
    const byZone = zoom <= ZONE_GROUPING_MAX_ZOOM;
    const cells = new Map<string, Accumulator>();

    for (let index = 0; index < total; index += 1) {
      const row = rows[index];
      const latitude = data.latitudeOf(row);
      const longitude = data.longitudeOf(row);
      // Los desplazamientos +90 / +180 hacen positivas las coordenadas antes de
      // truncar: truncar hacia cero plegaría las celdas de los dos lados del
      // ecuador y del meridiano en una sola.
      const cellY = Math.trunc((latitude + 90) / step);
      const cellX = Math.trunc((longitude + 180) / step);
      const cityIndex = data.city[row];

      // A poco zoom la clave es la ciudad y no la rejilla. Una rejilla fija
      // tiene que cortar en algún sitio, y a zoom 6 el corte cae dentro de
      // Madrid: dos puntos para una ciudad y uno para cada una de las otras.
      const key =
        byZone && cityIndex !== NULL_U8 ? `z:${cityIndex}` : `g:${cellY},${cellX}`;

      let cell = cells.get(key);
      if (!cell) {
        cell = newAccumulator(byZone && cityIndex !== NULL_U8 ? cityIndex : -1);
        cells.set(key, cell);
      }
      accumulate(cell, latitude, longitude, data.price[row], data.size[row]);
    }

    // Se ordena antes de construir las celdas, sobre las claves, para no llevar
    // la clave dentro del objeto que sale por la API solo para poder ordenarlo.
    // Por número de anuncios, y por clave a igualdad, de modo que dos peticiones
    // idénticas devuelvan las mismas celdas en el mismo orden.
    const keys = [...cells.keys()].sort((a, b) => {
      const difference = cells.get(b)!.count - cells.get(a)!.count;
      return difference !== 0 ? difference : a < b ? -1 : a > b ? 1 : 0;
    });

    const clusters: MapCluster[] = keys.slice(0, MAX_MAP_CELLS).map((key) => {
      const cell = cells.get(key)!;
      return {
        latitude: round(cell.latSum / cell.count, 5),
        longitude: round(cell.lonSum / cell.count, 5),
        ...cellExtent(cell),
        count: cell.count,
        avg_price: Math.round(cell.priceSum / cell.count),
        // Una celda donde nadie declara superficie no tiene precio por metro.
        // Null, no cero: «no se sabe» y «gratis» no son el mismo color.
        avg_price_per_m2: cell.withSize === 0 ? null : Math.round(cell.perM2Sum / cell.withSize),
        with_size: cell.withSize,
        // Solo significa algo cuando se agrupó por ciudad: una celda de rejilla
        // a caballo entre dos ciudades diría la que tocara.
        zone: cell.city === -1 ? null : data.header.vocabularies.cities[cell.city],
      };
    });

    return { mode: "clusters", total, points: [], clusters };
  }

  // -- estadísticas ------------------------------------------------------

  stats(filters: QueryFilters, bins: number): Stats {
    const rows = this.select(filters);
    const overall = this.overall(rows);
    // Con la búsqueda ya acotada a una ciudad o a unos barrios, cortar por
    // ciudad devuelve una fila que repite la cabecera. El mismo agregado por
    // barrio es lo que responde a «¿dónde dentro de Madrid sale a cuenta?».
    const byNeighbourhood =
      filters.zone != null || (filters.neighbourhoods?.length ?? 0) > 0;

    return {
      overall,
      by_zone: this.zoneStats(rows, byNeighbourhood),
      by_zone_is_neighbourhood: byNeighbourhood,
      by_rooms: this.byRooms(rows),
      by_size: this.bySize(rows),
      by_distance: this.byDistance(rows),
      amenities: this.amenityImpact(rows),
      price_distribution: this.priceDistribution(rows, overall, bins),
    };
  }

  private overall(rows: Int32Array): OverallStats {
    const data = this.data;
    const count = rows.length;
    if (count === 0) {
      return {
        count: 0,
        avg_price: null,
        min_price: null,
        max_price: null,
        avg_price_per_m2: null,
        p25_price: null,
        median_price: null,
        p75_price: null,
        p90_price: null,
        p99_price: null,
      };
    }

    const prices = new Float64Array(count);
    let priceSum = 0;
    let perM2Sum = 0;
    let withSize = 0;

    for (let index = 0; index < count; index += 1) {
      const row = rows[index];
      const price = data.price[row];
      prices[index] = price;
      priceSum += price;
      const size = data.size[row];
      if (size !== NULL_U16 && size > 0) {
        perM2Sum += price / size;
        withSize += 1;
      }
    }
    prices.sort();

    return {
      count,
      avg_price: priceSum / count,
      min_price: prices[0],
      max_price: prices[count - 1],
      // Se promedia el ratio, no se dividen los promedios: «cuánto cuesta el
      // metro por aquí» es la media de precio/m2, y el total sobre el total
      // dejaría que un piso de 400 m2 hablara por un barrio de estudios.
      avg_price_per_m2: withSize === 0 ? null : perM2Sum / withSize,
      p25_price: percentile(prices, 0.25),
      median_price: percentile(prices, 0.5),
      p75_price: percentile(prices, 0.75),
      p90_price: percentile(prices, 0.9),
      p99_price: percentile(prices, 0.99),
    };
  }

  private zoneStats(rows: Int32Array, byNeighbourhood: boolean): ZoneStats[] {
    const data = this.data;
    const groups = new Map<number, number[]>();

    for (let index = 0; index < rows.length; index += 1) {
      const row = rows[index];
      const key = byNeighbourhood ? data.neighbourhood[row] : data.city[row];
      if (byNeighbourhood ? key === NULL_U16 : key === NULL_U8) continue;
      const bucket = groups.get(key);
      if (bucket) bucket.push(row);
      else groups.set(key, [row]);
    }

    const results: ZoneStats[] = [];
    for (const [key, bucket] of groups) {
      const count = bucket.length;
      const prices = new Float64Array(count);
      let priceSum = 0;
      let perM2Sum = 0;
      let withSize = 0;

      for (let index = 0; index < count; index += 1) {
        const row = bucket[index];
        const price = data.price[row];
        prices[index] = price;
        priceSum += price;
        const size = data.size[row];
        if (size !== NULL_U16 && size > 0) {
          perM2Sum += price / size;
          withSize += 1;
        }
      }
      prices.sort();

      const entry = byNeighbourhood
        ? data.header.vocabularies.neighbourhoods[key]
        : null;

      results.push({
        zone: byNeighbourhood ? entry!.name : data.header.vocabularies.cities[key],
        neighbourhood_id: byNeighbourhood ? entry!.id : null,
        count,
        avg_price: priceSum / count,
        min_price: prices[0],
        max_price: prices[count - 1],
        median_price: percentile(prices, 0.5),
        avg_price_per_m2: withSize === 0 ? null : perM2Sum / withSize,
      });
    }

    results.sort((a, b) => b.count - a.count || (a.zone < b.zone ? -1 : a.zone > b.zone ? 1 : 0));
    return results;
  }

  /**
   * Agrega en tramos, en una pasada.
   *
   * Sin mediana: alimenta un gráfico de barras de dos centímetros en una barra
   * lateral, y la media es lo que ese gráfico puede enseñar.
   */
  private buckets(
    rows: Int32Array,
    bucketOf: (row: number) => number,
    label: (bucket: number) => string,
  ): Bucket[] {
    const data = this.data;
    const accumulator = new Map<number, { count: number; price: number; perM2: number }>();

    for (let index = 0; index < rows.length; index += 1) {
      const row = rows[index];
      const bucket = bucketOf(row);
      if (bucket < 0) continue;
      let entry = accumulator.get(bucket);
      if (!entry) {
        entry = { count: 0, price: 0, perM2: 0 };
        accumulator.set(bucket, entry);
      }
      entry.count += 1;
      entry.price += data.price[row];
      entry.perM2 += data.price[row] / data.size[row];
    }

    return [...accumulator.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([bucket, entry]) => ({
        bucket,
        label: label(bucket),
        count: entry.count,
        avg_price: Math.round(entry.price / entry.count),
        avg_price_per_m2: Math.round(entry.perM2 / entry.count),
      }));
  }

  private byRooms(rows: Int32Array): Bucket[] {
    const data = this.data;
    return this.buckets(
      rows,
      (row) => {
        const rooms = data.rooms[row];
        const size = data.size[row];
        if (rooms === NULL_U8 || size === NULL_U16 || size <= 0) return -1;
        // Todo lo que pase de 6 en un tramo: por encima, las cuentas son
        // demasiado pequeñas para que la media signifique nada.
        return Math.min(rooms, 6);
      },
      (bucket) => (bucket >= 6 ? "6+" : String(bucket)),
    );
  }

  private bySize(rows: Int32Array): Bucket[] {
    const data = this.data;
    return this.buckets(
      rows,
      (row) => {
        const size = data.size[row];
        if (size === NULL_U16 || size <= 0) return -1;
        return band(size, SIZE_BANDS);
      },
      (bucket) =>
        bucket === 0
          ? `≤${SIZE_BANDS[0]}`
          : bucket >= SIZE_BANDS.length
            ? `>${SIZE_BANDS[SIZE_BANDS.length - 1]}`
            : `${SIZE_BANDS[bucket - 1]}–${SIZE_BANDS[bucket]}`,
    );
  }

  private byDistance(rows: Int32Array): Bucket[] {
    const data = this.data;
    const scale = data.distanceScale;
    return this.buckets(
      rows,
      (row) => {
        const size = data.size[row];
        const distance = data.distanceCenter[row];
        if (size === NULL_U16 || size <= 0 || distance === NULL_U16) return -1;
        return band(distance / scale, DISTANCE_BANDS);
      },
      (bucket) =>
        bucket === 0
          ? `<${format(DISTANCE_BANDS[0])}`
          : bucket >= DISTANCE_BANDS.length
            ? `>${format(DISTANCE_BANDS[DISTANCE_BANDS.length - 1])}`
            : `${format(DISTANCE_BANDS[bucket - 1])}-${format(DISTANCE_BANDS[bucket])}`,
    );
  }

  /**
   * Por cada extra: cuántos lo tienen, y el precio por m2 con y sin él.
   *
   * **Es una correlación, y la interfaz tiene que decirlo.** Las piscinas y los
   * porteros aparecen donde ya está el dinero; poner una no le añade un 40 % a
   * un piso.
   */
  private amenityImpact(rows: Int32Array): AmenityImpact[] {
    const data = this.data;
    const names = data.header.vocabularies.amenities;
    const withCount = new Int32Array(names.length);
    const withSum = new Float64Array(names.length);
    const withoutCount = new Int32Array(names.length);
    const withoutSum = new Float64Array(names.length);
    let total = 0;

    for (let index = 0; index < rows.length; index += 1) {
      const row = rows[index];
      const size = data.size[row];
      if (size === NULL_U16 || size <= 0) continue;
      total += 1;
      const perM2 = data.price[row] / size;
      const mask = data.amenities[row];
      for (let bit = 0; bit < names.length; bit += 1) {
        if (mask & (1 << bit)) {
          withCount[bit] += 1;
          withSum[bit] += perM2;
        } else {
          withoutCount[bit] += 1;
          withoutSum[bit] += perM2;
        }
      }
    }

    const results: AmenityImpact[] = [];
    for (let bit = 0; bit < names.length; bit += 1) {
      // Una comparación necesita los dos lados. Si todo lo seleccionado tiene
      // ascensor, «con ascensor cuesta más que sin» no significa nada.
      if (withCount[bit] === 0 || withoutCount[bit] === 0) continue;
      const withIt = withSum[bit] / withCount[bit];
      const withoutIt = withoutSum[bit] / withoutCount[bit];
      if (!withoutIt) continue;
      results.push({
        amenity: names[bit] as Amenity,
        count: withCount[bit],
        share: total ? round((100 * withCount[bit]) / total, 1) : 0,
        with_it: Math.round(withIt),
        without_it: Math.round(withoutIt),
        difference: round(100 * (withIt / withoutIt - 1), 1),
      });
    }

    results.sort((a, b) => b.difference - a.difference);
    return results;
  }

  /**
   * El histograma, con la cola larga recogida en un tramo abierto.
   *
   * Tramos de igual anchura entre el mínimo y el percentil 99. Sin el recorte,
   * un solo anuncio de varios millones estiraría el eje y dejaría invisibles
   * todos los demás.
   */
  private priceDistribution(rows: Int32Array, overall: OverallStats, bins: number): PriceBucket[] {
    if (!overall.count || overall.min_price === null) return [];

    const lower = overall.min_price;
    const upper = overall.p99_price ?? overall.max_price;
    if (upper === null || upper <= lower) {
      return [{ lower, upper: null, count: overall.count }];
    }

    const width = (upper - lower) / bins;
    const counts = new Int32Array(bins + 1);
    for (let index = 0; index < rows.length; index += 1) {
      const bucket = Math.min(Math.trunc((this.data.price[rows[index]] - lower) / width), bins);
      counts[bucket] += 1;
    }

    const buckets: PriceBucket[] = [];
    for (let index = 0; index < bins; index += 1) {
      buckets.push({
        lower: lower + index * width,
        upper: lower + (index + 1) * width,
        count: counts[index],
      });
    }
    // El tramo de desbordamiento es abierto: todo lo que llega al percentil 99.
    buckets.push({ lower: upper, upper: null, count: counts[bins] });
    return buckets;
  }
}

/* -------------------------------------------------------------------------
 * Utilidades
 * ---------------------------------------------------------------------- */

/** Índice del primer tramo cuyo tope superior alcanza el valor; el último si ninguno. */
function band(value: number, bounds: number[]): number {
  for (let index = 0; index < bounds.length; index += 1) {
    if (value <= bounds[index]) return index;
  }
  return bounds.length;
}

/** Como el `%g` de Python para estos números: 0.5 -> "0.5", 1 -> "1". */
function format(value: number): string {
  return String(value);
}

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

/**
 * Percentil por rango más cercano sobre precios ya ordenados.
 *
 * La misma definición que usa el backend, que es lo que hace que la mediana de
 * la barra lateral coincida se sirva desde donde se sirva.
 */
function percentile(sorted: Float64Array, quantile: number): number | null {
  const count = sorted.length;
  if (count === 0) return null;
  const offset = Math.max(0, Math.min(Math.ceil(count * quantile) - 1, count - 1));
  return sorted[offset];
}

/** Lo que se acumula por celda del mapa mientras se recorren sus anuncios. */
interface Accumulator {
  city: number;
  count: number;
  latSum: number;
  lonSum: number;
  latSq: number;
  lonSq: number;
  latMin: number;
  latMax: number;
  lonMin: number;
  lonMax: number;
  priceSum: number;
  perM2Sum: number;
  withSize: number;
}

function newAccumulator(city: number): Accumulator {
  return {
    city,
    count: 0,
    latSum: 0,
    lonSum: 0,
    latSq: 0,
    lonSq: 0,
    latMin: Infinity,
    latMax: -Infinity,
    lonMin: Infinity,
    lonMax: -Infinity,
    priceSum: 0,
    perM2Sum: 0,
    withSize: 0,
  };
}

function accumulate(
  cell: Accumulator,
  latitude: number,
  longitude: number,
  price: number,
  size: number,
): void {
  cell.count += 1;
  cell.latSum += latitude;
  cell.lonSum += longitude;
  cell.latSq += latitude * latitude;
  cell.lonSq += longitude * longitude;
  if (latitude < cell.latMin) cell.latMin = latitude;
  if (latitude > cell.latMax) cell.latMax = latitude;
  if (longitude < cell.lonMin) cell.lonMin = longitude;
  if (longitude > cell.lonMax) cell.lonMax = longitude;
  cell.priceSum += price;
  if (size !== NULL_U16 && size > 0) {
    cell.perM2Sum += price / size;
    cell.withSize += 1;
  }
}

/**
 * El rectángulo que una celda cubre de verdad, ignorando el anuncio despistado.
 *
 * Media más/menos tres sigmas, recortado a la extensión real. El MIN/MAX puro
 * no sirve en cuanto una celda es una ciudad entera: uno de los 75.804 anuncios
 * de Madrid está en Almería, y su rectángulo se estiraría 400 km hacia el sur.
 */
function cellExtent(cell: Accumulator): Pick<
  MapCluster,
  "lat_min" | "lat_max" | "lon_min" | "lon_max"
> {
  const margin = 0.002; // ~200 m, para que una celda con un solo anuncio tenga área

  const bounds = (sum: number, sumSquares: number, low: number, high: number): [number, number] => {
    const mean = sum / cell.count;
    const spread = Math.sqrt(Math.max(sumSquares / cell.count - mean * mean, 0));
    return [
      Math.max(mean - 3 * spread - margin, low),
      Math.min(mean + 3 * spread + margin, high),
    ];
  };

  const [latMin, latMax] = bounds(cell.latSum, cell.latSq, cell.latMin, cell.latMax);
  const [lonMin, lonMax] = bounds(cell.lonSum, cell.lonSq, cell.lonMin, cell.lonMax);
  return {
    lat_min: round(latMin, 5),
    lat_max: round(latMax, 5),
    lon_min: round(lonMin, 5),
    lon_max: round(lonMax, 5),
  };
}
