import L from "leaflet";
import { useEffect } from "react";
import { useMap } from "react-leaflet";

import type { PoiCollection } from "../types/listing";

interface PoiLayerProps {
  data: PoiCollection | null;
}

/** Rojo de señalización, el color con el que se pinta el metro en toda España. */
const METRO_COLOR = "#d1332e";
/** Ámbar para el centro: destaca sobre teja, sobre satélite y sobre gris. */
const CENTRE_COLOR = "#b45309";
/** La calle principal, en un azul que no se confunde con ninguno de los pines. */
const STREET_COLOR = "#0369a1";

/**
 * El centro de la ciudad, como diana. Solo hay tres en todo el mapa, así que
 * puede permitirse ser un icono con detalle.
 */
const CENTRE_ICON = L.divIcon({
  html: [
    `<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 26 26">`,
    `<circle cx="13" cy="13" r="11" fill="#fff" stroke="${CENTRE_COLOR}" stroke-width="2"/>`,
    `<circle cx="13" cy="13" r="6.5" fill="none" stroke="${CENTRE_COLOR}" stroke-width="1.6"/>`,
    `<circle cx="13" cy="13" r="2.5" fill="${CENTRE_COLOR}"/>`,
    `</svg>`,
  ].join(""),
  className: "poi poi--centre",
  iconSize: [26, 26],
  iconAnchor: [13, 13],
});

/**
 * Los puntos de interés que el dataset trae y que hasta ahora no se usaban.
 *
 * Tres tipos y tres formas distintas, que es lo que pedía el problema:
 *
 * - **centro**: una diana. Es el punto desde el que el dataset mide
 *   `DISTANCE_TO_CITY_CENTER`, o sea el origen de la curva de €/m² por
 *   distancia que ya pinta el panel de estadísticas.
 * - **metro**: un círculo rojo con anillo blanco, dibujado en el *canvas*.
 * - **calle**: una línea, no 155 puntos sueltos. La Castellana, la Diagonal y
 *   Blasco Ibáñez son ejes de referencia, y como nube de motas no se leen.
 *
 * Por qué el metro no lleva icono propio como el centro: son 801 estaciones.
 * Un `divIcon` por estación son 801 nodos del DOM permanentes, y a zoom de
 * país se amontonan hasta ser ilegibles de todos modos. Un `circleMarker` con
 * el mapa en `preferCanvas` no crea ni un nodo: se pinta en el lienzo que ya
 * existe. Sigue distinguiéndose del centro por color y forma, que es lo que
 * tiene que hacer, y el mapa no se vuelve pesado por una capa de contexto.
 *
 * Tampoco llevan nombre porque el dataset no lo trae: `<Ciudad>_POIS$Metro` son
 * dos columnas, `Lon` y `Lat`. Inventar "Estación de Sol" a partir de unas
 * coordenadas sería adivinar, así que la etiqueta dice lo que se sabe.
 */
export default function PoiLayer({ data }: PoiLayerProps) {
  const map = useMap();

  useEffect(() => {
    if (!data) return;

    const group = L.layerGroup();

    for (const feature of data.features) {
      const { kind, city, name } = feature.properties;
      const geometry = feature.geometry;

      if (kind === "calle" && geometry.type === "LineString") {
        // [lon, lat] en GeoJSON, [lat, lon] en Leaflet. Es el error clásico y
        // se manifiesta dibujando la calle en el océano Índico.
        const path = geometry.coordinates.map(([lon, lat]) => [lat, lon] as L.LatLngTuple);

        // Dos trazos superpuestos: uno ancho y claro debajo, otro fino encima.
        // Sobre la capa de satélite una línea sola se pierde contra el asfalto.
        group.addLayer(
          L.polyline(path, { color: "#ffffff", weight: 7, opacity: 0.65, interactive: false }),
        );
        const line = L.polyline(path, { color: STREET_COLOR, weight: 3.5, opacity: 0.95 });
        line.bindTooltip(`${name}<span class="tip__city">${city}</span>`, {
          sticky: true,
          direction: "top",
          className: "tip tip--street",
        });
        group.addLayer(line);
        continue;
      }

      if (geometry.type !== "Point") continue;
      const [lon, lat] = geometry.coordinates;

      if (kind === "centro") {
        const marker = L.marker([lat, lon], { icon: CENTRE_ICON, alt: name ?? "Centro" });
        marker.bindTooltip(`${name}<span class="tip__city">centro de la ciudad</span>`, {
          direction: "top",
          offset: [0, -12],
          className: "tip tip--centre",
        });
        group.addLayer(marker);
        continue;
      }

      const station = L.circleMarker([lat, lon], {
        radius: 4.5,
        color: "#ffffff",
        weight: 1.5,
        fillColor: METRO_COLOR,
        fillOpacity: 1,
      });
      station.bindTooltip(`Estación de metro<span class="tip__city">${city}</span>`, {
        direction: "top",
        offset: [0, -6],
        className: "tip tip--metro",
      });
      group.addLayer(station);
    }

    group.addTo(map);
    return () => {
      group.remove();
    };
  }, [map, data]);

  return null;
}
