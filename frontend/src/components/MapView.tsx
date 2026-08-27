import L from "leaflet";
import { useEffect, useRef } from "react";
import { LayersControl, MapContainer, TileLayer, useMap, useMapEvents } from "react-leaflet";

import type { BoundingBox } from "../filters";
import { count, shortEuros } from "../format";
import { MARKER_STYLES, PROPERTY_TYPES } from "../markers";
import type {
  MapCluster,
  MapData,
  NeighbourhoodCollection,
  PoiCollection,
  PropertyType,
} from "../types/listing";
import DrawControl from "./DrawControl";
import HeatLayer, { buildScale } from "./HeatLayer";
import MarkerLayer from "./MarkerLayer";
import NeighbourhoodLayer from "./NeighbourhoodLayer";
import PoiLayer from "./PoiLayer";

/** Where the map is pointing. Lifted so it survives switching to the list. */
export interface Camera {
  center: [number, number];
  zoom: number;
}

/** Roughly peninsular Spain: fits Madrid, Barcelona and Valencia at once. */
export const INITIAL_CAMERA: Camera = { center: [40.1, -1.5], zoom: 6 };

/**
 * Base layers, all free and key-less.
 *
 * Attribution is not decoration: every one of these requires it by licence.
 */
const BASE_LAYERS = [
  {
    name: "Mapa",
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  },
  {
    name: "Satélite",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "Imágenes &copy; Esri, Maxar, Earthstar Geographics",
    maxZoom: 19,
  },
  {
    name: "Terreno",
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    attribution:
      '&copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA), datos de OpenStreetMap',
    maxZoom: 17,
  },
  {
    name: "Claro",
    url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    attribution: '&copy; OpenStreetMap contributors, &copy; <a href="https://carto.com/">CARTO</a>',
    maxZoom: 20,
  },
] as const;

interface MapViewProps {
  data: MapData | null;
  loading: boolean;
  error: string | null;
  camera: Camera;
  onViewportChange: (camera: Camera, bbox: BoundingBox, zoom: number) => void;
  /** Area to fly to, e.g. after picking a city in the sidebar. */
  flyTo: BoundingBox | null;
  selectedId: string | null;
  onSelect: (globalId: string) => void;
  drawing: boolean;
  onDrawn: (polygon: string) => void;
  polygon: string | null;
  /** `marcadores` draws one pin per listing; `calor` colours cells by €/m². */
  layer: MapLayer;
  onLayerChange: (layer: MapLayer) => void;
  /** Clicking an aggregated cell narrows the search to exactly that cell. */
  onCellSelect: (cell: MapCluster) => void;
  /** Geografía fija del dataset. Null mientras carga o si no se pudo traer. */
  neighbourhoodGeo: NeighbourhoodCollection | null;
  pois: PoiCollection | null;
  overlays: Overlays;
  onOverlayToggle: (key: keyof Overlays) => void;
  /** Ciudad filtrada: los barrios de las demás se apagan en vez de irse. */
  zone: string | null;

  /** Barrios seleccionados, por `LOCATIONID`. */
  selectedNeighbourhoods: string[];
  /** Anuncios por barrio, para la etiqueta de cada polígono. */
  neighbourhoodCounts: Map<string, number>;
  onToggleNeighbourhood: (id: string) => void;
}

export type MapLayer = "marcadores" | "calor";

/**
 * Las capas de contexto, independientes entre sí y del conmutador
 * marcadores/calor.
 *
 * Van separadas del `MapLayer` a propósito: aquello es una elección entre dos
 * formas de pintar **los mismos anuncios**, y esto son dos cosas distintas que
 * se dibujan encima. Meterlas en el mismo control obligaría a elegir entre ver
 * los barrios y ver los anuncios, que es justo lo contrario de para qué sirven.
 */
export interface Overlays {
  neighbourhoods: boolean;
  pois: boolean;
}

/**
 * Barrios sí, puntos de interés no, al abrir.
 *
 * Los contornos de barrio dan escala y salen gratis a cualquier zoom. Las 801
 * bocas de metro a vista de país son una mancha roja sobre tres ciudades, así
 * que esa se pide.
 */
export const DEFAULT_OVERLAYS: Overlays = { neighbourhoods: true, pois: false };

function toBoundingBox(bounds: L.LatLngBounds): BoundingBox {
  return {
    lat_min: bounds.getSouth(),
    lat_max: bounds.getNorth(),
    lon_min: bounds.getWest(),
    lon_max: bounds.getEast(),
  };
}

function readViewport(map: L.Map): [Camera, BoundingBox, number] {
  const center = map.getCenter();
  return [
    { center: [center.lat, center.lng], zoom: map.getZoom() },
    toBoundingBox(map.getBounds()),
    map.getZoom(),
  ];
}

/** Reports the viewport once the map settles, and once on mount. */
function ViewportReporter({ onChange }: { onChange: MapViewProps["onViewportChange"] }) {
  const map = useMapEvents({
    moveend: () => onChange(...readViewport(map)),
    zoomend: () => onChange(...readViewport(map)),
  });

  const changeRef = useRef(onChange);
  changeRef.current = onChange;

  useEffect(() => {
    changeRef.current(...readViewport(map));
  }, [map]);

  return null;
}

/** Flies the map to an area chosen elsewhere (a city, a listing on a card). */
function Flyer({ bounds }: { bounds: BoundingBox | null }) {
  const map = useMap();

  useEffect(() => {
    if (!bounds) return;
    map.flyToBounds(
      [
        [bounds.lat_min, bounds.lon_min],
        [bounds.lat_max, bounds.lon_max],
      ],
      { padding: [40, 40], duration: 0.8 },
    );
  }, [bounds, map]);

  return null;
}

/** Lets a cell click zoom the map in on that cell. */
function Zoomer({ register }: { register: (fn: (lat: number, lon: number) => void) => void }) {
  const map = useMap();

  useEffect(() => {
    register((lat, lon) => {
      // Three levels in is enough to break a cell apart without overshooting
      // past the point where the next request returns individual markers.
      map.flyTo([lat, lon], Math.min(map.getZoom() + 3, 18), { duration: 0.7 });
    });
  }, [map, register]);

  return null;
}

/** The colour ramp, with the €/m² each band starts at. */
function HeatLegend({ cells }: { cells: MapCluster[] }) {
  const scale = buildScale(cells);
  if (!scale) return null;

  /*
   * Los números fuera de la rampa, uno a cada lado.
   *
   * Antes iban DENTRO de la primera y la última banda, con un halo blanco
   * detrás para que se leyeran sobre cualquier tono. Con las cifras en
   * monoespaciada «1,4 mil» ya no cabe en una banda de 2,6 rem y se cortaba
   * por la izquierda. Sacarlos no es sólo que quepan: una rampa de color se
   * lee mejor entera, sin texto encima, y los extremos etiquetados a los lados
   * son la forma canónica de acotarla. De paso se va el «hasta X» que había
   * suelto al final, que decía lo mismo que el extremo derecho.
   */
  const bands = scale.colours.map((colour, index) => ({
    colour,
    from: index === 0 ? scale.min : scale.breaks[index - 1],
  }));

  return (
    <div className="legend legend--heat">
      <span className="legend__title">€/m²</span>
      <span className="legend__band">{shortEuros(scale.min)}</span>
      <span className="legend__ramp">
        {bands.map((band) => (
          <span
            key={band.colour}
            className="legend__step"
            style={{ background: band.colour }}
            title={`desde ${Math.round(band.from).toLocaleString("es-ES")} €/m²`}
          />
        ))}
      </span>
      <span className="legend__band">{shortEuros(scale.max)}</span>
    </div>
  );
}

/** Qué significa cada forma de la capa de puntos de interés. */
function PoiLegend() {
  return (
    <div className="legend legend--poi">
      <span className="legend__item">
        <span className="legend__swatch legend__swatch--centre" />
        Centro
      </span>
      <span className="legend__item">
        <span className="legend__swatch legend__swatch--metro" />
        Metro
      </span>
      <span className="legend__item">
        <span className="legend__swatch legend__swatch--street" />
        Calle principal
      </span>
    </div>
  );
}

function Legend({ types }: { types: PropertyType[] }) {
  if (types.length === 0) return null;

  return (
    <div className="legend">
      {types.map((type) => (
        <span key={type} className="legend__item">
          <span className="legend__swatch" style={{ background: MARKER_STYLES[type].color }} />
          {MARKER_STYLES[type].label}
        </span>
      ))}
      <span className="legend__item legend__item--note">
        <span className="legend__swatch legend__swatch--hollow" />
        Alquiler
      </span>
    </div>
  );
}

export default function MapView({
  data,
  loading,
  error,
  camera,
  onViewportChange,
  flyTo,
  selectedId,
  onSelect,
  drawing,
  onDrawn,
  polygon,
  layer,
  onLayerChange,
  onCellSelect,
  neighbourhoodGeo,
  pois,
  overlays,
  onOverlayToggle,
  zone,
  selectedNeighbourhoods,
  neighbourhoodCounts,
  onToggleNeighbourhood,
}: MapViewProps) {
  const zoomToRef = useRef<(lat: number, lon: number) => void>(() => {});

  const points = data?.points ?? [];
  const clusters = data?.clusters ?? [];
  const total = data?.total ?? 0;
  const aggregated = data?.mode === "clusters";
  const heat = layer === "calor";

  // Only the types actually on screen, so the legend describes this map and not
  // the ten types the enum happens to define.
  const visibleTypes = PROPERTY_TYPES.filter((type) =>
    points.some((point) => point.property_type === type),
  );

  return (
    <div className="map-pane">
      {/*
        preferCanvas afecta solo a las capas vectoriales, que aquí son el
        polígono dibujado y su trazo en vivo; los marcadores son divIcon, o sea
        DOM, y no cambian por esto. Se declara igualmente porque el trazo se
        redibuja en cada movimiento del ratón mientras se dibuja, y porque deja
        el mapa preparado si algún día se añade una capa de calor.
      */}
      <MapContainer
        center={camera.center}
        zoom={camera.zoom}
        className="map"
        zoomAnimation={false}
        preferCanvas
      >
        <LayersControl position="topright">
          {BASE_LAYERS.map((layer, index) => (
            <LayersControl.BaseLayer key={layer.name} name={layer.name} checked={index === 0}>
              <TileLayer
                url={layer.url}
                attribution={layer.attribution}
                maxZoom={layer.maxZoom}
                // Past a layer's own maximum, Leaflet upscales the last tiles it
                // has instead of showing grey. Satellite runs deeper than
                // terrain, so without this the deep zooms would go blank.
                maxNativeZoom={layer.maxZoom}
              />
            </LayersControl.BaseLayer>
          ))}
        </LayersControl>

        <ViewportReporter onChange={onViewportChange} />
        <Flyer bounds={flyTo} />
        <Zoomer register={(fn) => (zoomToRef.current = fn)} />
        <DrawControl active={drawing} onDrawn={onDrawn} polygon={polygon} />
        {/*
          Debajo de los marcadores en el código y, por tanto, en el orden de
          pintado: son contexto. Un contorno de barrio no debe taparle el clic
          a un anuncio.
        */}
        {overlays.neighbourhoods ? (
          <NeighbourhoodLayer
            data={neighbourhoodGeo}
            city={zone}
            selected={selectedNeighbourhoods}
            counts={neighbourhoodCounts}
            onToggle={onToggleNeighbourhood}
          />
        ) : null}
        {overlays.pois ? <PoiLayer data={pois} /> : null}
        {heat ? (
          <HeatLayer cells={clusters} onSelect={onCellSelect} />
        ) : (
          <MarkerLayer
            points={points}
            clusters={clusters}
            onSelect={onSelect}
            selectedId={selectedId}
            onCellSelect={onCellSelect}
          />
        )}
      </MapContainer>

      {/*
        Los cuatro conmutadores en una columna, y arrancando por debajo del
        control de zoom de Leaflet.

        Antes «Marcadores / Calor» se colocaba en la misma esquina y a la misma
        altura que los botones + y − del mapa, y con más z-index: el zoom
        estaba ahí pero quedaba tapado y no se podía pulsar. Ahora la columna
        empieza donde acaba el zoom, y de paso los cuatro botones —que son la
        misma clase de cosa, «qué se ve encima del mapa»— viven juntos en vez
        de en dos bloques separados.
      */}
      <div className="map-controls">
        <div className="map-layers segmented" role="tablist" aria-label="Capa del mapa">
          {(["marcadores", "calor"] as MapLayer[]).map((option) => (
            <button
              key={option}
              type="button"
              role="tab"
              aria-selected={layer === option}
              className={layer === option ? "is-active" : ""}
              onClick={() => onLayerChange(option)}
            >
              {option === "marcadores" ? "Marcadores" : "Calor"}
            </button>
          ))}
        </div>

        {/*
          Botones que conmutan, no pestañas: los dos pueden estar encendidos a
          la vez, y `aria-pressed` es lo que dice eso. Un `role="tab"`
          prometería que elegir uno apaga el otro.
        */}
        <div className="map-overlays" role="group" aria-label="Capas de contexto">
          <button
            type="button"
            aria-pressed={overlays.neighbourhoods}
            className={overlays.neighbourhoods ? "is-active" : ""}
            onClick={() => onOverlayToggle("neighbourhoods")}
            title="Contornos de los 277 barrios que delimita el dataset"
          >
            <span className="map-overlays__swatch map-overlays__swatch--zone" />
            Barrios
          </button>
          <button
            type="button"
            aria-pressed={overlays.pois}
            className={overlays.pois ? "is-active" : ""}
            onClick={() => onOverlayToggle("pois")}
            title="Centro de la ciudad, bocas de metro y calle principal"
          >
            <span className="map-overlays__swatch map-overlays__swatch--poi" />
            Puntos de interés
          </button>
        </div>
      </div>

      {/*
        Las leyendas, todas en la esquina inferior izquierda y apiladas.

        La de puntos de interés vivía en la inferior derecha, que es justo
        donde se abre la ficha del anuncio: seleccionabas un piso y la ficha
        caía encima. Y «qué significa cada símbolo» es una sola pregunta, así
        que tener media respuesta en cada esquina obligaba a mirar a dos sitios.
      */}
      <div className="map-legends">
        {overlays.pois ? <PoiLegend /> : null}
        {heat ? <HeatLegend cells={clusters} /> : <Legend types={visibleTypes} />}
      </div>

      <div className="map-status">
        {error ? (
          <span className="notice notice--error">{error}</span>
        ) : loading ? (
          <span>Cargando…</span>
        ) : heat ? (
          <span>
            <strong>{count(total)} anuncios</strong> en {count(clusters.length)} zonas
            <span className="muted"> · el color es el precio medio por m²</span>
          </span>
        ) : aggregated ? (
          <span>
            <strong>{count(total)} anuncios</strong> agrupados en {count(clusters.length)} zonas
            <span className="muted"> · haz clic en un grupo o acerca el mapa para verlos</span>
          </span>
        ) : (
          <span>
            <strong>{count(total)}</strong> {total === 1 ? "anuncio" : "anuncios"} en el mapa
          </span>
        )}
      </div>
    </div>
  );
}
