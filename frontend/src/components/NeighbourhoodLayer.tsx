import L from "leaflet";
import { useEffect, useMemo, useRef } from "react";
import { useMap } from "react-leaflet";

import { count } from "../format";
import type { NeighbourhoodCollection, NeighbourhoodProperties } from "../types/listing";

interface NeighbourhoodLayerProps {
  data: NeighbourhoodCollection | null;
  /** Ciudad filtrada, para apagar los barrios de las demás. */
  city: string | null;
  /** Barrios seleccionados, por `LOCATIONID`. */
  selected: string[];
  /** Cuántos anuncios tiene cada barrio, para la etiqueta. */
  counts: Map<string, number>;
  /** Un clic sobre el polígono añade o quita ese barrio de la búsqueda. */
  onToggle: (id: string) => void;
}

/**
 * Trazo fino y relleno casi transparente: es un mapa de referencia, no el dato.
 *
 * Se empezó con `weight: 1` y `opacity: 0.55`, y sobre las teselas de
 * OpenStreetMap —que ya vienen llenas de líneas rosas y grises— los contornos
 * literalmente no se veían en una captura a zoom de ciudad. Subirlo a 1,4 y
 * 0,85 los hace legibles sin que compitan con los marcadores.
 */
const BASE_STYLE: L.PathOptions = {
  color: "#1e293b",
  weight: 1.4,
  opacity: 0.85,
  // El relleno no es decoración. Un `L.Polygon` sin relleno solo recibe eventos
  // sobre la propia línea, o sea sobre un píxel de ancho, y entonces "pasar el
  // ratón por encima del barrio" no funciona en ningún sitio salvo el borde.
  // Con 0.06 el barrio entero es la zona sensible y sigue sin taparse el mapa.
  fill: true,
  fillColor: "#1e293b",
  fillOpacity: 0.06,
};

const HOVER_STYLE: L.PathOptions = {
  color: "#1a56db",
  weight: 2.5,
  opacity: 1,
  fillColor: "#1a56db",
  fillOpacity: 0.18,
};

/** El barrio elegido: relleno claro y borde grueso, visible sin pasar el ratón. */
const SELECTED_STYLE: L.PathOptions = {
  color: "#1a56db",
  weight: 3,
  opacity: 1,
  fillColor: "#1a56db",
  fillOpacity: 0.22,
};

/** Barrio de otra ciudad cuando hay una elegida: presente, pero apagado. */
const DIMMED_STYLE: L.PathOptions = { ...BASE_STYLE, opacity: 0.15, fillOpacity: 0.02 };

/**
 * Los 277 barrios del dataset, dibujados encima del mapa y **clicables**.
 *
 * Es la geografía que el dataset ya traía y que no se estaba usando: los
 * objetos `<Ciudad>_Polygons`, con su `LOCATIONID` y su `LOCATIONNAME`. La
 * ingesta los exporta a GeoJSON, el backend los sirve tal cual, y
 * `scripts/assign_neighbourhoods` ya ha escrito en cada anuncio en cuál cae, de
 * modo que un clic aquí se traduce en un `IN` sobre un índice y no en 277
 * pruebas de punto en polígono por consulta.
 *
 * Imperativo y no como componente `<GeoJSON>` de react-leaflet por la misma
 * razón que `MarkerLayer`: son 12.101 vértices, y react-leaflet no actualiza un
 * `<GeoJSON>` cuando cambian sus props — lo recrea entero. Montarlo una vez y
 * tocar solo el estilo evita reconstruir las 277 geometrías cada vez que se
 * marca un barrio.
 */
export default function NeighbourhoodLayer({
  data,
  city,
  selected,
  counts,
  onToggle,
}: NeighbourhoodLayerProps) {
  const map = useMap();
  const layerRef = useRef<L.GeoJSON<NeighbourhoodProperties> | null>(null);

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  // Leídos por referencia dentro de los manejadores, que se registran una sola
  // vez al montar la capa: si dependieran del valor capturado, marcar un barrio
  // dejaría los otros 276 con la selección de hace un render.
  const toggleRef = useRef(onToggle);
  toggleRef.current = onToggle;
  const selectedRef = useRef(selectedSet);
  selectedRef.current = selectedSet;
  const cityRef = useRef(city);
  cityRef.current = city;
  const countsRef = useRef(counts);
  countsRef.current = counts;

  /** El estilo que le toca a un barrio ahora mismo, sin pasar el ratón. */
  const restingStyle = (id: string, featureCity: string): L.PathOptions => {
    if (selectedRef.current.has(id)) return SELECTED_STYLE;
    if (cityRef.current !== null && featureCity !== cityRef.current) return DIMMED_STYLE;
    return BASE_STYLE;
  };

  useEffect(() => {
    if (!data) return;

    const layer = L.geoJSON<NeighbourhoodProperties>(data, {
      style: () => BASE_STYLE,
      onEachFeature: (feature, target) => {
        const { name, city: featureCity, location_id: id } = feature.properties;

        // `sticky` hace que la etiqueta siga al ratón en vez de anclarse al
        // centroide, que en un barrio alargado cae lejos de donde se apunta.
        target.bindTooltip(name, {
          sticky: true,
          direction: "top",
          className: "tip tip--zone",
        });

        target.on({
          mouseover: (event) => {
            const path = event.target as L.Path;
            path.setStyle(HOVER_STYLE);
            path.bringToFront();
            // El contenido se compone aquí y no al enlazar la etiqueta porque
            // los recuentos llegan con las facetas, **después** de que la capa
            // se monte. Puestos en el `bindTooltip`, la primera versión de esto
            // decía la ciudad para siempre: el mapa nunca llegó a enseñar un
            // número, porque en el momento de construirlo no había ninguno.
            const listings = countsRef.current.get(id);
            const chosen = selectedRef.current.has(id);
            path.setTooltipContent(
              `${name}<span class="tip__city">` +
                (listings === undefined ? featureCity : `${count(listings)} anuncios`) +
                ` · clic para ${chosen ? "quitarlo del" : "filtrar por este"} barrio</span>`,
            );
          },
          mouseout: (event) => {
            (event.target as L.Path).setStyle(restingStyle(id, featureCity));
          },
          // Un clic añade o quita este barrio de la búsqueda. Antes solo abría
          // la etiqueta, que es lo que un mapa hace cuando no puede hacer nada
          // mejor; ahora que cada anuncio sabe en qué barrio está, puede.
          click: () => toggleRef.current(id),
        });
      },
    });

    layer.addTo(map);
    layerRef.current = layer;

    return () => {
      layer.remove();
      layerRef.current = null;
    };
    // Solo `data` reconstruye la capa. `counts`, `selected`, `city` y `onToggle`
    // se leen por referencia dentro de los manejadores, así que cambian sin
    // volver a montar 277 polígonos por una etiqueta o un color.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, data]);

  // Repintado de estilos, que es lo único que cambia al elegir un barrio o una
  // ciudad. `setStyle` sobre un `Path` ya montado no reconstruye la geometría.
  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;

    layer.eachLayer((child) => {
      const properties = (
        child as L.Polygon & { feature?: GeoJSON.Feature<never, NeighbourhoodProperties> }
      ).feature?.properties;
      if (!properties) return;

      const style = restingStyle(properties.location_id, properties.city);
      (child as L.Path).setStyle(style);
      // El elegido, delante: si no, el vecino dibujado después le tapa el borde
      // justo por el lado que comparten.
      if (style === SELECTED_STYLE) (child as L.Path).bringToFront();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [city, selectedSet, data]);

  return null;
}
