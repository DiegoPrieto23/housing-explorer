import L from "leaflet";

import type { MapCluster, Operation, PropertyType } from "./types/listing";

/**
 * Marker icons drawn as inline SVG inside a Leaflet `divIcon`.
 *
 * This replaces Leaflet's default PNG pin, which is what used to appear —
 * wrongly, and often broken — once the cluster plugin stopped grouping markers
 * at high zoom. Bundlers rewrite the URLs of those PNGs and the retina variant
 * frequently resolves to nothing; an inline SVG has no URL to get wrong, scales
 * cleanly on any display, and can be recoloured per property type without
 * shipping ten more images.
 */

interface MarkerStyle {
  /** Fill of the pin. Distinct hue per family of property. */
  color: string;
  /** Path drawn in white inside the pin, on a 24x24 grid. */
  glyph: string;
  label: string;
}

/**
 * One hue and one glyph per type. Colour alone is not enough — roughly one man
 * in twelve cannot separate red from green — so every type also has a shape.
 */
const STYLES: Record<PropertyType, MarkerStyle> = {
  piso: {
    color: "#1a56db",
    label: "Piso",
    // A block of flats: three floors of windows.
    glyph: "M7 4h10v16H7z M9.5 7h2v2h-2z M12.5 7h2v2h-2z M9.5 11h2v2h-2z M12.5 11h2v2h-2z M10.5 15h3v5h-3z",
  },
  casa: {
    color: "#0e7c5a",
    label: "Casa",
    // A pitched roof over a door.
    glyph: "M12 3 3.5 10.5h2V20h13v-9.5h2z M10.5 13h3v7h-3z",
  },
  estudio: {
    color: "#b45309",
    label: "Estudio",
    // A single open square: one room.
    glyph: "M5 5h14v14H5z M8 8h8v8H8z",
  },
  duplex: {
    color: "#7c3aed",
    label: "Dúplex",
    // Two stacked levels joined by a stair.
    glyph: "M4 4h16v7H4z M4 13h16v7H4z M14 6h3v3h-3z M7 15h3v3H7z",
  },
  atico: {
    color: "#c2410c",
    label: "Ático",
    // A terrace on top of a building.
    glyph: "M5 9h14v11H5z M7 4h10v3H7z M8 12h3v3H8z M13 12h3v3h-3z",
  },
  habitacion: {
    color: "#be185d",
    label: "Habitación",
    // A bed.
    glyph: "M4 10h16v7h-2v-2H6v2H4z M6 6h5v3H6z",
  },
  terreno: {
    color: "#4d7c0f",
    label: "Terreno",
    // A plot boundary with a tree.
    glyph: "M3 17h18v3H3z M12 4l4 8H8z M11 12h2v5h-2z",
  },
  garaje: {
    color: "#525252",
    label: "Garaje",
    // A car under a roof.
    glyph: "M4 9 12 4l8 5v2H4z M6 13h12l1 4H5z M7 18h3v2H7z M14 18h3v2h-3z",
  },
  local: {
    color: "#0369a1",
    label: "Local",
    // A shopfront with an awning.
    glyph: "M4 4h16v4H4z M5 9h14v11H5z M8 12h4v8H8z M14 12h3v4h-3z",
  },
  otro: {
    color: "#6b7280",
    label: "Otro",
    glyph: "M12 4l8 8-8 8-8-8z",
  },
};

export const MARKER_STYLES = STYLES;

/** Every type, in the order the legend lists them. */
export const PROPERTY_TYPES = Object.keys(STYLES) as PropertyType[];

function styleFor(type: PropertyType): MarkerStyle {
  return STYLES[type] ?? STYLES.otro;
}

export function markerLabel(type: PropertyType): string {
  return styleFor(type).label;
}

export function markerColor(type: PropertyType): string {
  return styleFor(type).color;
}

/** The pin outline: a teardrop 30x40, anchored at its point. */
const PIN_PATH =
  "M15 39C15 39 28 24.5 28 15A13 13 0 1 0 2 15C2 24.5 15 39 15 39Z";

/** Ring drawn around a bargain pin. Deliberately not one of the type colours. */
export const BARGAIN_COLOR = "#e11d48";

function pinSvg(style: MarkerStyle, rented: boolean, bargain: boolean): string {
  // Rentals get a hollow pin so operation is readable without opening anything.
  const fill = rented ? "#ffffff" : style.color;
  const glyphFill = rented ? style.color : "#ffffff";

  // A bargain keeps its property-type colour and glyph and gains a thick
  // contrasting outline. Recolouring it instead would make the marker say
  // "bargain" at the cost of no longer saying "flat", and the two facts are
  // independent; an outline stacks on top of the colour rather than replacing it.
  const stroke = bargain ? BARGAIN_COLOR : style.color;
  const width = bargain ? 3.5 : 2;

  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="30" height="40" viewBox="0 0 30 40">`,
    `<path d="${PIN_PATH}" fill="${fill}" stroke="${stroke}" stroke-width="${width}"/>`,
    `<g transform="translate(7.5 7.5) scale(0.625)" fill="${glyphFill}">`,
    `<path d="${style.glyph}"/>`,
    `</g>`,
    bargain
      ? `<circle cx="24" cy="7" r="5.5" fill="${BARGAIN_COLOR}" stroke="#fff" stroke-width="1.5"/>`
      : "",
    `</svg>`,
  ].join("");
}

/**
 * Icons are built once per (type, operation) and reused across every marker.
 *
 * A city view can hold six thousand markers; letting each one build its own
 * `divIcon` would mean six thousand identical HTML strings parsed for nothing.
 */
const iconCache = new Map<string, L.DivIcon>();

export function markerIcon(
  type: PropertyType,
  operation: Operation,
  bargain = false,
): L.DivIcon {
  const key = `${type}:${operation}:${bargain}`;
  const cached = iconCache.get(key);
  if (cached) return cached;

  const icon = L.divIcon({
    html: pinSvg(styleFor(type), operation === "alquiler", bargain),
    className: `pin${bargain ? " pin--bargain" : ""}`,
    iconSize: [30, 40],
    // The point of the teardrop is what sits on the coordinate, not its centre.
    iconAnchor: [15, 39],
    popupAnchor: [0, -34],
  });

  iconCache.set(key, icon);
  return icon;
}

/**
 * A dot for an aggregated grid cell, sized by how many listings it holds.
 *
 * The radius grows with the square root of the count so that area, not width,
 * tracks the number — the way a proportional symbol map is supposed to read.
 */
export function clusterIcon(cell: MapCluster, maxCount: number): L.DivIcon {
  const share = Math.sqrt(cell.count) / Math.sqrt(Math.max(maxCount, 1));
  const size = Math.round(28 + share * 34);
  const label = compact(cell.count);

  return L.divIcon({
    html:
      `<span class="cell__inner" style="width:${size}px;height:${size}px">` +
      `<span class="cell__count">${label}</span></span>`,
    className: "cell",
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

/** 1234 -> "1,2k". Keeps the number inside the dot at any count. */
function compact(value: number): string {
  if (value < 1000) return String(value);
  if (value < 10_000) return `${(value / 1000).toFixed(1).replace(".", ",")}k`;
  if (value < 1_000_000) return `${Math.round(value / 1000)}k`;
  return `${(value / 1_000_000).toFixed(1).replace(".", ",")}M`;
}
