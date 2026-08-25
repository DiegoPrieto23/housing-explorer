import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

/**
 * `process`, declarado aquí y no traído de `@types/node`.
 *
 * Este fichero corre en Node, pero es el único del proyecto que lo hace: todo
 * `src/` es código de navegador. Instalar `@types/node` metería los globales de
 * Node en el mismo programa de TypeScript que el frontend, y entonces un
 * `setTimeout` que devuelve un `Timeout` en vez de un número dejaría de ser un
 * error de tipos donde debería serlo.
 *
 * Un `declare const` dentro de un módulo es local al módulo, así que esto no
 * sale de este fichero. Y hace falta declararlo: sin `@types/node` no existe, y
 * que compilara antes era casualidad — había un `@types/node` en un directorio
 * padre de esta máquina, fuera del repositorio, que TypeScript encontraba al
 * subir buscando `@types`. En CI no estaba y el despliegue no salió.
 */
declare const process: { env: Record<string, string | undefined> };

/**
 * Dos compilaciones del mismo código.
 *
 * `vite build` deja la web que habla con FastAPI: es la que sirve `docker
 * compose` y la que se usa en desarrollo.
 *
 * `vite build --mode static` deja la que no habla con nadie — resuelve los
 * filtros en el navegador contra el paquete de `scripts/build_static_data.py`—
 * y es la que se publica en GitHub Pages. Ver `src/api/index.ts`.
 *
 * El modo se traduce aquí a una constante que Vite sustituye en el código, en
 * vez de a un `.env` por modo, porque así las dos variantes están escritas la
 * una al lado de la otra y se ve de un vistazo en qué se diferencian.
 */
export default defineConfig(({ mode }) => {
  const isStatic = mode === "static";

  return {
    plugins: [react()],
    /**
     * Bajo qué ruta se sirve la web.
     *
     * En desarrollo y en Docker es la raíz. En GitHub Pages un proyecto cuelga
     * de `/<repo>/`, y sin esto los `<script src="/assets/...">` que genera el
     * build apuntarían al dominio y darían 404. `VITE_BASE_PATH` lo cambia sin
     * tocar el fichero, que es lo que necesita quien publique el repositorio
     * con otro nombre o bajo un dominio propio.
     */
    base: process.env.VITE_BASE_PATH ?? (isStatic ? "/housing-explorer/" : "/"),
    define: {
      "import.meta.env.VITE_DATA_MODE": JSON.stringify(isStatic ? "static" : "api"),
    },
    server: {
      host: true,
      port: 5173,
      // Calls to /api are forwarded to FastAPI, so the browser sees one origin.
      proxy: {
        "/api": {
          target: process.env.VITE_API_TARGET ?? "http://localhost:8000",
          changeOrigin: true,
        },
      },
    },
  };
});
