/**
 * Lectura del paquete que compila `scripts/build_static_data.py`.
 *
 * El fichero es columnar y autodescriptivo: una cabecera JSON declara cada
 * columna con su tipo y su desplazamiento, y este módulo la usa para montar los
 * `TypedArray` correspondientes **sobre el mismo búfer**, sin copiar. De ahí que
 * no haya ninguna tabla de offsets escrita a mano de este lado: la única forma
 * de que las dos mitades se desincronicen sería cambiar el formato sin subir
 * `FORMAT_VERSION`, y eso se comprueba abajo.
 *
 *     magic   'HEXP'            4 bytes
 *     header  uint32 longitud + JSON UTF-8 de esa longitud
 *     datos   los bloques de columna, uno detrás de otro
 */

import type { Facets, NeighbourhoodFacet } from "../../types/listing";

/** Tiene que coincidir con `FORMAT_VERSION` del script que escribe el paquete. */
export const SUPPORTED_VERSION = 1;

const MAGIC = "HEXP";

/** Los tipos de columna que el formato admite, y el `TypedArray` de cada uno. */
const VIEWS = {
  u8: Uint8Array,
  i8: Int8Array,
  u16: Uint16Array,
  i16: Int16Array,
  u32: Uint32Array,
  i32: Int32Array,
} as const;

type ColumnKind = keyof typeof VIEWS;

interface ColumnLayout {
  name: string;
  type: ColumnKind;
  offset: number;
  length: number;
}

export interface PayloadHeader {
  version: number;
  generatedAt: string;
  count: number;
  source: string;
  idPrefix: string;
  scales: { coord: number; distance: number; expected: number };
  nulls: { u8: number; u16: number; i8: number; i16: number };
  vocabularies: {
    operations: string[];
    propertyTypes: string[];
    conditions: string[];
    amenities: string[];
    cities: string[];
    neighbourhoods: NeighbourhoodFacet[];
  };
  columns: ColumnLayout[];
  facets: Facets;
}

export interface Payload {
  header: PayloadHeader;
  columns: Record<string, ArrayLike<number>>;
}

/** El paquete no está donde debería, o no es lo que dice ser. */
export class PayloadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PayloadError";
  }
}

/**
 * Descomprime si hace falta, y solo si hace falta.
 *
 * Los ficheros se publican comprimidos (`.gz`), pero **quién los descomprime
 * depende del servidor**: algunos los sirven con `Content-Encoding: gzip` y el
 * navegador ya los ha deshecho antes de que lleguen aquí, y otros los entregan
 * tal cual como un binario opaco. Mirar los dos primeros bytes distingue los dos
 * casos sin tener que saber nada del alojamiento, que es justo lo que no
 * queremos codificar: la misma compilación tiene que funcionar en GitHub Pages,
 * en `vite preview` y en un `python -m http.server`.
 */
async function gunzipIfNeeded(buffer: ArrayBuffer): Promise<ArrayBuffer> {
  const head = new Uint8Array(buffer, 0, Math.min(2, buffer.byteLength));
  if (head[0] !== 0x1f || head[1] !== 0x8b) return buffer;

  if (typeof DecompressionStream === "undefined") {
    throw new PayloadError(
      "Este navegador no puede descomprimir los datos (falta DecompressionStream). " +
        "Hace falta Chrome 80+, Firefox 113+ o Safari 16.4+.",
    );
  }

  const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream("gzip"));
  return await new Response(stream).arrayBuffer();
}

async function download(url: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw new PayloadError(`No se pudo descargar ${url}: ${response.status} ${response.statusText}`);
  }
  return gunzipIfNeeded(await response.arrayBuffer());
}

/** Un JSON publicado comprimido, como los GeoJSON de barrios y de POI. */
export async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const buffer = await download(url, signal);
  return JSON.parse(new TextDecoder().decode(buffer)) as T;
}

/**
 * El paquete de anuncios, ya en `TypedArray`.
 *
 * Las vistas se crean sobre el búfer descargado en vez de copiar los bloques a
 * arrays propios: son 8,7 MB, y duplicarlos para nada costaría el doble de
 * memoria y una pausa de recolección justo en el arranque.
 */
export async function fetchPayload(url: string, signal?: AbortSignal): Promise<Payload> {
  const buffer = await download(url, signal);

  if (buffer.byteLength < 8) {
    throw new PayloadError(`${url} está vacío o truncado.`);
  }

  const magic = new TextDecoder().decode(new Uint8Array(buffer, 0, 4));
  if (magic !== MAGIC) {
    throw new PayloadError(
      `${url} no es un paquete de Housing Explorer (esperaba '${MAGIC}', vino '${magic}').`,
    );
  }

  const headerLength = new DataView(buffer).getUint32(4, true);
  const headerBytes = new Uint8Array(buffer, 8, headerLength);
  const header = JSON.parse(new TextDecoder().decode(headerBytes)) as PayloadHeader;

  if (header.version !== SUPPORTED_VERSION) {
    throw new PayloadError(
      `El paquete de datos es de la versión ${header.version} y esta web lee la ` +
        `${SUPPORTED_VERSION}. Vuelve a compilarlo con scripts/build_static_data.py.`,
    );
  }

  const base = 8 + headerLength;
  const columns: Record<string, ArrayLike<number>> = {};
  for (const column of header.columns) {
    const View = VIEWS[column.type];
    if (!View) throw new PayloadError(`Tipo de columna desconocido: ${column.type}`);
    columns[column.name] = new View(
      buffer,
      base + column.offset,
      column.length / View.BYTES_PER_ELEMENT,
    );
  }

  return { header, columns };
}
