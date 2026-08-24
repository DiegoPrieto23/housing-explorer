# Estado del proyecto

Housing Explorer — visor de anuncios inmobiliarios sobre el dataset abierto
`idealista18`, preparado para enchufar la API oficial de Idealista sin tocar
el resto del sistema.

> Este archivo es la fuente de verdad del progreso. Se actualiza al terminar
> cada tarea o sesión de trabajo, para que el estado no dependa de recordar
> conversaciones anteriores.

**Última auditoría:** 2026-08-24 · hecha leyendo el código, no las notas.

| Comprobación | Resultado |
| --- | --- |
| Tests del backend (`pytest`) | 152 pasan, 1 se salta (necesita el dataset grande) |
| Tests del frontend | **Ninguno**: no hay runner configurado |
| Typecheck del frontend (`tsc --noEmit`) | Sin errores |
| Build de producción (`vite build`) | 373 kB, 114 kB comprimido |
| Notebook (`nbconvert --execute`) | 58 celdas, 0 errores, 12 gráficos |
| `data/housing.db` | 137 MB · esquema v4 · 32 columnas |
| Anuncios | 149.923 (Madrid 75.804 · Barcelona 46.728 · Valencia 27.391) |
| Con precio estimado | 149.923 de 149.923 (100 %) · 6.227 chollos (4,2 %) |
| Docker | **Verificado de punta a punta.** Imágenes construidas (315 y 369 MB), volumen `housing-explorer_housing-data` con la base |
| Git | Repo inicializado, **sin ningún commit todavía**: 91 ficheros en el índice |

Ahora mismo **no hay nada corriendo**: ni contenedores ni servidores de
desarrollo. `docker compose up -d` lo levanta en segundos (las imágenes están
construidas y el volumen conserva la base), o `.\start.ps1` en el host.

---

## Qué hace la web hoy

El resto del fichero es la crónica de cómo se llegó aquí, sección por sección y
de lo más reciente a lo más antiguo. Esto es el resumen de una pantalla.

**Dos vistas y una lista de favoritos**, sobre el mismo conjunto filtrado:

- **Mapa** con cuatro capas base (OSM, satélite de Esri, OpenTopoMap, CARTO) y
  un conmutador **Marcadores / Calor**. Sin tope de anuncios: el servidor decide
  entre marcadores individuales y celdas agregadas según cuántos coincidan, y el
  total es siempre exacto. Los marcadores llevan icono por tipo de vivienda, pin
  hueco si es alquiler y anillo rojo si es chollo. La capa de calor colorea por
  €/m² con tramos por cuantiles. Se puede **dibujar el área de búsqueda a mano**.
- **Lista** paginada en servidor, con tarjetas y ordenación por fecha, precio o
  desviación.
- **♥ Favoritos**, en `localStorage`, sin cuenta ni backend.

**Panel de filtros**: operación, ciudad, precio, superficie, habitaciones, tipo
de inmueble y, plegados en «Más filtros», baños, planta, año, estado, extras
(ascensor, garaje, piscina…) y distancia máxima al centro y al metro. Más el
interruptor **★ Solo chollos**.

**Panel de estadísticas**, que sigue los filtros activos: precio medio, mediana,
€/m², rango habitual, histograma de precios, tabla por zona, €/m² por
habitaciones y por superficie, curva de €/m² por distancia al centro, e impacto
de cada extra.

**Modelo de precio** entrenado en el notebook (MAE 47.943 €, MAPE 14,1 %,
R² 0,935) que estima cada anuncio y marca como «posible chollo» los que se piden
un 25 % o más por debajo.

Lo que **no** hace: no tiene autenticación, no guarda nada en servidor, no lee
la API real de Idealista (Fase 4, pendiente) y no conserva la búsqueda en la URL.

---

## Fase 0 — Scaffolding ✅ Completa

- [x] Estructura del repo: `backend/`, `frontend/`, `data/`, `scripts/`
- [x] Backend empaquetado (`backend/pyproject.toml`, instalable con `pip install -e`)
- [x] Frontend Vite + React 18 + TypeScript en modo estricto
- [x] Configuración por entorno con `pydantic-settings` (`app/config.py`), con `.env.example`
- [x] Interfaz abstracta `ListingSource` (`app/ingestion/base.py`): `fetch_listings()`,
      `iter_listings()` para streaming, `health_check()` y contadores `SourceStats`
- [x] Registro por nombre (`app/ingestion/registry.py`): dar de alta una fuente es
      un decorador `@register_source` y un import
- [x] Modelo canónico `Listing` con enums `Operation` / `PropertyType`
- [x] Pipeline de ingesta genérico (`app/ingestion/pipeline.py`), por lotes de 2.000
- [x] CLI de mantenimiento: `python -m app.cli init-db | sources | ingest --source X`
- [x] `Dockerfile` de backend y de frontend, `docker-compose.yml`, `.dockerignore` por contexto

Pendiente en esta fase: **nada**.

---

## Fase 1 — Ingesta desde dataset estático ✅ Completa

- [x] `StaticDatasetSource` (`sources/static_dataset.py`): lee el CSV exportado de
      `idealista18` y normaliza precio, superficie, habitaciones y coordenadas
- [x] Exportador de los `.rda` a CSV: `scripts/export_idealista18.R` (R solo hace
      falta una vez; en tiempo de ejecución no se necesita)
- [x] `SampleCsvSource`: 8 anuncios de ejemplo versionados, para arrancar sin dataset
- [x] Validación de coordenadas contra un bounding box de España, con los descartes
      contados y con ejemplos, para que un fichero malo sea diagnosticable
- [x] Deduplicación por `ASSETID` quedándose con el trimestre más reciente
      (opción `keep_all_periods=True` para conservar todos)
- [x] Almacenamiento SQLite (`app/storage/database.py`) con WAL, esquema versionado
      e índices de cobertura para los filtros
- [x] `upsert_many` idempotente sobre `global_id = fuente:id`
- [x] Cargador dedicado `python -m scripts.load_initial_data`
- [x] Tests: `tests/test_static_dataset.py`, `tests/test_smoke.py`
- [x] Base poblada: **149.923 anuncios** cargados

Pendiente / limitaciones conocidas del origen:

- [ ] El dataset solo trae **operaciones de venta**: no hay ni un anuncio de alquiler,
      así que `tipo_operacion=alquiler` devuelve 0 resultados. Es el dataset, no un fallo.
- [ ] `zone` es solo la **ciudad** (Madrid / Barcelona / Valencia). El dataset trae
      códigos de distrito y barrio que todavía no se traducen a nombres.
- [ ] El dataset **no trae fotos ni URL** del anuncio original.

---

## Fase 2 — API backend ✅ Completa

### Endpoints

- [x] `GET /api/listings` — lista paginada (`limit` ≤ 1000, `offset`) más el `total` real
- [x] `GET /api/listings/facets` — ciudades (con sus límites geográficos), operaciones,
      tipos y topes de los deslizadores, para que el frontend no codifique nada a mano
- [x] `GET /api/listings/map` — puntos o celdas agregadas según cuántos coincidan.
      **Sin tope**: `total` es siempre exacto y nada se trunca
- [x] `GET /api/listings/{id}` — detalle por `fuente:id` o por `id` a secas
      (404 si no existe, **409** si el id está en varias fuentes)
- [x] `GET /api/listings/{fuente}/{id}` — detalle sin ambigüedad posible
- [x] `GET /api/stats` — agregados sobre el **mismo conjunto filtrado**: totales,
      percentiles (p25/p50/p75/p90/p99), media por zona e histograma de precios
- [x] `GET /api/health` y `GET /api/health/ready` (esta reporta cuántos anuncios hay)
- [x] `GET /api/sources` — fuentes registradas, si están sanas y cuánto han cargado

### Filtros (los aceptan tanto `/listings` como `/stats`)

- [x] `precio_min`, `precio_max`
- [x] `m2_min`, `m2_max`
- [x] `habitaciones` (exacto) y `habitaciones_min` (mínimo)
- [x] `tipo_operacion` (venta / alquiler), `tipo_inmueble` (piso, casa, estudio…)
- [x] `zona` (ciudad o barrio, sin distinguir mayúsculas), `source`
- [x] Bounding box `lat_min` / `lat_max` / `lon_min` / `lon_max` — los cuatro o ninguno
- [x] `poligono` — área dibujada a mano, `lat,lon;lat,lon;...`. Se resuelve **dentro de
      SQL** con una función registrada, precedida del bounding box del polígono para que
      el índice haga el trabajo grueso; así el `COUNT(*)` no se separa de las filas
- [x] Validación cruzada: `precio_min > precio_max` es 422, un bbox parcial es 422, y
      `extra="forbid"` hace que una errata como `precio_minimo` falle en vez de
      ensanchar la búsqueda en silencio

### Transversal

- [x] Respuestas tipadas con Pydantic v2 (`app/models/responses.py`)
- [x] OpenAPI en `/docs`, con descripciones en castellano en cada endpoint
- [x] CORS: lista explícita más una regex para `localhost` / `127.0.0.1` en cualquier puerto
- [x] Filtrado y paginación empujados a SQL, nunca en memoria
- [x] Percentiles con dos estrategias según el tamaño del conjunto
      (umbral `PERCENTILE_SCAN_THRESHOLD = 50.000`)
- [x] **43 tests de API** en `tests/test_api.py`, sobre datos sembrados a mano

Pendiente:

- [ ] Sin caché de respuestas ni `ETag`: cada movimiento del mapa recalcula las
      estadísticas. A 150k filas responde de sobra, pero es lo primero que mirar si
      el dataset se multiplica.
- [ ] Sin límite de peticiones (rate limiting) ni paginación por cursor.

---

## Fase 3 — Frontend ✅ Completa (con extras pendientes)

### Hecho

- [x] Layout de dos columnas: panel lateral fijo más área de contenido
- [x] **Conmutador Mapa / Lista** sobre exactamente los mismos filtros
- [x] **Vista de mapa** (`MapView.tsx`): Leaflet con teselas de OpenStreetMap
- [x] **Sin tope de anuncios.** El mapa ya no pide 1.000 de 149.923: `/listings/map`
      decide entre marcadores individuales y celdas agregadas en SQL según cuántos
      coincidan, y `total` es siempre exacto
- [x] **Clustering** (`MarkerLayer.tsx`) con `leaflet.markercluster` para los marcadores
      individuales, gobernado de forma imperativa en vez de con un `<Marker>` de React
      por anuncio
- [x] **Celdas agregadas** para las vistas amplias: un punto por celda con el número
      dentro, radio proporcional a la raíz del recuento, y clic para acercar
- [x] **Iconos por tipo de vivienda**: SVG en línea, un color y una forma por tipo
      (piso, casa, estudio, dúplex, ático, habitación, terreno, garaje, local), pin
      hueco para alquiler y leyenda con los tipos presentes en pantalla
- [x] **Caja de detalle fija abajo a la derecha** (`DetailCard.tsx`) en lugar del popup
      sobre el marcador; carga el anuncio completo con `fetchListing()`
- [x] **Capas base intercambiables**: Mapa (OSM), Satélite (Esri), Terreno (OpenTopoMap)
      y Claro (CARTO), cada una con su atribución
- [x] **Dibujar la zona de búsqueda a mano** (`DrawControl.tsx`): se traza arrastrando,
      se simplifica en el navegador y viaja como `poligono=lat,lon;...`. Filtra la lista,
      el mapa y las estadísticas por igual
- [x] **Elegir ciudad mueve el mapa**: seleccionar Madrid filtra y vuela hasta ella, con
      los límites que devuelve `/listings/facets`
- [x] **Vista de lista** (`ListView.tsx`): tarjetas con foto, precio, €/m², m²,
      habitaciones, tipo y zona, con paginación en servidor
      ("1–24 de 149.923 · página 1 de 6.247")
- [x] **Foto de sustitución** (`placeholder.ts`): degradado SVG determinista derivado
      del id del anuncio, en línea como data URI — el dataset no trae imágenes
- [x] **Panel de filtros** (`FilterPanel.tsx`): operación, ciudad, rango de precio,
      rango de m², habitaciones mínimas, tipo de inmueble y botón "Limpiar (n)"
- [x] Las opciones del panel salen de `/listings/facets`, no están escritas a mano
- [x] **Panel de estadísticas** (`StatsPanel.tsx`): precio medio, mediana, €/m², rango
      habitual (p25–p75), histograma de precios y tabla de media por zona;
      **hacer clic en una zona filtra por ella**
- [x] Los filtros afectan a las dos vistas y a las estadísticas **sin recargar la página**
- [x] Casilla "buscar solo en el área visible": decide si el bbox del mapa acota también
      la lista y las estadísticas
- [x] Peticiones con `AbortController` y filtros con rebote de 300 ms
      (`useResource.ts`, `useDebounced.ts`): el dato anterior se queda en pantalla
      mientras llega el siguiente, así nada parpadea en vacío
- [x] "Ver en el mapa" desde una tarjeta centra el mapa en ese anuncio
- [x] Proxy de Vite `/api` → FastAPI: en desarrollo el navegador ve un solo origen
- [x] Tema claro y oscuro con tokens CSS; layout responsive a partir de 900 px
- [x] TypeScript estricto, sin errores de tipos

### A medias / pendiente

- [ ] **Los filtros no van en la URL.** No se puede compartir ni guardar una búsqueda,
      y el botón "atrás" del navegador no deshace un filtro.
- [ ] `fetchSources()` tampoco se usa: no hay ninguna pantalla que diga de dónde salen
      los datos ni cuándo se cargaron.
- [ ] Precio y m² son **campos numéricos**, no deslizadores de rango; los topes que
      devuelve `/facets` solo se enseñan como texto de ayuda.
- [ ] **Cero tests de frontend**: no hay runner configurado (ni Vitest ni Playwright).
- [ ] En móvil el panel lateral no se pliega: ocupa el 45 % superior de la pantalla.

---

## Fase 4 — Conector de la API real de Idealista ⛔ No implementada (correcto)

Confirmado: **no está implementada**, tal y como se planificó.

### Lo que ya está preparado

- [x] `IdealistaApiSource` existe como esqueleto registrado (`sources/idealista.py`):
      `fetch_listings()` lanza `NotImplementedError` y `health_check()` devuelve `False`
      si faltan credenciales
- [x] Credenciales por variables de entorno ya declaradas en `Settings`:
      `IDEALISTA_API_KEY` / `IDEALISTA_API_SECRET`
- [x] `docker-compose.yml` ya las propaga al contenedor

### Verificación: ¿aguanta la interfaz de la Fase 0 sin refactorizar?

Sí. Añadir la fuente real toca **un solo archivo** más un import en
`sources/__init__.py`. Nada fuera del paquete `ingestion` sabe de qué fuente viene
un anuncio.

| Necesidad de la Fase 4 | ¿Lo cubre la interfaz actual? |
| --- | --- |
| Paginar miles de resultados | Sí — `iter_listings()` permite ir en streaming sin cargar todo en memoria |
| Rate limiting | Sí — es un detalle interno de la fuente; nada fuera del `fetch` lo ve |
| Token OAuth2 | Sí — cabe en `__init__` y en `health_check()` |
| Credenciales por entorno | Sí — ya están en `Settings` |
| Convivir con el dataset estático | Sí — `global_id = fuente:id`, y `/listings?source=` filtra por fuente |
| Reportar descartes y errores | Sí — `SourceStats` y `ListingSourceError` |
| Parámetros de búsqueda (centro, radio) | Sí — viajan en el `__init__` de la fuente, no en `fetch_listings()` |
| Elegir la fuente activa | **A medias** — se elige con `--source` en el CLI; no hay ajuste de configuración |

### Pendiente cuando lleguen las credenciales

- [ ] Intercambio OAuth2 de `api_key`/`api_secret` por bearer token
- [ ] Paginación de `/3.5/es/search` y mapeo de `elementList` a `Listing`
- [ ] Limitador de peticiones que respete la cuota del plan
- [ ] Ajuste de configuración `ACTIVE_SOURCE`, para elegir fuente sin pasar `--source`
- [ ] **Campo de foto en `Listing`.** La API real devuelve `thumbnail` y el modelo no
      tiene dónde guardarlo. Añadirlo toca `Listing`, el esquema SQLite, el repositorio
      y `ListingCard`: es el único punto donde la Fase 4 obliga a tocar varias capas,
      y conviene decidirlo **antes** de escribir el conector.
- [ ] Programar refrescos periódicos (la API tiene cuota; no se puede recargar a demanda)

---

## Análisis y modelo de precio ✅ Completa

Fase no planificada al principio; se añade porque el proyecto es de portfolio y el
dataset da para ello.

### Notebook — `notebooks/analisis.ipynb`

- [x] Versionado **con sus salidas y gráficos**: se lee en GitHub sin ejecutarlo
- [x] Saneamiento documentado: deduplicación por `ASSETID` (189.923 filas → 149.923
      viviendas; sin ella el mismo piso cae en train y en test), exclusión de
      `UNITPRICE` por fuga de objetivo, y un anuncio de «Madrid» que está en Almería
- [x] EDA: distribución del precio y del €/m², superficie, habitaciones, baños,
      planta, antigüedad, calidad catastral, 20 variables de extras
- [x] Ubicación: gradiente al centro por ciudad, distancia al metro, mapas de calor
      hexagonales de las tres ciudades y 120 zonas por k-means
- [x] Modelo: dos líneas base (mediana y Ridge sin zona) contra
      `HistGradientBoostingRegressor`
- [x] Diagnóstico del error: estimado contra real, residuos, error por decil de
      precio, por ciudad y por tamaño
- [x] Importancia por permutación (no la interna del modelo, que se sesga hacia las
      variables con muchos valores distintos)
- [x] Limitaciones escritas: no es un tasador, es 2018, es precio de oferta y no es
      causal

### Resultados

| | MAE | MAPE | R² (log) | R² (€) |
| --- | ---: | ---: | ---: | ---: |
| Mediana | 196.836 € | 64,5 % | 0,00 | −0,08 |
| Ridge sin zona | 112.717 € | 26,0 % | 0,81 | −9,19 |
| **Gradient boosting** | **47.943 €** | **14,1 %** | **0,935** | **0,918** |

Validación cruzada de 5 particiones: R²(log) = 0,932 ± 0,001. Error mediano 10,3 %;
el 49 % dentro de ±10 % y el 78 % dentro de ±20 %.

`BARRIO` (0,61) y `CONSTRUCTEDAREA` (0,47) se llevan el **89 %** de la importancia.

### Exportación

- [x] `backend/models/price_model.joblib` — paquete con modelo, k-means, orden de
      columnas, categorías y métricas (1,4 MB)
- [x] `backend/models/price_model.json` — los mismos metadatos en texto plano, para
      leerlos sin abrir el pickle
- [x] Verificación de ida y vuelta **dentro del notebook**: se recarga desde disco y
      se comprueba que predice lo mismo (diferencia máxima 0,000000 €)
- [x] `app/pricing/` — cargador con el contrato de columnas en código, no en prosa
- [x] 10 tests (`tests/test_pricing.py`)
- [x] Extras de dependencias separados: `[notebook]` para analizar, `[serving]` para
      inferir. El runtime del API no arrastra pandas ni scikit-learn

Pendiente:

- [x] **El modelo ya se usa en el producto**: ver «Chollos» más abajo
- [ ] Sigue sin haber un `POST /api/valoracion` que estime a la carta una vivienda
      que no esté en la base. El cargador (`app.pricing`) ya lo soporta.
- [ ] El `Dockerfile` copia `app` y `scripts`, no `models`: el modelo no viaja en
      la imagen, así que dentro del contenedor `score_listings` no funcionaría.
- [ ] Barrios administrativos de verdad en vez del k-means (§4.3 del notebook)
- [ ] Validación espacial: la partición es aleatoria, no por zonas
- [ ] Intervalos de predicción con `loss="quantile"` en vez de un número suelto

---

## Docker verificado, y dos arreglos del mapa ✅ Completa

### El mapa

- [x] **Madrid salía partido en dos** (3,4k y 72k). La rejilla de agregación tiene
      que cortar en algún sitio, y a zoom 6 la celda mide 0,94° con un borde que
      cae en la longitud −3,75: dentro de la ciudad. Mover la rejilla solo cambia
      de víctima, así que mientras la celda sea más grande que una ciudad la
      agrupación pasa a ser **por zona**: un punto por ciudad, que es lo que la
      vista de país está preguntando. Ahora son 3 celdas: 75.804 / 46.728 / 27.391
- [x] La extensión de la celda se calcula de forma **robusta** (media ± 3σ). Con
      el mínimo y el máximo, la caja de Madrid llegaba hasta Almería por culpa de
      un anuncio mal etiquetado: 400 km de alto en vez de 23
- [x] **Hacer clic en un clúster ahora enseña solo los suyos.** Antes solo volaba,
      y como el viewport es un rectángulo con la proporción de la pantalla y nunca
      la de la celda, los vecinos volvían a colarse. Si la celda es una zona se
      filtra por su nombre; si es de rejilla, por su rectángulo como área dibujada
- [x] Filtrar por nombre y no por rectángulo no es un detalle: `zona=Madrid` es
      una igualdad sobre un índice, mientras que el rectángulo obliga a evaluar un
      polígono fila a fila sobre las 75.000 que caen dentro. En el contenedor, la
      diferencia entre responder al momento y tardar medio minuto

### Docker

Verificado de punta a punta por primera vez. Tres cosas estaban mal:

1. **La base en un montaje de Windows es inservible.** Medido dentro del
   contenedor con los 137 MB de `housing.db`:

   | | montaje de Windows | volumen de Docker |
   | --- | ---: | ---: |
   | `SELECT COUNT(*)` | 17.377 ms | **80 ms** |
   | contar por zona | 9.532 ms | **164 ms** |

   217× y 58×. Los endpoints se quedaban sin responder. Ahora la base vive en un
   volumen y `./data` se monta en `/seed` en solo lectura para los CSV; el
   *entrypoint* copia la base del host al volumen la primera vez.

2. **`--reload` y los montajes de código costaban 190 % de CPU** en reposo: sin
   inotify sobre NTFS, el vigilante sondea. Fuera los dos. Este `compose` ejecuta
   la aplicación; para desarrollar está `start.ps1`.

3. **Los healthchecks mentían.** El del backend tenía 3 s de límite cuando la
   sonda tarda 4,2–4,6 s ahí dentro (el coste es arrancar CPython, no la
   biblioteca: con `http.client` tarda lo mismo). El del frontend usaba
   `localhost`, que resuelve a `::1` antes que a `127.0.0.1`, y Vite solo escucha
   en IPv4.

### Un fallo de diseño que la carga destapó

Con una pestaña vieja recargando en bucle, el backend se quedó a 250–600 % de CPU
y dejó de responder. La caché computaba **fuera del lock a propósito**, y con 24
hilos fallando en la misma clave los 24 recalculaban el mismo agregado sobre 150k
filas a la vez, peleándose por el GIL.

- [x] **Single-flight**: el primero calcula y el resto esperan su resultado.
      Medido con 30 peticiones idénticas simultáneas: **3,0 s en total**, todas
      correctas, y la CPU vuelve a 0,25 % al acabar. Antes, ese mismo patrón
      tumbaba el servidor durante minutos
- [x] Un cálculo que falla libera a los que esperan en vez de dejarlos colgados,
      y un `bump()` durante el cálculo también. Con tests para las dos cosas

---

## Más filtros y más estadísticas ✅ Completa

### El problema de partida

El panel solo podía filtrar por lo que la tabla guardaba: 15 columnas. Los
extras, la planta, el año y las distancias estaban **en el CSV y no en la base**,
así que no se podía filtrar por ellos en SQL.

La respuesta no fue meter columnas de idealista18 en el esquema normalizado, sino
mirar cuáles son **atributos comunes de cualquier portal** —la API oficial de
Idealista también los devuelve— y promover solo esos:

- [x] `bathrooms`, `floor`, `year_built`, `condition`
- [x] `distance_to_center_km`, `distance_to_metro_km`
- [x] 9 extras (`ascensor`, `terraza`, `garaje`, `aire_acondicionado`, `piscina`,
      `portero`, `jardin`, `trastero`, `armarios`), una columna cada uno

Se quedaron fuera a propósito las orientaciones y la calidad catastral: son del
dataset, no criterios de búsqueda de un portal.

Los extras van como **columnas y no como JSON ni máscara de bits** porque son
criterios de filtro: SQLite puede indexarlos y el SQL se lee. En la API salen
como una lista de tokens, que es lo que hace legible el JSON; el mapeo vive en la
capa de almacenamiento y en ningún sitio más.

- [x] Migración `SCHEMA_VERSION = 4` y recarga: 149.923 anuncios con baños, año,
      estado y distancias al **100 %**, planta al 95 %
- [x] Ningún índice propio para los extras: el 73 % tiene ascensor, así que por
      separado no acotan nada, y siempre acompañan a un filtro de precio o zona
      que sí. Sí lo lleva `distance_to_center_km`, donde un radio pequeño descarta
      mucho

### Filtros nuevos en el panel

Van en una sección **«Más filtros»** plegable, con su propio contador, que se abre
sola si ya hay algo dentro — un filtro activo escondido detrás de un triángulo es
la peor manera de perder resultados sin saber por qué.

| filtro | comprobado sobre los datos reales |
| --- | ---: |
| `extras=ascensor` | 108.785 |
| `extras=ascensor&extras=garaje` | 23.486 |
| `+ piscina` | 9.932 |
| `banos_min=2` | 65.961 |
| `planta_min=1` (sin bajos) | 128.461 |
| `estado=obra_nueva` | 3.910 |
| `centro_max_km=1` | 15.679 |
| `metro_max_km=0.25` | 52.967 |

Los extras marcados se piden **todos**, no cualquiera: un panel que devuelve pisos
sin ascensor cuando has marcado «ascensor» está roto, por muchas otras casillas
que hayas marcado. El panel lo dice en cuanto hay más de uno.

### Dos estadísticas nuevas

**Precio por m² según la distancia al centro**, como línea y no como barras: la
distancia es continua y lo que cuenta es la forma entre los puntos. En Madrid:

| km al centro | <0,5 | 0,5–1 | 1–2 | 2–3 | 3–4 | 4–6 | 6–8 | >8 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| €/m² | 5.075 | 4.875 | ~5.000 | 4.886 | 3.565 | 2.834 | 2.661 | 2.855 |

Meseta hasta los 3 km y luego un acantilado. Con **varias ciudades mezcladas el
gráfico avisa y no se deja leer como si fuera de una**: los tres gradientes son
distintos y la media no describe ninguno.

**Qué acompaña a un precio alto**: diferencia de €/m² entre tener cada extra y no
tenerlo, en un carril con el cero en el centro. En Madrid: ascensor +46 %,
portero +33 %, aire +19 %, … jardín −2 % y **terraza −12 %**.

Debajo va el aviso, que no es palabrería: son **correlaciones**. La piscina no
sube el piso un 7 %, es que los pisos con piscina están donde el metro ya vale
eso. La terraza sale negativa por lo mismo al revés — abunda en los barrios
exteriores, más baratos.

- [x] Un extra que **todo** el conjunto filtrado tiene se cae del gráfico: sin los
      dos lados no hay comparación, y media barra sería mentira. Comprobado
      filtrando por ascensor y viendo desaparecer «ascensor»
- [x] Ambas siguen los filtros activos, y una sola pasada de SQL con agregados
      condicionales en vez de dos consultas por extra (18 pasadas para 9 barras)

### Un bug encontrado al probarlo

Marcar dos extras seguidos dejaba solo el segundo. Los dos clics leían el
`filters` capturado en el render en que se pintaron las fichas, así que el segundo
pisaba al primero — el fallo clásico de closure obsoleto, que solo aparece si se
pulsa rápido. `onChange` pasa a aceptar una función actualizadora, y React aplica
cada cambio sobre el estado vigente. De paso, los dos contadores del panel
(«Limpiar (n)» y el de la sección) contaban cosas distintas de lo mismo; ahora
cuentan igual.

---

## Capa de calor, favoritos y gráficos ✅ Completa

### Mapa de calor por €/m²

- [x] Conmutador **Marcadores / Calor** en el propio mapa
- [x] `?calor=true` en `/listings/map`: agrega **siempre**, aunque los anuncios
      cupieran uno a uno. Si cambiara a marcadores al acercarse, la capa
      desaparecería justo cuando se quiere comparar barrio con barrio
- [x] Cada celda trae `avg_price_per_m2`, `with_size` y su **extensión real**
      (`lat_min`…`lon_max`), para dibujarla como el rectángulo que ocupa y no como
      una mancha alrededor del centroide
- [x] Respeta todos los filtros: verificado con Valencia (27.391 anuncios, 101
      celdas, y la leyenda pasando de 1,3 mil–4 mil a 707–2,5 mil €/m²)
- [x] Se dibuja en **canvas** (`preferCanvas`), comprobado en el navegador: 0 nodos
      SVG para 162 rectángulos

Dos decisiones que conviene no deshacer sin pensarlo:

- **Es un coropleto de celdas, no un `Leaflet.heat`.** Una capa de calor pinta
  densidad sumando pesos de puntos solapados; con €/m² eso haría brillar más un
  barrio con muchos pisos baratos que uno con pocos caros, o sea respondería
  «dónde hay muchos anuncios» disfrazado de «dónde es caro».
- **Los tramos van por cuantiles.** El €/m² está sesgado (1.115 a 6.731 solo en
  Madrid); ocho tramos de ancho igual dejarían casi todas las celdas en el primero
  y gastarían seis colores en un puñado de átipicos.
- **Se promedia el ratio, no se dividen las medias.** `AVG(price/size_m2)`, no
  `AVG(price)/AVG(size_m2)`: lo segundo deja que un ático de 400 m² hable por un
  barrio entero de estudios.
- Una celda sin superficies declaradas es **gris**, no del color más barato:
  «desconocido» y «tirado de precio» no son lo mismo.

### Favoritos en `localStorage`

- [x] Corazón en cada tarjeta y en el panel de detalle
- [x] Pestaña **♥ Favoritos** con contador, y su propio mensaje cuando está vacía
- [x] Persisten al recargar (verificado) y se sincronizan entre pestañas: `storage`
      para las otras pestañas y un evento propio para los demás usos del hook en
      esta, sin el cual el contador y la estrella de la tarjeta se desincronizan
- [x] `localStorage` tratado como **entrada no fiable**: el usuario puede editarlo,
      así que lo que no sea una lista de cadenas se descarta en vez de romper la
      carga; y todos los accesos van en `try/catch` para no caerse en modo privado
- [x] Filtro `ids` repetible en `/listings`, para pedir los favoritos en **una**
      petición en vez de una por favorito. Parámetros ligados, nunca concatenados;
      hay un test con una inyección SQL que lo comprueba
- [x] Un favorito puede sobrevivir al anuncio que apunta: no es un error, se ignora

Límite dicho en la interfaz, no callado: no viajan a otro navegador ni a otro
dispositivo, y borrar los datos del sitio los pierde.

### Gráfico nuevo en las estadísticas

- [x] `/stats` devuelve `by_rooms` y `by_size`
- [x] Gráfico de barras con dos pestañas (Habitaciones / Superficie), sigue los
      filtros activos como el resto del panel

Es el hallazgo del notebook, ahora en vivo: ambos plotean **€/m²**, no precio
total. El precio total sube con el tamaño y para eso no hace falta un gráfico; lo
que el análisis encontró es que el precio *unitario* dibuja una **U**. Con los
datos de Madrid:

| habitaciones | 0 | 1 | 2 | **3** | 4 | 5 | 6+ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| €/m² | 4.349 | 4.388 | 3.582 | **3.091** | 3.969 | 4.455 | 4.539 |

Los estudios son pequeños **y** céntricos; las viviendas grandes están en barrios
caros; el mínimo está en el piso familiar de tres habitaciones. Por superficie sale
la misma curva, con el mínimo en 60–80 m² (2.980 €/m²).

- [x] Barras escaladas desde cero, no desde la barra más baja: arrancar el eje en el
      mínimo convertiría un 15 % de diferencia en un acantilado
- [x] Sin mediana por tramo: costaría una consulta extra por barra para un gráfico
      de dos centímetros en una barra lateral

---

## Rendimiento ✅ Completa

Pensado para que aguante cuando entre la API real de Idealista y el dataset
crezca. Todo lo de abajo está **medido**, no supuesto: `python -m scripts.benchmark`
queda en el repo para poder repetirlo.

### Cómo se midió

Comparar dos ejecuciones separadas del benchmark no vale: la primera tanda daba un
«1,2× más lento» uniforme en consultas que ni siquiera se habían tocado, que
resultó ser carga de la máquina. Las cifras de abajo salen de un **A/B en el mismo
proceso**: dos copias de la base, una con el esquema viejo y otra con el nuevo,
medidas alternadamente, mediana de 5 ejecuciones y la caché invalidada antes de
cada una para medir SQL y no la caché.

### 1. Índices de filtro — hasta 51× más rápido

El problema no era que faltaran índices, sino que eran **estrechos**: SQLite usa
un índice por tabla, así que con `precio` indexado por su cuenta acotaba por
precio y luego iba a la tabla fila por fila para comprobar zona, habitaciones y
tipo. Tres índices compuestos y cubridores lo resuelven.

| consulta | antes | después | |
| --- | ---: | ---: | --- |
| `zona + precio + habitaciones + operación` | 581 ms | **11 ms** | 51× |
| `precio + habitaciones` | 569 ms | **13 ms** | 45× |
| `tipo + precio` | 624 ms | **12 ms** | 50× |
| `/stats?zona=Madrid` | 557 ms | **49 ms** | 11× |
| facetas | 208 ms | **159 ms** | 1,3× |
| tamaño del fichero | 116,7 MB | 116,7 MB | igual |

Tres detalles que solo aparecieron midiendo:

- `idx_listings_zone_price` indexaba `zone` con la colación BINARY por defecto,
  así que `zone = ? COLLATE NOCASE` **no podía usarlo** y caía en un índice
  estrecho más una lectura de tabla por cada una de las 75.804 filas de Madrid.
- `operation` va al **final** de los índices compuestos, nunca al principio: tiene
  un solo valor distinto en este dataset, así que como prefijo no descarta nada
  y deja el índice inservible si el usuario no filtra por él. Al final no cuesta
  páginas y mantiene el índice cubridor: 240 ms → 12 ms.
- Hizo falta **volver a añadir** un índice estrecho `(price, size_m2)`. Los
  agregados sin filtrar recorren el índice de precios entero, y la versión ancha
  lleva dos columnas más por entrada que ese recorrido no necesita: `/stats` sin
  filtros pasó de 54 ms a 89 ms hasta que se recuperó el estrecho.

### 2. Índice espacial R-Tree — probado y **descartado**

Implementado sobre una copia y medido. **Es más lento**, así que no se ha
integrado:

| vista | índice actual | R-Tree | |
| --- | ---: | ---: | --- |
| manzana (1.917 filas) | 0,5 ms | 1,1 ms | 2,2× más lento |
| barrio (19.943) | 3,2 ms | 21,5 ms | 6,7× más lento |
| ciudad (75.763) | 9,0 ms | 74,6 ms | 8,3× más lento |
| país (149.923) | 18,3 ms | 159,3 ms | 8,7× más lento |

La razón es que `idx_listings_bbox (latitude, longitude)` ya es **cubridor**: el
`COUNT` no toca la tabla. El R-Tree devuelve una lista de rowids y obliga a una
búsqueda aleatoria por fila, que cuesta más que el recorrido secuencial que
sustituye. Y el margen de mejora es pequeño: la banda de latitud contiene
**1,00×** las filas de la caja en las vistas de ciudad y país, y 3,45× en la más
estrecha; no hay casi nada que podar.

**Cuándo habrá que revisarlo.** Con la API real cubriendo toda España, una caja
sobre Sevilla compartirá banda de latitud con Murcia, Badajoz y Huelva, y ese
factor de amplificación crecerá. El criterio es ese número: si pasa de ~10×, el
R-Tree empieza a compensar. El benchmark lo imprime.

### 3. Endpoint ligero del mapa — ya existía; se le quitó grasa

`GET /listings/map` ya devuelve solo lo necesario, no el anuncio entero:

| | bytes / anuncio |
| --- | ---: |
| `/listings` (16 campos) | 398 |
| `/listings/map` (7 campos) | 205 → **174** |

Bajar a los 4 campos mínimos (`id`, `lat`, `lon`, `precio`) ahorraría un 39 % más,
pero los otros tres **pintan algo**: `property_type` decide el icono (11,4 % del
payload), `operation` si el pin va hueco (9,8 %) y `price_deviation` si se resalta
como chollo (18 %). Quitarlos obligaría a una segunda petición por marcador.

Lo que sí sobraba era **precisión**: se enviaba `40.415002935187` y
`-19.529510836867093`. Redondeando a 5 decimales de coordenada (~1,1 m sobre el
terreno) y 1 de porcentaje, el payload cae un **15 %** sin que se note en pantalla.

### 4. Caché de agregados — hasta 23× en la segunda petición

`app/storage/cache.py`. La clave lleva dentro una **versión de los datos**, así que
la invalidación no es algo que nadie tenga que acordarse de hacer: cualquier
escritura sube la versión y todas las entradas anteriores quedan inalcanzables de
golpe. No hay un `invalidate()` que olvidar ni ventana en la que se pueda servir
una respuesta caduca.

| endpoint | primera vez | segunda | |
| --- | ---: | ---: | --- |
| `/stats?zona=Madrid` | 159 ms | **7 ms** | 23× |
| `/stats` sin filtros | 8 ms | 6 ms | ya estaba caliente |
| `/listings/facets` | 6 ms | 5 ms | — |

Se cachean `count`, `overall_stats`, `zone_stats`, el histograma y las facetas. No
se cachean las filas de `/listings`: son páginas de 24, ya baratas, y guardarlas
sería memoria a cambio de nada.

### 5. La consulta paginada con bbox — hasta 3.000×

La más usada de todas, y la que peor estaba: acotaba por bbox y luego **ordenaba
en un árbol temporal las 75.000 filas del área** para quedarse con 24.

`idx_listings_recent` ahora lleva también `latitude` y `longitude`, de modo que se
puede recorrer ya ordenado y parar al encontrar 24. El planificador no lo elegía
solo, así que la consulta paginada envuelve el bbox en `likely()` — una **pista**,
no un `INDEXED BY`: si el índice desapareciera, la consulta seguiría funcionando.

| vista | antes | después | |
| --- | ---: | ---: | --- |
| manzana | 2,3 ms | 7,8 ms | 3,4× más lento |
| barrio | 37 ms | **7,4 ms** | 5× |
| ciudad | 178 ms | **7,3 ms** | 24× |
| país | 339 ms | **0,1 ms** | 3.061× |

El intercambio está aceptado a conciencia: se pierden 5 ms en la vista más
pequeña, donde ya era instantáneo, y se ganan 170–340 ms en todas las demás.

### 6. Migración de esquema

`CREATE INDEX IF NOT EXISTS` no recrea un índice cuya definición cambió: se queda
callado con la forma vieja para siempre, y el único síntoma es que las consultas
siguen lentas. Ahora hay `PRAGMA user_version` y `Database._migrate`, que borra
por nombre lo que haya cambiado antes de recrearlo. La primera vez tarda 1,7 s; las
siguientes, 0,01 s. `ANALYZE` solo se ejecuta tras migrar o en una base sin
estadísticas, no en cada arranque.

### Frontend

**5. Leaflet.heat — no se usa.** No está en `package.json` ni en el código. La
densidad se muestra con celdas agregadas en servidor (§ mapa) y con
`leaflet.markercluster`; el mapa de calor hexagonal del notebook es matplotlib,
una imagen. Los marcadores son `divIcon`, es decir DOM, así que `preferCanvas` no
les afecta: solo toca capas vectoriales. Se ha declarado igualmente porque sí hay
una —el trazo del dibujo a mano, que se redibuja en cada movimiento del ratón— y
porque deja el mapa preparado si algún día se añade una capa de calor.

**6. Debounce — ya existía; se corrigió dónde se aplicaba.** Medido en el navegador
con eventos de confianza vía CDP:

| gesto | antes | después |
| --- | ---: | ---: |
| un arrastre de 30 pasos | 3 peticiones | 3 |
| **tres arrastres encadenados** | **9** | **3** |
| teclear 6 dígitos en el precio | 3 | 3 |

Un solo arrastre ya se colapsaba bien. Lo que fallaba era explorar: tres arrastres
cortos seguidos disparaban tres tandas. El arreglo no fue subir el retardo sin más
—eso solo ayudó al mapa, porque la lista y las estadísticas seguían reaccionando
al viewport con el retardo corto— sino **poner el debounce en el origen**: cada
estado se estabiliza con el retardo que su gesto merece (teclear 300 ms, mover el
mapa 600 ms) y los consumidores componen valores ya estabilizados.

**7. Virtualización — no procede, y con números.** La lista pagina en servidor a 24
tarjetas:

| | |
| --- | ---: |
| tarjetas en pantalla | 24 |
| nodos DOM de la lista | 390 |
| recálculo de layout completo | 10,5 ms |
| altura de la lista | 2.654 px (≈3 pantallas) |

`react-window` para 390 nodos añadiría una dependencia, alturas fijas y una capa
de complejidad para virtualizar tres pantallas de scroll. Merecerá la pena si
`PAGE_SIZE` sube por encima de ~200 tarjetas, o si se cambia a scroll infinito;
mientras la lista pagine, no.

---

## Chollos: precio estimado y desviación ✅ Completa

Marcar los anuncios que se piden muy por debajo de lo que el modelo estima.

### Backend

- [x] Columnas `expected_price` y `price_deviation` en `listings`, más migración
      automática por `ALTER TABLE` para las bases que ya existían
- [x] Índice parcial `idx_listings_deviation` (solo las filas puntuadas, ~1/3 de la
      tabla)
- [x] `scripts/score_listings.py` — estima los 149.923 anuncios en ~12 s
- [x] Filtros `solo_chollos`, `desviacion_max` y `solo_estimados`, que componen con
      todo lo demás: mapa, estadísticas y zona dibujada incluidos
- [x] Orden `orden=desviacion`, con los no estimados al final y desempate por
      `global_id` para que la paginación no repita ni se salte filas
- [x] La desviación viaja también en los puntos del mapa
- [x] Un re-ingest borra la estimación: un precio nuevo invalida el cálculo, y una
      marca de chollo caducada es peor que ninguna
- [x] 20 tests nuevos

### La decisión que gobierna el diseño

El modelo usa 35 variables y la tabla guarda 5. Medido sobre el conjunto de test:

| entrada | MAE | MAPE | error mediano |
| --- | ---: | ---: | ---: |
| las 35 variables | 47.943 € | 14,1 % | 10,3 % |
| solo lo que guarda la tabla | 78.947 € | 23,6 % | 18,2 % |

Con un error mediano del 18 % casi ningún anuncio está lo bastante por debajo como
para destacar, y la marca sería ruido. Por eso el cálculo se hace **en la ingesta**,
volviendo al dataset de origen, y no al servir. Lo que no se puede puntuar se queda
en `NULL` y no aparece en ninguna búsqueda de chollos.

Umbral: **−25 %**, unas 2,4 veces el error mediano del modelo. Salen 6.227 de
149.923 anuncios (4,2 %). La desviación se reparte centrada (media +1,4 %, el 52 %
dentro de ±10 %), que es lo que debe hacer un residuo sano.

### Frontend

- [x] Interruptor **★ Solo chollos** en el panel lateral, que además cambia el orden
      de la lista a «mayor desviación primero»
- [x] Tarjetas con borde y distintivo *★ Posible chollo −87 %*; las demás enseñan la
      estimación en pequeño
- [x] Pines del mapa con contorno rojo y estrella, por encima de los normales. El
      color del tipo de vivienda se mantiene: ser un chollo y ser un piso son dos
      hechos independientes, y el contorno se suma al color en vez de sustituirlo
- [x] El panel de detalle explica la estimación y **avisa de lo que es**: una señal
      estadística, no una tasación

Pendiente:

- [ ] La estimación no se recalcula sola tras un `load_initial_data`; hay que lanzar
      `score_listings` a mano (queda avisado al terminar la carga)
- [ ] Sin intervalo de confianza: se enseña un número, no una banda

---

## Puesta en marcha ✅ Completa

- [x] `docker compose up --build` levanta backend y frontend con un solo comando
- [x] El backend **siembra la base al arrancar** (`scripts/ensure_data.py`): esquema,
      dataset completo si lo encuentra, CSV de demostración si no, y nada si ya hay
      filas. Nunca tumba el arranque: una web vacía es diagnosticable, un contenedor
      que no arranca no
- [x] `depends_on: condition: service_healthy` — la web no arranca antes que la API
- [x] `init: true` en ambos servicios, para que Ctrl+C llegue a uvicorn y a node
- [x] Healthcheck también en el frontend, para que `docker compose up --wait` sirva
- [x] `start.ps1` / `start.sh` como alternativa sin Docker: crean el entorno virtual,
      instalan lo que falte, arrancan en orden, esperan a que respondan y abren el
      navegador. Ctrl+C cierra los dos y libera los puertos
- [x] El `Dockerfile` del backend instala con `uv pip install -r pyproject.toml`:
      la lista de dependencias vive en un solo sitio y no puede desincronizarse

Pendiente:

- [ ] **La ruta de Docker no está probada en esta máquina**, porque Docker no está
      instalado. El YAML valida y los Dockerfiles son directos, pero nadie ha
      ejecutado `docker compose up` todavía. Comprobarlo en cuanto haya Docker.
- [ ] No hay imagen de producción del frontend (`vite build` + nginx): compose levanta
      el servidor de desarrollo, que es lo que se quiere para trabajar pero no para
      desplegar.

---

## Cómo se levanta hoy

Con Docker:

```bash
docker compose up --build
```

Sin Docker:

```powershell
.\start.ps1          # Windows / PowerShell
```

```bash
./start.sh           # Git Bash, Linux, macOS
```

Los scripts preparan el entorno virtual y `node_modules` si faltan, arrancan los dos
procesos, esperan a que respondan y abren el navegador. `Ctrl+C` cierra ambos.

- Web: <http://localhost:5173> ← **la URL que hay que abrir**
- API: <http://localhost:8000/api>
- Documentación interactiva: <http://localhost:8000/docs>

**En esta máquina Docker no está instalado**, así que hoy la vía verificada son los
scripts.

---

## Próximos pasos

Por orden de prioridad:

1. **Hacer el primer commit.** 91 ficheros en el índice y ni un solo commit: hoy
   por hoy no hay red de seguridad ni forma de ver qué cambió cuándo. Es lo único
   de esta lista que, si sale mal, cuesta trabajo ya hecho.
2. **Meter los filtros en la URL** (`?precio_max=300000&zona=Madrid&vista=mapa`).
   Es lo que convierte la web en algo que se puede enseñar con un enlace, y de paso
   arregla el botón «atrás». Poco código: `filters.ts` ya sabe serializar a
   `URLSearchParams`; falta leerlos al arrancar y escribirlos con `history.replaceState`.
   Con los filtros que hay ahora —extras, distancias, chollos— reconstruir una
   búsqueda a mano ya es tedioso.
3. **Tests de frontend.** Vitest para `filters.ts`, `format.ts` y `markers.ts`
   (lógica pura, barato) y una prueba de humo de que la lista pinta lo que devuelve
   la API. Es el hueco más grande que queda: 12 componentes y 0 tests, y ya han
   aparecido por aquí dos fallos que un test habría cazado —el closure obsoleto de
   los extras y el contador que contaba otra cosa.
4. **Mapear distritos y barrios** a `zone`. Ahora mismo «zona» es la ciudad, así que
   las estadísticas por zona solo distinguen tres cosas y el mapa agrupa por ciudad
   entera a zoom bajo. El dataset trae los códigos; falta traducirlos a nombres.
5. **Decidir el campo de foto en `Listing`** antes de escribir el conector real: es la
   única decisión de la Fase 4 que arrastra cambios en varias capas.
6. **Plegar el panel lateral en móvil.** Con los filtros nuevos ocupa bastante más
   que cuando se midió.
7. **Valoración a la carta.** Un `POST /api/valoracion` que estime una vivienda que
   no está en la base, a partir de las características que mande el cliente. El
   cargador ya lo soporta; falta el endpoint y su modelo de petición.
8. **Cachear la agregación del mapa.** La vista de toda España sin filtros tarda ~1,1 s:
   el `GROUP BY` sobre 150k filas necesita un árbol temporal. Una tabla de celdas
   precalculadas por nivel de zoom lo dejaría en milisegundos.
9. **Acelerar `/stats` con área dibujada.** Los percentiles se calculan con una
   consulta por rango, y cada una vuelve a evaluar el polígono fila a fila: 4,2 s la
   primera vez sobre un área del tamaño de Madrid. Se cachea después, pero la
   primera espera se nota.
10. Fase 4 completa, cuando lleguen las credenciales de Idealista.

---

## Bitácora

| Fecha | Qué se hizo |
| --- | --- |
| 2026-08-24 | Docker verificado por primera vez de punta a punta, con tres arreglos que hacían falta para que funcionara: la base a un volumen (en montaje de Windows va 217× más lento y la API no responde), fuera `--reload` y los montajes de código (190 % de CPU en reposo por sondeo de ficheros) y healthchecks recalibrados (3 s de límite para una sonda de 4,5 s; `localhost` resolviendo a IPv6 donde Vite solo escucha IPv4). Del mapa: Madrid ya no sale partido —a zoom bajo se agrupa por zona, no por rejilla— y clicar un clúster enseña solo los suyos. Y la caché pasa a single-flight tras ver 24 hilos recalculando el mismo agregado y tumbar el servidor. |
| 2026-08-24 | Filtros nuevos (extras, baños, planta, año, estado, distancia al centro y al metro) promoviendo al esquema normalizado los atributos que cualquier portal reporta, con migración v4 y recarga al 100 % de cobertura; y dos estadísticas nuevas: la curva de €/m² por distancia al centro —que avisa si hay varias ciudades mezcladas— y el impacto de cada extra, con su aviso de que son correlaciones. Encontrado y corregido al probarlo un closure obsoleto que hacía que marcar dos extras seguidos dejara solo el segundo. |
| 2026-08-24 | Capa de calor por €/m² con conmutador Marcadores/Calor en el mapa (coropleto de celdas en canvas, tramos por cuantiles, respeta los filtros), favoritos en `localStorage` con corazón en tarjeta y detalle, pestaña propia y filtro `ids` para pedirlos en una sola petición, y gráfico de €/m² por habitaciones y por superficie en el panel lateral, que enseña la U que encontró el notebook. Verificado todo en navegador: 0 errores de consola. |
| 2026-08-24 | Rendimiento: índices compuestos y cubridores (hasta 51× en los filtros del panel), caché de agregados con invalidación por versión de datos (23× en la segunda petición a `/stats`), la consulta paginada con bbox de 178 ms a 7 ms en vista de ciudad, payload del mapa un 15 % menor por redondeo, migración de esquema por `PRAGMA user_version` y debounce movido al origen (tres arrastres encadenados: 9 peticiones → 3). El **R-Tree se implementó, se midió y se descartó**: 8× más lento que el índice cubridor actual. También se sacó `geometry` de `app.storage`, que cerraba un import circular latente. |
| 2026-08-24 | Chollos: `expected_price` y `price_deviation` en la base, `scripts/score_listings` (149.923 estimados en 12 s, 6.227 chollos al −25 %), filtros `solo_chollos` / `desviacion_max` / `solo_estimados`, orden por desviación, y en la web interruptor, distintivo en las tarjetas y pines resaltados. La decisión de calcular en la ingesta y no al servir se tomó midiendo: con las 5 variables que guarda la tabla el MAPE sube del 14,1 % al 23,6 % y la marca sería ruido. |
| 2026-08-24 | Notebook de análisis (`notebooks/analisis.ipynb`): EDA de los 149.922 anuncios y modelo de precio con gradient boosting — MAE 47.943 €, MAPE 14,1 %, R²(log) 0,935, validación cruzada 0,932 ± 0,001. Exportado a `backend/models/` con el k-means de zonas y el contrato de columnas, más `app/pricing/` y 10 tests para que el backend pueda usarlo. Al contrastar el texto con las salidas reales aparecieron seis afirmaciones mías equivocadas (Barcelona contra Madrid, la correlación de Spearman, el €/m² por habitaciones, el sótano, el error por ciudad y el rango entre zonas); corregidas y el notebook reejecutado. |
| 2026-08-24 | Mapa rehecho: eliminado el tope de 1.000 anuncios con `/listings/map` (puntos o celdas agregadas en SQL, `total` exacto), dibujo de zona a mano alzada filtrando dentro de SQL, cuatro capas base, iconos SVG por tipo de vivienda —que de paso arreglan el icono roto al máximo zoom—, caja de detalle fija abajo a la derecha y ciudad que vuela el mapa. Dos índices nuevos: `idx_listings_map` (agregación del mapa, 1734→1099 ms) e `idx_listings_zone_geo` (facetas, 948→90 ms; bajo carga concurrente llegaba a 20 s). Caja de zona robusta al anuncio de «Madrid» que está en Almería. |
| 2026-08-24 | Verificado `.\start.ps1` de punta a punta y la interfaz renderizada en un navegador (Edge headless por CDP): vista de mapa con 17 clústeres y 24 teselas, vista de lista con 24 tarjetas y paginador "1–24 de 149.923", filtro por Barcelona reduciendo a 46.728 y arrastrando estadísticas e histograma, cero errores de consola y ninguna imagen rota. |
| 2026-08-24 | Puesta en marcha con un comando: siembra automática de la base al arrancar (`scripts/ensure_data.py`), `docker-compose.yml` con healthchecks en ambos servicios e `init: true`, y `Dockerfile` del backend instalando desde `pyproject.toml` en vez de duplicar la lista de dependencias. Verificado `./start.sh` de punta a punta (web 200, API 200, docs 200, proxy y filtros correctos, puertos liberados al cerrar). La ruta de Docker queda **sin probar**: no hay Docker en esta máquina. |
| 2026-08-24 | Auditoría completa del código. Se crea este archivo. Guarda de idempotencia en `cleanup()` de `start.sh`, que imprimía "Cerrando" dos veces al cerrar por señal. |
| 2026-08-23 | Fase 3 completa: frontend React con vistas de lista y mapa, clustering, panel de filtros y panel de estadísticas. Cerrados los huecos de la Fase 2 (`/listings/facets`, 43 tests de API). Lanzadores `start.ps1` y `start.sh`. Arreglados dos `.dockerignore` que rompían el build de Docker. |
