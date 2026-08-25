/** Los mensajes que cruzan entre la aplicación y el worker de datos. */

import type { Order } from "../types";
import type { QueryFilters } from "./query";

export type Request =
  | { id: number; op: "load"; payloadUrl: string }
  | {
      id: number;
      op: "listings";
      filters: QueryFilters;
      limit: number;
      offset: number;
      order: Order;
    }
  | { id: number; op: "stats"; filters: QueryFilters; bins: number }
  | { id: number; op: "map"; filters: QueryFilters; zoom: number; heat: boolean }
  | { id: number; op: "facets" }
  | { id: number; op: "listing"; globalId: string };

/**
 * Una petición sin su `id`, que es lo que el cliente compone antes de numerarla.
 *
 * El `T extends unknown` no sobra: sin él, `Omit` sobre una unión se aplica al
 * tipo unificado y deja solo las propiedades **comunes** a todas las variantes
 * —es decir, ninguna—, y montar un mensaje concreto dejaría de compilar. Con él
 * el condicional distribuye y cada variante conserva las suyas.
 */
export type RequestBody<T = Request> = T extends unknown ? Omit<T, "id"> : never;

export type Response =
  | { id: number; ok: true; value: unknown }
  | { id: number; ok: false; status: number; message: string };
