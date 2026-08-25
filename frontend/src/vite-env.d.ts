/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /**
   * `static` compila la web contra el paquete de datos en vez de contra la API.
   * Ver `src/api/index.ts`. Cualquier otro valor —o ninguno— deja el modo HTTP.
   */
  readonly VITE_DATA_MODE?: string;
  /** La ruta bajo la que se sirve la web; `/` salvo en GitHub Pages. */
  readonly BASE_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
  readonly url: string;
}
