/**
 * Portadas de sustitución.
 *
 * idealista18 trae coordenadas y atributos, no fotografías, así que no hay
 * nada real que enseñar. Lo que se dibuja en su lugar es el TIPO DE INMUEBLE:
 * el mismo glifo y el mismo tono que lleva su chincheta en el mapa.
 *
 * Antes cada anuncio recibía un degradado de tono aleatorio, sacado por hash
 * de su id y repartido por el círculo cromático entero. Distinguía unas
 * tarjetas de otras, sí, pero con una variable que no significaba nada: una
 * rejilla con una casa rosa, una oliva, una marrón y una turquesa seguidas no
 * dice nada sobre las viviendas. Ahora el color es información —piso azul,
 * casa verde, ático naranja— y la tarjeta se lee igual que su pin.
 *
 * Es un SVG en línea como data URI: no cuesta una petición y funciona sin red.
 */

import { MARKER_STYLES } from "./markers";
import type { Listing, PropertyType } from "./types/listing";

/**
 * El tono del tipo, llevado a una luminosidad intermedia.
 *
 * Los colores de las chinchetas son oscuros a propósito: van sobre teselas de
 * mapa claras y llevan un glifo blanco encima. Puestos tal cual sobre la
 * portada de una tarjeta se hundirían en el tema oscuro. Fijando la luminosidad
 * al 56 % el mismo tono se lee sobre `--surface-alt` en claro Y en oscuro, que
 * es justo lo que hace falta: el fondo del SVG deja pasar la superficie de la
 * tarjeta, así que la portada cambia con el tema sin que existan dos versiones
 * de la imagen.
 *
 * Devuelve hexadecimal y no `hsl(...)` porque un `%` dentro de un data URI sin
 * codificar se lee como el principio de un escape: `42%` se convertiría en
 * `%42`, o sea en la letra B, y el color saldría roto.
 */
function midTone(hex: string): string {
  const value = Number.parseInt(hex.slice(1), 16);
  const r = ((value >> 16) & 255) / 255;
  const g = ((value >> 8) & 255) / 255;
  const b = (value & 255) / 255;

  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const span = max - min;

  let hue = 0;
  if (span !== 0) {
    if (max === r) hue = ((g - b) / span) % 6;
    else if (max === g) hue = (b - r) / span + 2;
    else hue = (r - g) / span + 4;
  }
  hue = hue * 60;
  if (hue < 0) hue += 360;

  // El gris del tipo «otro» no tiene tono que conservar; se queda gris.
  const saturation = span === 0 ? 0 : 0.42;
  return hslToHex(hue, saturation, 0.56);
}

function hslToHex(hue: number, saturation: number, lightness: number): string {
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
  const second = chroma * (1 - Math.abs(((hue / 60) % 2) - 1));
  const base = lightness - chroma / 2;

  const sector = Math.floor(hue / 60) % 6;
  const [r, g, b] = (
    [
      [chroma, second, 0],
      [second, chroma, 0],
      [0, chroma, second],
      [0, second, chroma],
      [second, 0, chroma],
      [chroma, 0, second],
    ] as const
  )[sector];

  const channel = (part: number) =>
    Math.round((part + base) * 255)
      .toString(16)
      .padStart(2, "0");

  return `#${channel(r)}${channel(g)}${channel(b)}`;
}

/*
 * La banda, no la foto.
 *
 * 200×84 y no un rectángulo de proporciones fotográficas: esto no es una
 * imagen del inmueble ni quiere que se confunda con una. Es una franja de
 * identificación, así que ocupa lo que cuesta llevar un glifo y un tono, y le
 * deja el sitio de la tarjeta a lo que sí son datos. `--card-media` en el CSS
 * lleva esta misma proporción; si cambia una tiene que cambiar la otra, porque
 * `object-fit: cover` recortaría el glifo.
 */
const WIDTH = 200;
const HEIGHT = 84;

/** Un color por tipo, calculado una vez y no en cada una de las 24 tarjetas. */
const TONES = new Map<string, string>();

function toneFor(type: PropertyType): string {
  const cached = TONES.get(type);
  if (cached) return cached;
  const tone = midTone((MARKER_STYLES[type] ?? MARKER_STYLES.otro).color);
  TONES.set(type, tone);
  return tone;
}

export function placeholderImage(listing: Listing): string {
  const type = (listing.property_type as PropertyType) ?? "otro";
  const style = MARKER_STYLES[type] ?? MARKER_STYLES.otro;
  const tone = toneFor(type);

  // El glifo viene dibujado sobre una retícula de 24×24 en `markers.ts`. Aquí
  // se escala y se centra en la banda.
  const scale = 2.6;
  const offsetX = (WIDTH - 24 * scale) / 2;
  const offsetY = (HEIGHT - 24 * scale) / 2;

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${WIDTH} ${HEIGHT}" preserveAspectRatio="xMidYMid slice">
<rect width="${WIDTH}" height="${HEIGHT}" fill="${tone}" fill-opacity="0.14"/>
<g transform="translate(${offsetX} ${offsetY}) scale(${scale})" fill="${tone}" fill-opacity="0.85">
<path fill-rule="evenodd" d="${style.glyph}"/>
</g>
<rect y="${HEIGHT - 3}" width="${WIDTH}" height="3" fill="${tone}" fill-opacity="0.7"/>
</svg>`;

  // Sólo los caracteres que romperían un data URI sin comillas; codificarlo
  // entero triplicaría su tamaño sin ganar nada.
  return `data:image/svg+xml;charset=utf-8,${svg
    .replace(/\n/g, "")
    .replace(/"/g, "'")
    .replace(/</g, "%3C")
    .replace(/>/g, "%3E")
    .replace(/#/g, "%23")}`;
}
