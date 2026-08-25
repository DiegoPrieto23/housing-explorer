/**
 * El conjunto de datos, ya decodificado y listo para consultar.
 *
 * Envuelve el paquete columnar que lee `payload.ts` y le pone nombres y
 * semántica: qué columna es cuál, qué valor significa «no se sabe», y cómo se
 * reconstruye un `Listing` completo a partir de una fila.
 *
 * Las columnas se quedan como `TypedArray`, no se convierten a objetos. Un
 * filtro sobre 149.923 anuncios es entonces un bucle sobre memoria contigua
 * —unos pocos milisegundos— en vez de 149.923 accesos a propiedades de objetos
 * dispersos por el montón, que es la diferencia entre que el panel responda al
 * arrastrar un deslizador y que no.
 */

import type {
  Amenity,
  Condition,
  Listing,
  Operation,
  PropertyType,
} from "../../types/listing";
import type { Payload, PayloadHeader } from "./payload";

/** Centinelas de nulo, uno por anchura. Ver `build_static_data.py`. */
const NULL_U8 = 0xff;
const NULL_U16 = 0xffff;
const NULL_I8 = -128;

/** La fuente es única en este dataset, pero el esquema la lleva igual. */
const SOURCE = "idealista18";

export class Dataset {
  readonly header: PayloadHeader;
  readonly count: number;

  readonly price: ArrayLike<number>;
  readonly size: ArrayLike<number>;
  readonly rooms: ArrayLike<number>;
  readonly baths: ArrayLike<number>;
  readonly floor: ArrayLike<number>;
  readonly year: ArrayLike<number>;
  readonly operation: ArrayLike<number>;
  readonly ptype: ArrayLike<number>;
  readonly condition: ArrayLike<number>;
  readonly amenities: ArrayLike<number>;
  /** Grados por `coordScale`. Ver `latitudeOf`. */
  readonly lat: ArrayLike<number>;
  readonly lon: ArrayLike<number>;
  readonly city: ArrayLike<number>;
  readonly neighbourhood: ArrayLike<number>;
  readonly distanceCenter: ArrayLike<number>;
  readonly distanceMetro: ArrayLike<number>;
  /** Precio estimado, en céntimos. Cero = el modelo no lo pudo estimar. */
  readonly expected: ArrayLike<number>;
  /**
   * Cuánto se aparta el precio pedido del estimado, en %. `NaN` si no hay
   * estimación.
   *
   * Se calcula al cargar y no viaja en el paquete: es exactamente
   * `100 * (precio - estimado) / estimado`, así que guardarla sería el mismo
   * dato dos veces. Y `NaN` como «no se sabe» no es un atajo — toda comparación
   * con `NaN` es falsa, que es justo lo que hace un `NULL` en SQL, así que los
   * filtros de desviación heredan la semántica correcta sin escribirla.
   */
  readonly deviation: Float64Array;

  private readonly idLength: ArrayLike<number>;
  private readonly idDigits: ArrayLike<number>;
  /** Dónde empieza cada id dentro de `idDigits`. Sumas parciales, calculadas una vez. */
  private readonly idOffset: Uint32Array;

  readonly coordScale: number;
  readonly distanceScale: number;
  readonly expectedScale: number;

  constructor(payload: Payload) {
    const { header, columns } = payload;
    this.header = header;
    this.count = header.count;

    const column = (name: string): ArrayLike<number> => {
      const found = columns[name];
      if (!found) throw new Error(`El paquete no trae la columna '${name}'`);
      return found;
    };

    this.price = column("price");
    this.size = column("size");
    this.rooms = column("rooms");
    this.baths = column("baths");
    this.floor = column("floor");
    this.year = column("year");
    this.operation = column("operation");
    this.ptype = column("ptype");
    this.condition = column("condition");
    this.amenities = column("amenities");
    this.lat = column("lat");
    this.lon = column("lon");
    this.city = column("city");
    this.neighbourhood = column("neighbourhood");
    this.distanceCenter = column("distanceCenter");
    this.distanceMetro = column("distanceMetro");
    this.expected = column("expected");
    this.idLength = column("idLength");
    this.idDigits = column("idDigits");

    this.coordScale = header.scales.coord;
    this.distanceScale = header.scales.distance;
    this.expectedScale = header.scales.expected;

    this.idOffset = new Uint32Array(this.count + 1);
    for (let row = 0; row < this.count; row += 1) {
      this.idOffset[row + 1] = this.idOffset[row] + this.idLength[row];
    }

    // Una pasada al cargar en vez de una división por fila y por consulta: la
    // desviación se filtra, se ordena y se pinta, y recalcularla cada vez
    // costaría más que el 1,2 MB que ocupa tenerla hecha.
    this.deviation = new Float64Array(this.count);
    for (let row = 0; row < this.count; row += 1) {
      const cents = this.expected[row];
      this.deviation[row] =
        cents === 0 ? NaN : (100 * (this.price[row] * this.expectedScale - cents)) / cents;
    }
  }

  // -- accesores de columna, con el nulo ya resuelto ----------------------

  latitudeOf(row: number): number {
    return this.lat[row] / this.coordScale;
  }

  longitudeOf(row: number): number {
    return this.lon[row] / this.coordScale;
  }

  sizeOf(row: number): number | null {
    const value = this.size[row];
    return value === NULL_U16 ? null : value;
  }

  roomsOf(row: number): number | null {
    const value = this.rooms[row];
    return value === NULL_U8 ? null : value;
  }

  bathroomsOf(row: number): number | null {
    const value = this.baths[row];
    return value === NULL_U8 ? null : value;
  }

  floorOf(row: number): number | null {
    const value = this.floor[row];
    return value === NULL_I8 ? null : value;
  }

  yearOf(row: number): number | null {
    const value = this.year[row];
    return value === NULL_U16 ? null : value;
  }

  conditionOf(row: number): Condition | null {
    const value = this.condition[row];
    return value === NULL_U8
      ? null
      : (this.header.vocabularies.conditions[value] as Condition);
  }

  cityOf(row: number): string | null {
    const value = this.city[row];
    return value === NULL_U8 ? null : this.header.vocabularies.cities[value];
  }

  distanceCenterOf(row: number): number | null {
    const value = this.distanceCenter[row];
    return value === NULL_U16 ? null : value / this.distanceScale;
  }

  distanceMetroOf(row: number): number | null {
    const value = this.distanceMetro[row];
    return value === NULL_U16 ? null : value / this.distanceScale;
  }

  expectedOf(row: number): number | null {
    const value = this.expected[row];
    return value === 0 ? null : value / this.expectedScale;
  }

  deviationOf(row: number): number | null {
    const value = this.deviation[row];
    return Number.isNaN(value) ? null : value;
  }

  operationOf(row: number): Operation {
    return this.header.vocabularies.operations[this.operation[row]] as Operation;
  }

  propertyTypeOf(row: number): PropertyType {
    return this.header.vocabularies.propertyTypes[this.ptype[row]] as PropertyType;
  }

  amenitiesOf(row: number): Amenity[] {
    const mask = this.amenities[row];
    const names = this.header.vocabularies.amenities;
    const result: Amenity[] = [];
    for (let bit = 0; bit < names.length; bit += 1) {
      if (mask & (1 << bit)) result.push(names[bit] as Amenity);
    }
    return result;
  }

  /**
   * ¿Es este anuncio el del id pedido?
   *
   * Compara los dígitos byte a byte contra la columna, sin construir la cadena.
   * Es lo que permite buscar por id —el detalle de un anuncio, los favoritos—
   * recorriendo las 149.923 filas sin tener que mantener un índice de 150.000
   * cadenas en memoria para algo que se usa un puñado de veces por sesión.
   */
  matchesId(row: number, digits: string): boolean {
    const length = this.idLength[row];
    if (length !== digits.length) return false;
    const start = this.idOffset[row];
    for (let index = 0; index < length; index += 1) {
      if (this.idDigits[start + index] !== digits.charCodeAt(index)) return false;
    }
    return true;
  }

  idOf(row: number): string {
    const start = this.idOffset[row];
    const end = this.idOffset[row + 1];
    let digits = "";
    for (let index = start; index < end; index += 1) {
      digits += String.fromCharCode(this.idDigits[index]);
    }
    return this.header.idPrefix + digits;
  }

  globalIdOf(row: number): string {
    return `${SOURCE}:${this.idOf(row)}`;
  }

  /**
   * La fila del anuncio con este identificador global, o -1.
   *
   * Acepta `fuente:id` o el id a secas, como hace `GET /listings/{id}`.
   */
  rowOfGlobalId(globalId: string): number {
    const separator = globalId.indexOf(":");
    if (separator !== -1) {
      if (globalId.slice(0, separator) !== SOURCE) return -1;
      globalId = globalId.slice(separator + 1);
    }
    const prefix = this.header.idPrefix;
    if (!globalId.startsWith(prefix)) return -1;
    const digits = globalId.slice(prefix.length);

    for (let row = 0; row < this.count; row += 1) {
      if (this.matchesId(row, digits)) return row;
    }
    return -1;
  }

  /**
   * El anuncio entero, tal y como lo devolvería la API.
   *
   * Se llama para una página de 24 tarjetas o para un detalle, nunca para el
   * conjunto filtrado: materializar 149.923 objetos es exactamente lo que el
   * formato columnar existe para no tener que hacer.
   */
  listingOf(row: number): Listing {
    const zone = this.cityOf(row);
    const rooms = this.roomsOf(row);
    const size = this.sizeOf(row);
    const propertyType = this.propertyTypeOf(row);
    const neighbourhoodIndex = this.neighbourhood[row];
    const neighbourhood =
      neighbourhoodIndex === NULL_U16
        ? null
        : this.header.vocabularies.neighbourhoods[neighbourhoodIndex];

    return {
      id: this.idOf(row),
      source: SOURCE,
      title: buildTitle(propertyType, rooms, size, zone ?? ""),
      // El dataset no trae ni la dirección ni el enlace al anuncio original.
      url: null,
      address: null,
      operation: this.operationOf(row),
      property_type: propertyType,
      price: this.price[row],
      size_m2: size,
      rooms,
      latitude: this.latitudeOf(row),
      longitude: this.longitudeOf(row),
      zone,
      neighbourhood_id: neighbourhood?.id ?? null,
      neighbourhood: neighbourhood?.name ?? null,
      // Todo el paquete se compiló a la vez, así que todos los anuncios
      // entraron a la vez. El orden «reciente» no sale de aquí: es el orden de
      // las filas del fichero, que ya viene ordenado por la compilación.
      ingested_at: this.header.generatedAt,
      bathrooms: this.bathroomsOf(row),
      floor: this.floorOf(row),
      year_built: this.yearOf(row),
      condition: this.conditionOf(row),
      distance_to_center_km: this.distanceCenterOf(row),
      distance_to_metro_km: this.distanceMetroOf(row),
      amenities: this.amenitiesOf(row),
      expected_price: this.expectedOf(row),
      price_deviation: this.deviationOf(row),
    };
  }
}

/**
 * El rótulo de un anuncio: «Piso de 1 hab. y 47 m2 en Madrid».
 *
 * Copia de `_title` en `backend/app/ingestion/sources/static_dataset.py`. El
 * dataset no trae texto de anuncio, así que el título se compone de lo que sí
 * trae; y se compone aquí en vez de viajar en el paquete porque son 149.923
 * cadenas de ~35 caracteres —5 MB— que se pueden derivar de cuatro columnas
 * que ya están.
 */
function buildTitle(
  propertyType: PropertyType,
  rooms: number | null,
  size: number | null,
  zone: string,
): string {
  const parts = [propertyType.charAt(0).toUpperCase() + propertyType.slice(1)];
  if (rooms) parts.push(`de ${rooms} hab.`);
  if (size) parts.push(rooms ? `y ${size} m2` : `de ${size} m2`);
  if (zone) parts.push(`en ${zone}`);
  return parts.join(" ");
}

export { NULL_U8, NULL_U16, NULL_I8, SOURCE };
