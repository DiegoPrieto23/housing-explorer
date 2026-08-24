/**
 * Stand-in cover images.
 *
 * idealista18 ships coordinates and attributes, not photographs, so there is
 * nothing real to show. Rather than one grey rectangle repeated 150.000 times,
 * each listing gets a gradient derived from its own id plus a house glyph: the
 * cards stay visually distinguishable when scrolling, and nobody can mistake
 * the result for a real photo. It is an inline SVG data URI, so it costs no
 * request and works offline.
 */

import type { Listing } from "./types/listing";
import { globalId } from "./types/listing";

/** FNV-1a: short, stable, and good enough to spread ids across the hue circle. */
function hash(value: string): number {
  let result = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 0x01000193);
  }
  return result >>> 0;
}

const GLYPH =
  "M100 44 L146 82 L146 140 L118 140 L118 108 L82 108 L82 140 L54 140 L54 82 Z";

export function placeholderImage(listing: Listing): string {
  const seed = hash(globalId(listing));
  const hue = seed % 360;
  // A second hue nearby keeps the gradient a shade rather than a rainbow.
  const partner = (hue + 28) % 360;
  const id = `g${seed.toString(36)}`;

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 150" preserveAspectRatio="xMidYMid slice">
<defs><linearGradient id="${id}" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="hsl(${hue} 42% 62%)"/>
<stop offset="1" stop-color="hsl(${partner} 46% 44%)"/>
</linearGradient></defs>
<rect width="200" height="150" fill="url(%23${id})"/>
<path d="${GLYPH}" fill="hsl(${hue} 30% 96%)" fill-opacity="0.35"/>
</svg>`;

  // Only the characters that would break an unquoted data URI; encoding the
  // whole thing would triple its size for no benefit.
  return `data:image/svg+xml;charset=utf-8,${svg
    .replace(/\n/g, "")
    .replace(/"/g, "'")
    .replace(/</g, "%3C")
    .replace(/>/g, "%3E")
    .replace(/#/g, "%23")}`;
}
