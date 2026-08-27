/** Number and label formatting, in one place so both views agree. */

import type { Listing, Operation } from "./types/listing";

const EUR = new Intl.NumberFormat("es-ES", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
});

const COMPACT = new Intl.NumberFormat("es-ES", {
  notation: "compact",
  maximumFractionDigits: 1,
});

const INTEGER = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });

/**
 * "-32 %" — cuánto se aparta el precio pedido del estimado.
 *
 * Con signo siempre: sin él, "32 %" no dice si el piso está caro o barato, que
 * es justamente lo único que interesa saber.
 */
export function deviation(value: number | null): string {
  if (value === null) return "—";
  return `${value > 0 ? "+" : ""}${Math.round(value)} %`;
}

export function euros(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : EUR.format(value);
}

/** For axis labels and chips, where "1,2 M €" beats "1.200.000 €". */
export function shortEuros(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${COMPACT.format(value)} €`;
}

/**
 * Las mismas cifras, sin la unidad, para usar dentro de una tabla.
 *
 * En una tabla la unidad vive en la cabecera de la columna, y repetirla en cada
 * una de las 135 filas no añade información: añade ancho. Y el ancho es justo
 * lo que falta en una barra lateral de 336 px, donde «293,2 mil €» junto a
 * «4375 €/m²» deja las dos columnas pegadas y las cifras dejan de compararse,
 * que es para lo único que están ahí.
 */
export function shortEurosBare(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : COMPACT.format(value);
}

export function pricePerM2Bare(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : INTEGER.format(value);
}

export function count(value: number): string {
  return INTEGER.format(value);
}

export function squareMetres(value: number | null): string {
  return value === null ? "— m²" : `${INTEGER.format(value)} m²`;
}

export function pricePerM2(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${INTEGER.format(value)} €/m²`;
}

/** Rent is a monthly figure; sale is not. The suffix is the only difference. */
export function priceWithPeriod(price: number, operation: Operation): string {
  return operation === "alquiler" ? `${EUR.format(price)}/mes` : EUR.format(price);
}

export function rooms(value: number | null): string {
  if (value === null) return "— hab.";
  return value === 0 ? "Estudio" : `${value} hab.`;
}

/** "Piso · 3 hab. · 90 m²" — the one-line summary used on cards and popups. */
export function summarise(listing: Listing): string {
  return [
    listing.property_type.charAt(0).toUpperCase() + listing.property_type.slice(1),
    listing.rooms !== null ? rooms(listing.rooms) : null,
    listing.size_m2 !== null ? squareMetres(listing.size_m2) : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function unitPrice(listing: Listing): string | null {
  if (!listing.size_m2) return null;
  return pricePerM2(listing.price / listing.size_m2);
}
