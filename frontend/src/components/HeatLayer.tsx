import L from "leaflet";
import { useEffect, useRef } from "react";
import { useMap } from "react-leaflet";

import { shortEuros } from "../format";
import type { MapCluster } from "../types/listing";

/**
 * Price per square metre, drawn as coloured cells.
 *
 * Deliberately a choropleth of cell averages and not a `Leaflet.heat` blur.
 * A heat layer paints *density*: it adds up overlapping point weights, so a
 * neighbourhood with many cheap flats glows brighter than one with a few
 * expensive ones. For a ratio like €/m² that is exactly backwards — the answer
 * would be "where are there lots of listings", dressed up as "where is it
 * expensive". Each cell here is one number, averaged in SQL over the listings
 * inside it, and its colour means that number and nothing else.
 *
 * Rendered as canvas (the map sets `preferCanvas`), so two thousand rectangles
 * cost two thousand fills rather than two thousand DOM nodes.
 */

interface HeatLayerProps {
  cells: MapCluster[];
  onSelect: (cell: MapCluster) => void;
}

/**
 * A sequential ramp, light to dark, colour-blind safe.
 *
 * Sequential and not a rainbow: €/m² is an ordered quantity, and a rainbow has
 * no order anyone can read off it — people disagree about whether green is more
 * or less than orange, but nobody disagrees that darker is more.
 */
const RAMP = [
  "#fff7ec",
  "#fee8c8",
  "#fdd49e",
  "#fdbb84",
  "#fc8d59",
  "#ef6548",
  "#d7301f",
  "#990000",
] as const;

/** Cells with no declared area anywhere inside them. Grey, never a ramp colour. */
const UNKNOWN = "#9ca3af";

export interface HeatScale {
  /** Upper bound of each band, in €/m². `RAMP.length - 1` of them. */
  breaks: number[];
  colours: readonly string[];
  min: number;
  max: number;
}

/**
 * Bands by quantile, not by equal width.
 *
 * Price per m² is skewed: Madrid's centre is three times Valencia's outskirts,
 * and a handful of cells sit far above the rest. Cutting the range into eight
 * equal slices would put almost every cell in the first two and leave six
 * colours describing a dozen outliers. Quantiles spend the colours where the
 * data is, so the map separates neighbourhoods instead of separating one
 * penthouse from everything else.
 */
export function buildScale(cells: MapCluster[]): HeatScale | null {
  const values = cells
    .map((cell) => cell.avg_price_per_m2)
    .filter((value): value is number => value !== null)
    .sort((a, b) => a - b);

  if (values.length === 0) return null;

  const bands = RAMP.length;
  const breaks: number[] = [];
  for (let index = 1; index < bands; index += 1) {
    breaks.push(values[Math.floor((values.length * index) / bands)]);
  }

  return { breaks, colours: RAMP, min: values[0], max: values[values.length - 1] };
}

export function colourFor(value: number | null, scale: HeatScale): string {
  if (value === null) return UNKNOWN;
  let band = 0;
  while (band < scale.breaks.length && value >= scale.breaks[band]) band += 1;
  return scale.colours[band];
}

export default function HeatLayer({ cells, onSelect }: HeatLayerProps) {
  const map = useMap();
  const groupRef = useRef<L.LayerGroup | null>(null);

  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  useEffect(() => {
    const group = L.layerGroup();
    groupRef.current = group;
    map.addLayer(group);
    return () => {
      map.removeLayer(group);
      groupRef.current = null;
    };
  }, [map]);

  useEffect(() => {
    const group = groupRef.current;
    if (!group) return;

    group.clearLayers();
    const scale = buildScale(cells);
    if (!scale) return;

    for (const cell of cells) {
      // The cell's real extent, when the server sent it. Falling back to a
      // square around the centroid would misplace the edges of a sparse cell,
      // where the listings sit in one corner of the grid square.
      const bounds: L.LatLngBoundsExpression = [
        [cell.lat_min ?? cell.latitude, cell.lon_min ?? cell.longitude],
        [cell.lat_max ?? cell.latitude, cell.lon_max ?? cell.longitude],
      ];

      const rectangle = L.rectangle(bounds, {
        color: "#ffffff",
        weight: 0.5,
        fillColor: colourFor(cell.avg_price_per_m2, scale),
        fillOpacity: 0.68,
      });

      rectangle.bindTooltip(
        cell.avg_price_per_m2 === null
          ? `${cell.count} anuncios · sin superficie declarada`
          : `${cell.avg_price_per_m2.toLocaleString("es-ES")} €/m²` +
              ` · ${cell.count} anuncios · media ${shortEuros(cell.avg_price)}`,
        { sticky: true },
      );
      rectangle.on("click", () => selectRef.current(cell));
      group.addLayer(rectangle);
    }
  }, [cells]);

  return null;
}
