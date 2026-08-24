import L from "leaflet";
import { useEffect, useRef } from "react";
import { useMap } from "react-leaflet";

/**
 * Freehand area drawing, the way a property portal does it.
 *
 * Written against Leaflet directly rather than pulling in `leaflet-draw`: all
 * that is needed is "hold the mouse down and trace a shape", and the plugin
 * brings a whole toolbar, its own icon sprites and a vertex-editing UI that
 * would have to be styled and then hidden again.
 *
 * While drawing, map dragging is switched off — otherwise the gesture pans the
 * map instead of drawing on it.
 */

interface DrawControlProps {
  /** Whether the map is currently in drawing mode. */
  active: boolean;
  /** Receives the finished shape, encoded as `lat,lon;lat,lon;...`. */
  onDrawn: (polygon: string) => void;
  /** Existing shape to keep visible, or null. */
  polygon: string | null;
}

/** Vertices closer together than this (in pixels) add nothing but payload. */
const MIN_PIXEL_GAP = 8;

/** Below this a "shape" is a stray click, not an area. */
const MIN_VERTICES = 3;

const SHAPE_STYLE: L.PathOptions = {
  color: "#1a56db",
  weight: 2,
  fillColor: "#1a56db",
  fillOpacity: 0.08,
};

function encode(points: L.LatLng[]): string {
  // Six decimals is about 11 cm — far past what a hand-drawn shape means, and
  // it keeps the query string short enough to stay well inside URL limits.
  return points.map((p) => `${p.lat.toFixed(6)},${p.lng.toFixed(6)}`).join(";");
}

function decode(encoded: string): L.LatLngExpression[] {
  return encoded.split(";").map((pair) => {
    const [lat, lon] = pair.split(",").map(Number);
    return [lat, lon] as L.LatLngExpression;
  });
}

export default function DrawControl({ active, onDrawn, polygon }: DrawControlProps) {
  const map = useMap();

  const drawnRef = useRef(onDrawn);
  drawnRef.current = onDrawn;

  // Draw the committed shape, so it stays on screen while the map is panned.
  useEffect(() => {
    if (!polygon) return;

    const shape = L.polygon(decode(polygon), { ...SHAPE_STYLE, interactive: false });
    shape.addTo(map);
    return () => {
      shape.remove();
    };
  }, [map, polygon]);

  // The drawing gesture itself.
  useEffect(() => {
    if (!active) return;

    const container = map.getContainer();
    container.classList.add("map--drawing");

    let tracing = false;
    let points: L.LatLng[] = [];
    let lastPoint: L.Point | null = null;
    let trace: L.Polyline | null = null;

    const start = (event: L.LeafletMouseEvent) => {
      tracing = true;
      points = [event.latlng];
      lastPoint = map.latLngToContainerPoint(event.latlng);
      trace = L.polyline([event.latlng], { ...SHAPE_STYLE, interactive: false }).addTo(map);
      map.dragging.disable();
    };

    const extend = (event: L.LeafletMouseEvent) => {
      if (!tracing || !trace) return;

      const pixel = map.latLngToContainerPoint(event.latlng);
      // Thinning as we go, not afterwards: a 30-second trace at 60fps would
      // otherwise collect two thousand points before anyone looked at them.
      if (lastPoint && pixel.distanceTo(lastPoint) < MIN_PIXEL_GAP) return;

      lastPoint = pixel;
      points.push(event.latlng);
      trace.addLatLng(event.latlng);
    };

    const finish = () => {
      if (!tracing) return;
      tracing = false;
      map.dragging.enable();
      trace?.remove();
      trace = null;

      if (points.length >= MIN_VERTICES) drawnRef.current(encode(points));
      points = [];
      lastPoint = null;
    };

    map.on("mousedown", start);
    map.on("mousemove", extend);
    map.on("mouseup", finish);
    // Releasing outside the map still ends the gesture; without this the map
    // would stay undraggable until the next click.
    document.addEventListener("mouseup", finish);

    return () => {
      map.off("mousedown", start);
      map.off("mousemove", extend);
      map.off("mouseup", finish);
      document.removeEventListener("mouseup", finish);
      container.classList.remove("map--drawing");
      trace?.remove();
      map.dragging.enable();
    };
  }, [active, map]);

  return null;
}
