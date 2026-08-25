/**
 * Comprueba que el motor del navegador responde lo mismo que SQL.
 *
 * `src/api/static/query.ts` es un puerto de `backend/app/storage/repository.py`,
 * y un puerto es una copia que puede desviarse en silencio. Un `>=` que debería
 * ser `>`, un nulo que en SQL no cumple la comparación y en JavaScript sí, una
 * media de ratios que se convierte en un ratio de medias: nada de eso rompe
 * nada, solo hace que la web publicada enseñe números distintos de los de la
 * API. Esto es lo que lo detecta.
 *
 * La referencia la escribe `scripts/build_static_data.py` en `checks.json`,
 * calculada **en SQL sobre la base de datos**, con las consultas escritas a
 * mano y aparte. Aquí se vuelven a hacer las mismas preguntas con el motor de
 * TypeScript y se comparan los dos resultados.
 *
 *     npm run verify:static
 */

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { gunzipSync } from "node:zlib";

import { Dataset } from "../src/api/static/dataset";
import type { PayloadHeader } from "../src/api/static/payload";
import { QueryEngine, type QueryFilters } from "../src/api/static/query";

// Relativo al directorio de trabajo y no a `import.meta.url`: esto se ejecuta
// empaquetado, y el bundle vive en `node_modules/.cache`, que no está donde
// está el fuente. `npm run` siempre arranca en `frontend/`.
const ROOT = process.cwd();
const PAYLOAD = join(ROOT, "public", "data", "listings.bin.gz");
const CHECKS = join(ROOT, "tools", "checks.json");

/** Cuánto puede separarse un número del suyo. */
const TOLERANCE = 1e-6;

interface Check {
  name: string;
  filters: QueryFilters;
  expected: {
    count: number;
    avg_price: number | null;
    min_price: number | null;
    max_price: number | null;
    avg_price_per_m2: number | null;
    p25_price: number | null;
    median_price: number | null;
    p75_price: number | null;
    p99_price: number | null;
    by_rooms: { bucket: number; count: number; avg_price: number; avg_price_per_m2: number }[];
    ascensor: { count: number; with_it: number; without_it: number } | null;
  };
}

/**
 * Decodifica el paquete desde disco.
 *
 * Repite lo que hace `payload.ts` porque aquél va sobre `fetch` y
 * `DecompressionStream`, que son del navegador. Lo que **no** se repite es nada
 * de la lógica de consulta: `Dataset` y `QueryEngine` se importan tal cual, que
 * es lo único que hace que esta comprobación pruebe algo.
 */
function loadPayload(): { header: PayloadHeader; columns: Record<string, ArrayLike<number>> } {
  const raw = gunzipSync(readFileSync(PAYLOAD));
  // Copia a un ArrayBuffer propio: el Buffer de Node es una vista sobre un
  // fondo común compartido cuyo `byteOffset` no tiene por qué estar alineado,
  // y las vistas de columna sí lo necesitan.
  const buffer = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength) as ArrayBuffer;

  const magic = new TextDecoder().decode(new Uint8Array(buffer, 0, 4));
  if (magic !== "HEXP") throw new Error(`${PAYLOAD} no es un paquete válido`);

  const headerLength = new DataView(buffer).getUint32(4, true);
  const header = JSON.parse(
    new TextDecoder().decode(new Uint8Array(buffer, 8, headerLength)),
  ) as PayloadHeader;

  const views = {
    u8: Uint8Array,
    i8: Int8Array,
    u16: Uint16Array,
    i16: Int16Array,
    u32: Uint32Array,
    i32: Int32Array,
  } as const;

  const base = 8 + headerLength;
  const columns: Record<string, ArrayLike<number>> = {};
  for (const column of header.columns) {
    const View = views[column.type as keyof typeof views];
    columns[column.name] = new View(
      buffer,
      base + column.offset,
      column.length / View.BYTES_PER_ELEMENT,
    );
  }
  return { header, columns };
}

let failures = 0;

function compare(scenario: string, field: string, actual: unknown, expected: unknown): void {
  let ok: boolean;
  if (typeof expected === "number" && typeof actual === "number") {
    // Relativa y no absoluta: un precio medio es un número de seis cifras y
    // una diferencia de un céntimo entre sumar en un orden u otro es ruido de
    // coma flotante, no un error.
    ok = Math.abs(actual - expected) <= TOLERANCE * Math.max(1, Math.abs(expected));
  } else {
    ok = JSON.stringify(actual) === JSON.stringify(expected);
  }

  if (!ok) {
    failures += 1;
    console.error(`  ✗ ${scenario} · ${field}`);
    console.error(`      SQL      ${JSON.stringify(expected)}`);
    console.error(`      motor    ${JSON.stringify(actual)}`);
  }
}

function main(): void {
  const payload = loadPayload();
  const engine = new QueryEngine(new Dataset(payload));
  const checks = JSON.parse(readFileSync(CHECKS, "utf-8")) as Check[];

  console.log(
    `Paquete: ${payload.header.count.toLocaleString("es-ES")} anuncios, ` +
      `compilado el ${payload.header.generatedAt}`,
  );
  console.log(`Comprobando ${checks.length} consultas contra SQL\n`);

  for (const check of checks) {
    const stats = engine.stats(check.filters, 20);
    const { overall } = stats;
    const expected = check.expected;

    compare(check.name, "count", overall.count, expected.count);
    for (const field of [
      "avg_price",
      "min_price",
      "max_price",
      "avg_price_per_m2",
      "p25_price",
      "median_price",
      "p75_price",
      "p99_price",
    ] as const) {
      compare(check.name, field, overall[field], expected[field]);
    }

    compare(
      check.name,
      "by_rooms",
      stats.by_rooms.map(({ bucket, count, avg_price, avg_price_per_m2 }) => ({
        bucket,
        count,
        avg_price,
        avg_price_per_m2,
      })),
      expected.by_rooms,
    );

    const lift = stats.amenities.find((entry) => entry.amenity === "ascensor") ?? null;
    compare(
      check.name,
      "ascensor",
      lift && { count: lift.count, with_it: lift.with_it, without_it: lift.without_it },
      expected.ascensor,
    );

    // El total de la lista y el del mapa tienen que ser el mismo número. Es la
    // promesa que hace la interfaz al no poner tope de anuncios, y sale mal en
    // cuanto el mapa filtra con una condición que la lista no tiene.
    const page = engine.page(check.filters, 24, 0, "reciente");
    const map = engine.map(check.filters, 12, false);
    compare(check.name, "total de la lista", page.total, expected.count);
    compare(check.name, "total del mapa", map.total, expected.count);

    console.log(
      `  ${check.name}: ${overall.count.toLocaleString("es-ES")} anuncios` +
        (overall.median_price === null
          ? ""
          : `, mediana ${Math.round(overall.median_price).toLocaleString("es-ES")} €`),
    );
  }

  // Los ids son la clave de los favoritos y de la ficha de detalle, así que
  // tienen que sobrevivir al empaquetado intactos: se comprueba que uno se
  // reconstruye y que se puede volver a encontrar por él.
  const first = engine.page({}, 1, 0, "reciente").items[0];
  const found = engine.listing(`${first.source}:${first.id}`);
  compare("ids", "ida y vuelta", found?.id ?? null, first.id);

  const digest = createHash("sha256").update(readFileSync(PAYLOAD)).digest("hex");
  console.log(`\nsha256 del paquete: ${digest.slice(0, 16)}…`);

  if (failures > 0) {
    console.error(`\n${failures} comprobaciones fallidas.`);
    process.exit(1);
  }
  console.log("Todo cuadra.");
}

main();
