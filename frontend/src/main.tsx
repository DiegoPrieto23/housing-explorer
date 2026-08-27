import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";

/*
 * IBM Plex, autoalojado.
 *
 * Sólo el subconjunto latino y sólo los tres pesos que se usan de cada
 * familia: seis ficheros woff2 en lugar de los ciento y pico que trae el
 * paquete entero. Autoalojado y no enlazado a Google Fonts porque esta web se
 * publica en GitHub Pages y se abre también sin red, y una tipografía que
 * depende de un tercero es una tipografía que a veces no está.
 *
 * `MarkerCluster.Default.css` no se importa a propósito: pintaba los grupos
 * con los globos verde / amarillo / rojo del plugin. Los grupos los dibuja
 * ahora `clusterBubbleIcon` en `markers.ts`, con la paleta de la aplicación.
 */
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "@fontsource/ibm-plex-mono/latin-600.css";

import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
