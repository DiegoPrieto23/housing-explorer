import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchFacets,
  fetchListings,
  fetchListingsByIds,
  fetchMapData,
  fetchNeighbourhoods,
  fetchPointsOfInterest,
  fetchStats,
} from "./api/client";
import type { Order } from "./api/client";
import DetailCard from "./components/DetailCard";
import FilterPanel from "./components/FilterPanel";
import ListView from "./components/ListView";
import LoadingScreen, { type LoadingStep } from "./components/LoadingScreen";
import MapView, {
  DEFAULT_OVERLAYS,
  INITIAL_CAMERA,
  type Camera,
  type MapLayer,
  type Overlays,
} from "./components/MapView";
import StatsPanel from "./components/StatsPanel";
import { EMPTY_FILTERS, filtersKey, type BoundingBox, type Filters } from "./filters";
import { TYPING_DELAY, useDebounced, VIEWPORT_DELAY } from "./hooks/useDebounced";
import { useFavourites } from "./hooks/useFavourites";
import { useResource } from "./hooks/useResource";
import type { Listing, MapCluster, NeighbourhoodFacet, ZoneFacet } from "./types/listing";
import { globalId } from "./types/listing";

type View = "map" | "list" | "favourites";

/** Cards per page in the list view. */
const PAGE_SIZE = 24;
/** Histogram resolution; enough bars to show a shape in a narrow sidebar. */
const HISTOGRAM_BINS = 24;

/** The rectangle a map cell covers, or null if the server did not send one. */
function cellBounds(cell: MapCluster): BoundingBox | null {
  if (
    cell.lat_min === null ||
    cell.lat_max === null ||
    cell.lon_min === null ||
    cell.lon_max === null
  ) {
    return null;
  }
  return {
    lat_min: cell.lat_min,
    lat_max: cell.lat_max,
    lon_min: cell.lon_min,
    lon_max: cell.lon_max,
  };
}

/** A box as the four corners the `poligono` filter expects. */
function encodeRectangle(box: BoundingBox): string {
  const corners: [number, number][] = [
    [box.lat_min, box.lon_min],
    [box.lat_min, box.lon_max],
    [box.lat_max, box.lon_max],
    [box.lat_max, box.lon_min],
  ];
  return corners.map(([lat, lon]) => `${lat.toFixed(6)},${lon.toFixed(6)}`).join(";");
}

/** The smallest box containing every neighbourhood passed in. */
function unionBounds(entries: NeighbourhoodFacet[]): BoundingBox | null {
  if (entries.length === 0) return null;
  return {
    lat_min: Math.min(...entries.map((entry) => entry.lat_min)),
    lat_max: Math.max(...entries.map((entry) => entry.lat_max)),
    lon_min: Math.min(...entries.map((entry) => entry.lon_min)),
    lon_max: Math.max(...entries.map((entry) => entry.lon_max)),
  };
}

/** A zone's bounds, or null when the zone has no coordinates on record. */
function zoneBounds(zone: ZoneFacet | undefined): BoundingBox | null {
  if (!zone || zone.lat_min === null || zone.lon_min === null) return null;
  return {
    lat_min: zone.lat_min,
    lat_max: zone.lat_max as number,
    lon_min: zone.lon_min,
    lon_max: zone.lon_max as number,
  };
}

export default function App() {
  const [view, setView] = useState<View>("map");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [camera, setCamera] = useState<Camera>(INITIAL_CAMERA);
  const [bbox, setBbox] = useState<BoundingBox | null>(null);
  const [zoom, setZoom] = useState(INITIAL_CAMERA.zoom);
  const [searchInView, setSearchInView] = useState(true);
  const [pageIndex, setPageIndex] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [flyTo, setFlyTo] = useState<BoundingBox | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [layer, setLayer] = useState<MapLayer>("marcadores");
  const [overlays, setOverlays] = useState<Overlays>(DEFAULT_OVERLAYS);

  const favourites = useFavourites();

  /**
   * The map is always bounded by its own viewport — the server would otherwise
   * aggregate the whole country into cells no matter how far in you had zoomed.
   * The checkbox decides whether that same box also constrains the list and the
   * statistics, which is what makes "los mismos datos filtrados" true on demand
   * rather than by accident.
   *
   * A drawn polygon is different: it lives in `filters`, so it applies to every
   * view at once and survives panning the map away from it.
   */
  /*
   * Debounced at the source, not at the consumer.
   *
   * Each kind of change gets the delay its gesture deserves: typing in a box is
   * settled in 300 ms, dragging the map is not settled until 600 ms. Composing
   * the already-settled values means the list and the statistics inherit the
   * long window when the viewport is what moved, and the short one when the
   * panel is — which debouncing the finished filter objects instead could not
   * express, because by then the two changes look identical.
   */
  const debouncedFilters = useDebounced(filters, TYPING_DELAY);
  const debouncedBbox = useDebounced(bbox, VIEWPORT_DELAY);
  const debouncedZoom = useDebounced(zoom, VIEWPORT_DELAY);

  const mapFilters = useMemo<Filters>(
    () => ({ ...debouncedFilters, bbox: debouncedBbox }),
    [debouncedFilters, debouncedBbox],
  );
  const panelFilters = useMemo<Filters>(
    () => ({ ...debouncedFilters, bbox: searchInView ? debouncedBbox : null }),
    [debouncedFilters, debouncedBbox, searchInView],
  );

  const panelKey = filtersKey(panelFilters);
  const mapKey = filtersKey(mapFilters);

  /*
   * Asking for bargains and then sorting by date would bury the point of the
   * question: the interesting listing is the one furthest below its estimate,
   * not the one published most recently. So the ordering follows the filter.
   */
  const order: Order = filters.bargainsOnly ? "desviacion" : "reciente";

  const listPage = useResource(
    `list:${panelKey}:${pageIndex}:${order}`,
    useCallback(
      (signal: AbortSignal) =>
        fetchListings(
          panelFilters,
          { limit: PAGE_SIZE, offset: pageIndex * PAGE_SIZE },
          order,
          signal,
        ),
      [panelFilters, pageIndex, order],
    ),
  );

  const mapData = useResource(
    `map:${mapKey}:${debouncedZoom}:${layer}`,
    useCallback(
      (signal: AbortSignal) =>
        fetchMapData(mapFilters, debouncedZoom, layer === "calor", signal),
      [mapFilters, debouncedZoom, layer],
    ),
    // The list hides the map, and the viewport cannot change while it is
    // hidden, so the last markers stay valid for the next switch.
    { enabled: view === "map" && mapFilters.bbox !== null },
  );

  /*
   * Favourites are a different question from the filter panel, so they get their
   * own request rather than another filter: the point of the view is "the ones I
   * saved", and intersecting that with a price range the user set ten minutes
   * ago for a different search would hide them with no explanation.
   *
   * Keyed on the sorted ids so that starring something refetches, and unstarring
   * the same thing back does not.
   */
  const favouriteIds = useMemo(() => [...favourites.ids].sort(), [favourites.ids]);
  const favouritePage = useResource(
    `fav:${favouriteIds.join(",")}:${pageIndex}`,
    useCallback(
      (signal: AbortSignal) =>
        fetchListingsByIds(
          favouriteIds,
          { limit: PAGE_SIZE, offset: pageIndex * PAGE_SIZE },
          signal,
        ),
      [favouriteIds, pageIndex],
    ),
    { enabled: view === "favourites" && favouriteIds.length > 0 },
  );

  const stats = useResource(
    `stats:${panelKey}`,
    useCallback(
      (signal: AbortSignal) => fetchStats(panelFilters, HISTOGRAM_BINS, signal),
      [panelFilters],
    ),
  );

  // Options and slider bounds never depend on the selection: fetched once.
  const facets = useResource(
    "facets",
    useCallback((signal: AbortSignal) => fetchFacets(signal), []),
  );

  /*
   * La geografía del dataset: los barrios y los puntos de interés.
   *
   * Clave constante, así que se pide una vez y se queda. No depende de los
   * filtros y no puede depender: los contornos de Chamberí son los mismos
   * busques pisos de 200.000 o de un millón. Y se piden aunque la capa esté
   * apagada, porque encenderla no debería costar una espera de 279 kB — para
   * eso están la compresión y el `Cache-Control` del servidor.
   */
  const neighbourhoods = useResource(
    "neighbourhoods",
    useCallback((signal: AbortSignal) => fetchNeighbourhoods(signal), []),
  );
  const pointsOfInterest = useResource(
    "pois",
    useCallback((signal: AbortSignal) => fetchPointsOfInterest(signal), []),
  );

  /*
   * Los pasos de la pantalla de carga.
   *
   * Solo cuenta la primera vez: `data === null && loading` es "todavía no ha
   * llegado nunca". Después, mover el mapa vuelve a poner `loading` a true y
   * eso no debe hacer reaparecer la pantalla completa — para eso está el aviso
   * discreto de la esquina del mapa.
   */
  const initialSteps = useMemo<LoadingStep[]>(() => {
    const step = (label: string, resource: { data: unknown; error: string | null }): LoadingStep => ({
      label,
      state: resource.error !== null ? "error" : resource.data !== null ? "done" : "pending",
    });
    return [
      step("Anuncios", mapData),
      step("Opciones de búsqueda", facets),
      step("Barrios", neighbourhoods),
      step("Puntos de interés", pointsOfInterest),
    ];
  }, [mapData, facets, neighbourhoods, pointsOfInterest]);

  const toggleOverlay = useCallback((key: keyof Overlays) => {
    setOverlays((previous) => ({ ...previous, [key]: !previous[key] }));
  }, []);

  /**
   * Todo lo que hay que saber de un barrio, por id.
   *
   * Sale de las facetas, que se piden una vez y no dependen de la selección, y
   * es lo que convierte una lista de `LOCATIONID` —lo único que viaja en el
   * filtro— en nombres para el resumen y en cajas para volar el mapa.
   */
  const neighbourhoodsById = useMemo(() => {
    const map = new Map<string, NeighbourhoodFacet>();
    for (const zone of facets.data?.zones ?? []) {
      for (const entry of zone.neighbourhoods) map.set(entry.id, entry);
    }
    return map;
  }, [facets.data]);

  const neighbourhoodCounts = useMemo(() => {
    const map = new Map<string, number>();
    for (const [id, entry] of neighbourhoodsById) map.set(id, entry.count);
    return map;
  }, [neighbourhoodsById]);

  const selectedNeighbourhoods = useMemo(
    () =>
      filters.neighbourhoods
        .map((id) => neighbourhoodsById.get(id))
        .filter((entry): entry is NeighbourhoodFacet => entry !== undefined),
    [filters.neighbourhoods, neighbourhoodsById],
  );

  /**
   * Accepts a value or an updater, and that is not a convenience.
   *
   * Two chips clicked in quick succession both read the `filters` captured by
   * the render they were drawn in, so the second overwrites the first and one
   * of the two selections silently disappears. Passing a function lets React
   * apply each change to the latest state instead of to a stale snapshot.
   */
  const changeFilters = useCallback(
    (next: Filters | ((previous: Filters) => Filters)) => {
      setFilters(next);
      // Page 7 of the old result set means nothing in the new one.
      setPageIndex(0);
    },
    [],
  );

  /** Picking a city both filters and moves the map, the way a portal does. */
  const changeZone = useCallback(
    (zone: string | null) => {
      // Elegir la ciudad suelta los barrios. Son la misma pregunta con distinto
      // grano, y dejar las dos puestas obligaría a adivinar cuál manda.
      changeFilters((previous) => ({ ...previous, zone, neighbourhoods: [] }));
      setFlyTo(zone === null ? null : zoneBounds(facets.data?.zones.find((z) => z.value === zone)));
    },
    [changeFilters, facets.data],
  );

  /**
   * Marcar o desmarcar un barrio, venga del mapa o del selector lateral.
   *
   * Los dos caminos acaban aquí a propósito: hacer clic en el polígono de
   * Chamberí y marcarlo en la lista tienen que ser el mismo acto, y con un
   * manejador para cada uno se separarían a la primera.
   *
   * Encender la capa de barrios al marcar uno no es un adorno: se puede llegar
   * aquí desde la lista con la capa apagada, y entonces el mapa se filtraría
   * sin enseñar por qué.
   */
  const toggleNeighbourhood = useCallback(
    (id: string) => {
      changeFilters((previous) => {
        const on = previous.neighbourhoods.includes(id);
        const next = on
          ? previous.neighbourhoods.filter((item) => item !== id)
          : [...previous.neighbourhoods, id];
        return { ...previous, neighbourhoods: next, zone: null };
      });
      setOverlays((previous) =>
        previous.neighbourhoods ? previous : { ...previous, neighbourhoods: true },
      );
    },
    [changeFilters],
  );

  const clearNeighbourhoods = useCallback(() => {
    changeFilters((previous) => ({ ...previous, neighbourhoods: [] }));
  }, [changeFilters]);

  /*
   * Volar a lo elegido, y solo cuando cambia lo elegido.
   *
   * En un efecto y no dentro de `toggleNeighbourhood` porque las cajas vienen
   * de las facetas, que pueden llegar después del primer clic. La dependencia
   * es la lista de ids, no el objeto, para que mover el mapa a mano después no
   * lo devuelva de un salto al barrio.
   */
  const selectionKey = filters.neighbourhoods.join(",");
  useEffect(() => {
    if (selectionKey === "") return;
    const bounds = unionBounds(selectedNeighbourhoods);
    if (bounds) setFlyTo(bounds);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectionKey, neighbourhoodsById]);

  const handleViewport = useCallback(
    (nextCamera: Camera, nextBbox: BoundingBox, nextZoom: number) => {
      setCamera(nextCamera);
      setBbox(nextBbox);
      setZoom(nextZoom);
      setPageIndex(0);
    },
    [],
  );

  /**
   * Clicking an aggregated cell searches inside it, rather than only flying to it.
   *
   * Flying alone was the old behaviour and it is what the complaint was about:
   * the viewport is a rectangle of the screen's aspect ratio, never the cell's,
   * so the neighbours always leaked back in. Turning the cell into the drawn
   * search area makes the answer exact, reuses the filter that already exists,
   * and — because the panel shows it with a "Quitar" button — leaves the user a
   * visible way back out.
   */
  const handleCellSelect = useCallback(
    (cell: MapCluster) => {
      const bounds = cellBounds(cell);
      if (!bounds) return;

      /*
       * Una celda que **es** una zona se filtra por su nombre, no por su
       * rectángulo. Da el mismo resultado y cuesta otra cosa: `zona=Madrid` es
       * una igualdad sobre un índice, mientras que el rectángulo obliga a
       * evaluar un polígono fila a fila sobre las 75.000 que caen dentro. Medido
       * en el contenedor, la diferencia era entre responder al momento y tardar
       * media minuto la primera vez.
       *
       * Las celdas de rejilla no tienen nombre que usar, así que esas sí van
       * como área dibujada.
       */
      if (cell.zone) {
        changeFilters((previous) => ({ ...previous, zone: cell.zone, polygon: null }));
      } else {
        changeFilters((previous) => ({ ...previous, polygon: encodeRectangle(bounds) }));
      }
      setFlyTo(bounds);
    },
    [changeFilters],
  );

  const handleDrawn = useCallback(
    (polygon: string) => {
      setDrawing(false);
      changeFilters((previous) => ({ ...previous, polygon }));
    },
    [changeFilters],
  );

  const clearPolygon = useCallback(() => {
    setDrawing(false);
    changeFilters((previous) => ({ ...previous, polygon: null }));
  }, [changeFilters]);

  const showOnMap = useCallback((listing: Listing) => {
    setSelectedId(globalId(listing));
    setView("map");
    // The heat layer has no individual markers, so "see it on the map" would
    // fly to a coloured rectangle and show nothing in particular.
    setLayer("marcadores");
    if (listing.latitude !== null && listing.longitude !== null) {
      // A point has no extent; a tight box around it is what flyToBounds needs.
      const margin = 0.002;
      setFlyTo({
        lat_min: listing.latitude - margin,
        lat_max: listing.latitude + margin,
        lon_min: listing.longitude - margin,
        lon_max: listing.longitude + margin,
      });
    }
  }, []);

  return (
    <div className="layout">
      <aside className="sidebar">
        <FilterPanel
          filters={filters}
          onChange={changeFilters}
          onZoneChange={changeZone}
          facets={facets.data}
          total={listPage.data?.total ?? null}
          loading={listPage.loading || stats.loading}
          searchInView={searchInView}
          onSearchInViewChange={setSearchInView}
          showSearchInView={bbox !== null}
          onClearPolygon={clearPolygon}
          onToggleNeighbourhood={toggleNeighbourhood}
          onClearNeighbourhoods={clearNeighbourhoods}
        />

        <StatsPanel
          stats={stats.data}
          loading={stats.loading}
          error={stats.error}
          selectedZone={filters.zone}
          onZoneSelect={(zone) => changeZone(filters.zone === zone ? null : zone)}
          selectedNeighbourhoods={filters.neighbourhoods}
          onNeighbourhoodSelect={toggleNeighbourhood}
        />
      </aside>

      <main className="content">
        <div className="toolbar">
          <div className="segmented segmented--views" role="tablist" aria-label="Vista">
            <button
              type="button"
              role="tab"
              aria-selected={view === "map"}
              className={view === "map" ? "is-active" : ""}
              onClick={() => setView("map")}
            >
              Mapa
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "list"}
              className={view === "list" ? "is-active" : ""}
              onClick={() => setView("list")}
            >
              Lista
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "favourites"}
              className={view === "favourites" ? "is-active" : ""}
              onClick={() => {
                setView("favourites");
                setPageIndex(0);
              }}
            >
              ♥ Favoritos
              {favourites.count > 0 ? <span className="tab__count">{favourites.count}</span> : null}
            </button>
          </div>

          {view === "map" ? (
            <div className="toolbar__draw">
              <button
                type="button"
                className={`button${drawing ? " button--on" : ""}`}
                aria-pressed={drawing}
                onClick={() => setDrawing((on) => !on)}
              >
                {drawing ? "Dibujando… (arrastra sobre el mapa)" : "✏ Dibujar zona"}
              </button>
              {filters.polygon ? (
                <button type="button" className="button button--ghost" onClick={clearPolygon}>
                  Borrar zona
                </button>
              ) : null}
            </div>
          ) : null}
        </div>

        {view === "map" ? (
          <MapView
            data={mapData.data}
            loading={mapData.loading}
            error={mapData.error}
            camera={camera}
            onViewportChange={handleViewport}
            flyTo={flyTo}
            selectedId={selectedId}
            onSelect={setSelectedId}
            drawing={drawing}
            onDrawn={handleDrawn}
            polygon={filters.polygon}
            layer={layer}
            onLayerChange={setLayer}
            onCellSelect={handleCellSelect}
            neighbourhoodGeo={neighbourhoods.data}
            pois={pointsOfInterest.data}
            overlays={overlays}
            onOverlayToggle={toggleOverlay}
            zone={filters.zone}
            selectedNeighbourhoods={filters.neighbourhoods}
            neighbourhoodCounts={neighbourhoodCounts}
            onToggleNeighbourhood={toggleNeighbourhood}
          />
        ) : view === "list" ? (
          <ListView
            page={listPage.data}
            loading={listPage.loading}
            error={listPage.error}
            pageSize={PAGE_SIZE}
            pageIndex={pageIndex}
            onPageChange={setPageIndex}
            selectedId={selectedId}
            onSelect={showOnMap}
            isFavourite={favourites.has}
            onToggleFavourite={favourites.toggle}
          />
        ) : (
          <ListView
            // An empty favourites list is not a page of zero results: the API is
            // never asked, so a synthetic empty page is what says so honestly.
            page={
              favouriteIds.length === 0
                ? { items: [], total: 0, limit: PAGE_SIZE, offset: 0 }
                : favouritePage.data
            }
            loading={favouritePage.loading}
            error={favouritePage.error}
            pageSize={PAGE_SIZE}
            pageIndex={pageIndex}
            onPageChange={setPageIndex}
            selectedId={selectedId}
            onSelect={showOnMap}
            isFavourite={favourites.has}
            onToggleFavourite={favourites.toggle}
            emptyMessage="Todavía no has guardado ningún anuncio. Pulsa el ♡ de una tarjeta para tenerlo aquí; se guarda en este navegador, no en una cuenta."
          />
        )}
      </main>

      <DetailCard
        globalId={selectedId}
        onClose={() => setSelectedId(null)}
        favourite={selectedId !== null && favourites.has(selectedId)}
        onToggleFavourite={favourites.toggle}
      />

      <LoadingScreen steps={initialSteps} />
    </div>
  );
}
