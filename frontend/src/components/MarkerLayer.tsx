import L from "leaflet";
import "leaflet.markercluster";
import { useEffect, useRef } from "react";
import { useMap } from "react-leaflet";

import { clusterIcon, markerIcon } from "../markers";
import type { MapCluster, MapPoint } from "../types/listing";
import { isBargain } from "../types/listing";

interface MarkerLayerProps {
  points: MapPoint[];
  clusters: MapCluster[];
  onSelect: (globalId: string) => void;
  /** Highlighted marker, so the map shows what the detail panel is describing. */
  selectedId: string | null;
  /** Clicking an aggregated cell narrows the search to it. */
  onCellSelect: (cell: MapCluster) => void;
}

/**
 * Draws whatever the server decided the map can take, in one of two modes.
 *
 * `points` — a marker per listing, grouped client-side by `leaflet.markercluster`
 * so nearby pins collapse as you zoom out.
 *
 * `clusters` — the server already grouped them, because there were too many to
 * send. Each cell becomes a single dot labelled with its count.
 *
 * Driven imperatively rather than as React children: a city view can hold
 * thousands of markers, and reconciling that many components on every pan is
 * far more work than handing Leaflet an array.
 */
export default function MarkerLayer({
  points,
  clusters,
  onSelect,
  selectedId,
  onCellSelect,
}: MarkerLayerProps) {
  const map = useMap();
  const groupRef = useRef<L.MarkerClusterGroup | null>(null);
  const cellsRef = useRef<L.LayerGroup | null>(null);
  /** globalId -> marker, so the selected one can be found without a scan. */
  const markersRef = useRef<Map<string, L.Marker>>(new Map());

  // Handlers are read through refs so changing them does not rebuild markers.
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;
  const cellRef = useRef(onCellSelect);
  cellRef.current = onCellSelect;

  useEffect(() => {
    const group = L.markerClusterGroup({
      chunkedLoading: true,
      // Past this zoom the plugin stops grouping and shows the real pins. Our
      // own icons are used there, so nothing falls back to Leaflet's default.
      disableClusteringAtZoom: 17,
      maxClusterRadius: 55,
      showCoverageOnHover: false,
    });
    const cells = L.layerGroup();

    groupRef.current = group;
    cellsRef.current = cells;
    map.addLayer(group);
    map.addLayer(cells);

    return () => {
      map.removeLayer(group);
      map.removeLayer(cells);
      groupRef.current = null;
      cellsRef.current = null;
    };
  }, [map]);

  // -- individual listings ---------------------------------------------------

  useEffect(() => {
    const group = groupRef.current;
    if (!group) return;

    const byId = new Map<string, L.Marker>();
    const markers = points.map((point) => {
      const bargain = isBargain(point);
      const marker = L.marker([point.latitude, point.longitude], {
        icon: markerIcon(point.property_type, point.operation, bargain),
        // Keyboard users can tab to markers; give them a name to hear.
        alt:
          `${point.property_type} · ${Math.round(point.price)} euros` +
          (bargain ? " · posible chollo" : ""),
        // Bargains float above their neighbours so a highlighted pin is not
        // hidden behind an ordinary one.
        zIndexOffset: bargain ? 1000 : 0,
        riseOnHover: true,
      });
      // No bindPopup: the detail lives in a fixed panel, not over the marker.
      marker.on("click", () => selectRef.current(point.global_id));
      byId.set(point.global_id, marker);
      return marker;
    });

    group.clearLayers();
    group.addLayers(markers);
    markersRef.current = byId;
  }, [points]);

  // -- aggregated cells ------------------------------------------------------

  useEffect(() => {
    const cells = cellsRef.current;
    if (!cells) return;

    cells.clearLayers();
    if (clusters.length === 0) return;

    const busiest = Math.max(...clusters.map((cell) => cell.count));

    for (const cell of clusters) {
      const marker = L.marker([cell.latitude, cell.longitude], {
        icon: clusterIcon(cell, busiest),
        title: `${cell.zone ?? "Esta zona"}: ${cell.count} anuncios · pulsa para verlos`,
        alt: `${cell.count} anuncios en ${cell.zone ?? "esta zona"}`,
      });
      marker.on("click", () => cellRef.current(cell));
      cells.addLayer(marker);
    }
  }, [clusters]);

  // -- selection -------------------------------------------------------------

  useEffect(() => {
    const marker = selectedId === null ? null : markersRef.current.get(selectedId);
    if (!marker) return;

    /*
     * Toggling a class beats rebuilding the icon: the marker keeps its identity
     * and the cluster plugin does not have to reindex anything.
     *
     * The element only exists while the marker is actually rendered — inside a
     * collapsed cluster, or off screen, `getElement()` is undefined. So the
     * class is reapplied whenever the plugin redraws, and removed on cleanup
     * rather than by hunting for the previously selected marker.
     */
    const paint = () => marker.getElement()?.classList.add("pin--selected");

    paint();
    map.on("zoomend moveend", paint);
    return () => {
      map.off("zoomend moveend", paint);
      marker.getElement()?.classList.remove("pin--selected");
    };
  }, [map, selectedId, points]);

  return null;
}
