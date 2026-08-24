# housing-explorer

Explorador de anuncios de vivienda sobre mapa. El backend normaliza anuncios de
**fuentes intercambiables** en un único esquema `Listing`, los guarda en SQLite y
los sirve por HTTP; el frontend los pinta en un mapa Leaflet.

Estado: **funcional**. Ingesta, API de consulta (filtros, paginación,
estadísticas) y frontend (lista + mapa con clustering, filtros compartidos)
están hechos sobre los 149.923 anuncios de idealista18.

El desglose fase a fase de lo hecho y lo pendiente vive en
[PROGRESS.md](PROGRESS.md), que se actualiza en cada sesión de trabajo.

---

## Arquitectura

```
                 +-----------------------------------------+
                 |  ingestion/                             |
   fuentes ----> |    ListingSource (interfaz abstracta)   |
   externas      |      |-- StaticDatasetSource (idealista18)
                 |      |-- SampleCsvSource      (CSV demo)|
                 |      +-- IdealistaApiSource   (stub)    |
                 +-------------------+---------------------+
                                     | list[Listing]  <- esquema normalizado
                 +-------------------v---------------------+
                 |  storage/   Database + ListingRepository |
                 |             SQLite (data/housing.db)     |
                 +-------------------+---------------------+
                                     |
                 +-------------------v---------------------+
                 |  api/       FastAPI  /api/listings ...   |
                 +-------------------+---------------------+
                                     | JSON
                 +-------------------v---------------------+
                 |  frontend/  React + Vite + Leaflet       |
                 +-----------------------------------------+

   models/  --  esquemas Pydantic compartidos por las tres capas
```

Las capas solo dependen hacia abajo: `api -> storage -> models` e
`ingestion -> models`. Ninguna capa sabe de qué fuente vino un anuncio.

### La interfaz `ListingSource`

Es el punto de extensión del sistema (`backend/app/ingestion/base.py`):

```python
class ListingSource(ABC):
    name: ClassVar[str]

    @abstractmethod
    def fetch_listings(self) -> list[Listing]: ...

    def health_check(self) -> bool: ...
```

Todo lo específico de un proveedor —autenticación, paginación, límites de cuota,
mapeo de campos, conversión de unidades— vive dentro de su implementación de
`fetch_listings()`. Hacia fuera solo salen objetos `Listing`.

**Añadir la API oficial de Idealista** (o cualquier otra fuente) cuesta esto y
nada más:

1. Escribir la clase en `backend/app/ingestion/sources/`, decorada con
   `@register_source`.
2. Importarla en `backend/app/ingestion/sources/__init__.py`.
3. Añadir sus credenciales a `.env`.

Storage, API y frontend no cambian: el registro las descubre por nombre y
`/api/sources` las expone automáticamente. `IdealistaApiSource` ya existe como
esqueleto en `sources/idealista.py` para dejar el hueco marcado.

### El esquema `Listing`

`backend/app/models/listing.py`. Campos normalizados:

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | `str` | identificador dentro de su fuente |
| `source` | `str` | nombre de la `ListingSource` |
| `title` | `str` | |
| `url` | `str` / nulo | enlace al anuncio |
| `operation` | `venta` / `alquiler` | |
| `property_type` | enum | `piso`, `casa`, `estudio`, `atico`, ... |
| `price` | `float` | EUR: total en venta, mensual en alquiler |
| `size_m2` | `float` / nulo | |
| `rooms` | `int` / nulo | |
| `latitude`, `longitude` | `float` / nulo | |
| `address` | `str` / nulo | |
| `zone` | `str` / nulo | barrio o distrito |
| `ingested_at` | `datetime` | UTC, en el momento de la ingesta |

La clave global es el par `(source, id)`, expuesta como `listing.global_id`
(`"sample_csv:1"`). Es lo que usa storage para deduplicar en el upsert.

---

## Estructura

```
housing-explorer/
├── start.ps1                  # arranque de un comando (Windows)
├── start.sh                   # arranque de un comando (macOS / Linux)
├── scripts/
│   └── export_idealista18.R   # .rda -> CSV, base R, cero dependencias
├── backend/
│   ├── scripts/
│   │   └── load_initial_data.py   # carga inicial idempotente
│   ├── app/
│   │   ├── main.py            # factoría FastAPI + CORS + lifespan
│   │   ├── cli.py             # init-db / sources / ingest
│   │   ├── config.py          # Settings (pydantic-settings, lee .env)
│   │   ├── models/            # esquemas Pydantic
│   │   ├── ingestion/
│   │   │   ├── base.py        # ListingSource (ABC)  <- punto de extensión
│   │   │   ├── registry.py    # registro nombre -> clase
│   │   │   ├── pipeline.py    # stream -> upsert por lotes
│   │   │   └── sources/       # implementaciones concretas
│   │   ├── storage/           # SQLite: conexión, esquema, repositorio
│   │   └── api/               # routers y dependencias FastAPI
│   ├── tests/
│   └── pyproject.toml
├── frontend/                  # React + Vite + TypeScript + Leaflet
│   └── src/
│       ├── api/client.ts      # cliente HTTP tipado
│       ├── filters.ts         # estado de filtros -> query string
│       ├── format.ts          # euros, m2, etiquetas
│       ├── placeholder.ts     # portadas SVG deterministas
│       ├── hooks/             # useResource (fetch + abort), useDebounced
│       ├── components/        # FilterPanel, ListView, MapView, ClusterLayer, StatsPanel
│       └── types/listing.ts   # espejo de models/
├── data/                      # datasets locales (ver data/README.md)
├── docker-compose.yml         # opcional
└── .env.example
```

---

## Levantarlo en local

Requisitos: **Python 3.11+** y **Node 18+**. Nada más: la base de datos es un
fichero SQLite y no hay servicios externos.

### Qué se arranca, y en qué orden

Son **dos procesos**, no tres: la base de datos no es un servicio, es el fichero
`data/housing.db`.

1. **La base de datos.** No se arranca: se crea sola. El backend monta el esquema
   al iniciarse y, si está vacía, la siembra (`scripts/ensure_data.py`) con el
   dataset completo si lo encuentra o con los 8 anuncios de demostración si no.
2. **El backend** (FastAPI + uvicorn) en <http://localhost:8000>. Va primero
   porque el frontend no tiene nada que pintar sin él.
3. **El frontend** (Vite) en <http://localhost:5173>. **Esta es la URL que hay que
   abrir en el navegador.** Vite reenvía `/api` al backend, así que el navegador
   ve un solo origen.

Los tres comandos de abajo hacen exactamente eso, en ese orden.

### Un solo comando

```powershell
.\start.ps1        # Windows
```

```bash
./start.sh         # macOS, Linux o Git Bash
```

El script crea el entorno virtual, instala lo que falte, arranca la API y la
web, espera a que ambas respondan y abre el navegador en
<http://localhost:5173>. **Ctrl+C** cierra los dos. La primera vez tarda un par
de minutos instalando; las siguientes, segundos.

Opciones: `-BackendPort` / `-FrontendPort` para cambiar de puerto, `-NoBrowser`
para no abrir el navegador (en `start.sh`, las variables de entorno
`BACKEND_PORT`, `FRONTEND_PORT` y `NO_BROWSER`).

> Si PowerShell se queja de la política de ejecución:
> `powershell -ExecutionPolicy Bypass -File .\start.ps1`

### Docker

```bash
docker compose up --build
```

Un solo comando y no hace falta tener ni Python ni Node instalados. Levanta el
backend (`:8000`) y el frontend (`:5173`). El orden lo resuelve compose: el
frontend declara `depends_on: condition: service_healthy`, así que no arranca
hasta que la API responde.

La primera vez tarda unos minutos: construye las dos imágenes y copia la base al
volumen. Después, `docker compose up -d` levanta todo en segundos.

Para parar: **Ctrl+C**, o `docker compose down` desde otra terminal.

```bash
docker compose down       # para los contenedores, conserva la base
docker compose down -v    # y además borra el volumen, para empezar de cero
docker compose logs -f backend
docker compose exec backend python -m scripts.score_listings
```

### Tres cosas que hubo que corregir para que funcionara de verdad

Están comentadas en `docker-compose.yml`, pero merecen figurar aquí porque
cualquiera que monte esto en Windows se las va a encontrar:

1. **La base de datos no puede vivir en un montaje del host.** Medido con los
   137 MB de `housing.db` dentro del contenedor:

   | | montaje de Windows | volumen de Docker |
   | --- | ---: | ---: |
   | `SELECT COUNT(*)` | 17.377 ms | **80 ms** |
   | contar por zona | 9.532 ms | **164 ms** |

   217× y 58×. SQLite hace lectura aleatoria y el puente de Docker Desktop hacia
   NTFS la penaliza hasta dejar la API inservible: los endpoints se quedaban sin
   responder. Ahora la base está en un volumen (`housing-data`), y `./data` se
   monta en `/seed` **en solo lectura** para los CSV, que sí se leen de forma
   secuencial. El *entrypoint* copia la base del host al volumen la primera vez,
   así conserva las estimaciones ya calculadas.

2. **Nada de `--reload` ni de montar el código.** El vigilante de ficheros no
   puede usar inotify sobre un volumen de Windows, así que cae a sondear: 190 %
   de CPU en el backend y una CPU entera en Vite, sin una sola petición en curso.
   Este `compose` es para **ejecutar** la aplicación; para desarrollar con
   recarga en caliente está `.\start.ps1`, que corre en el host.

3. **Los healthchecks estaban mal calibrados**, y no de forma inocente: marcaban
   los servicios como enfermos mientras respondían perfectamente. El del backend
   tenía 3 s de límite cuando arrancar el intérprete y hacer la petición tarda
   4,2–4,6 s ahí dentro. El del frontend usaba `localhost`, que en el contenedor
   resuelve a `::1` antes que a `127.0.0.1`, y Vite solo escucha en IPv4.

### A mano

Útil para desarrollar, con recarga en caliente en ambos lados. Dos terminales:

```bash
# Terminal 1 — API en http://localhost:8000, docs en /docs
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1        # Linux y macOS: source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload
```

```bash
# Terminal 2 — web en http://localhost:5173
cd frontend
npm install
npm run dev
```

Vite hace de proxy de `/api` hacia `http://localhost:8000`, así que el navegador
ve un único origen y no hay CORS en desarrollo. Con `uv` instalado, el backend
se reduce a `uv sync && uv run uvicorn app.main:app --reload`.

### Datos: el dataset idealista18

La fuente inicial es [idealista18](https://github.com/paezha/idealista18):
189.923 anuncios de venta de Madrid, Barcelona y Valencia (2018),
georreferenciados en EPSG:4326 y con licencia ODbL. Se distribuye como paquete
de R, así que hay que convertirlo **una vez**:

```bash
Rscript scripts/export_idealista18.R    # .rda -> data/idealista18_sale.csv
cd backend
python -m scripts.load_initial_data     # CSV -> SQLite (idempotente)
```

El script de R usa **solo R base** —ni `sf`, ni `arrow`, ni `devtools`— y cachea
las descargas en `data/.idealista18-rda/`. El de carga es idempotente: si la
fuente ya tiene filas no hace nada salvo que pases `--force`. La API nunca
ingesta: `app.main` solo crea el esquema, así que arrancar es instantáneo y no
depende de que el dataset esté presente.

Detalles del dataset, el mapeo campo a campo y las alternativas de exportación
(`rpy2`, `pyreadr`) están en [`data/README.md`](data/README.md).

---

## Comandos

| Comando | Qué hace |
| --- | --- |
| `.\start.ps1` / `./start.sh` | levanta API + web y abre el navegador |
| `docker compose up --build` | lo mismo, en contenedores |
| `Rscript scripts/export_idealista18.R` | exporta el dataset de R a CSV (desde la raíz) |
| `jupyter lab notebooks/analisis.ipynb` | abre el notebook de análisis |
| `python -m scripts.load_initial_data` | carga inicial en SQLite, idempotente |
| `python -m scripts.load_initial_data --force` | recarga aunque ya haya datos |
| `python -m scripts.score_listings` | estima el precio de cada anuncio y marca los chollos |
| `python -m scripts.score_listings --dry-run` | lo calcula y lo enseña, sin escribir |
| `python -m scripts.benchmark` | cronometra las consultas más usadas, en frío y con caché |
| `python -m scripts.benchmark --json a.json --compare b.json` | compara dos tandas |
| `python -m app.cli init-db` | crea el esquema SQLite |
| `python -m app.cli sources` | lista las fuentes registradas |
| `python -m app.cli ingest --source sample_csv` | ingesta desde una fuente |
| `pytest` | tests (desde `backend/`) |
| `ruff check . && ruff format .` | lint y formato |
| `npm run lint` | comprobación de tipos del frontend |

## Endpoints

Documentación interactiva completa en <http://localhost:8000/docs>.

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/api/health` | liveness |
| `GET` | `/api/health/ready` | readiness y número de anuncios |
| `GET` | `/api/listings` | lista paginada y filtrada (ver tabla siguiente) |
| `GET` | `/api/listings/facets` | valores y rangos disponibles, y los límites de cada ciudad |
| `GET` | `/api/listings/map` | puntos o celdas agregadas para el mapa, **sin tope de anuncios** |
| `GET` | `/api/listings/{id}` | un anuncio por `fuente:id` o por `id` a secas |
| `GET` | `/api/listings/{source}/{id}` | un anuncio, con la fuente explícita |
| `GET` | `/api/stats` | agregados: percentiles, media por zona, histograma de precios |
| `GET` | `/api/sources` | fuentes registradas, su salud y cuántos anuncios tienen |

### Filtros

`/api/listings` y `/api/stats` aceptan **exactamente los mismos** parámetros, de
modo que las estadísticas siempre describen el conjunto que se está listando.
Los nombres son los del dominio, en castellano:

| Parámetro | Tipo | Nota |
| --- | --- | --- |
| `precio_min`, `precio_max` | número | euros |
| `m2_min`, `m2_max` | número | excluyen los anuncios sin superficie declarada |
| `habitaciones` | entero | número exacto |
| `habitaciones_min` | entero | mínimo; incompatible con `habitaciones` |
| `tipo_operacion` | `venta` \| `alquiler` | |
| `tipo_inmueble` | `piso`, `casa`, `estudio`, ... | |
| `zona` | texto | ciudad o barrio, sin distinguir mayúsculas |
| `poligono` | `lat,lon;lat,lon;...` | área dibujada a mano; mínimo 3 vértices |
| `solo_chollos` | booleano | solo lo que se pide un 25 % o más por debajo de la estimación |
| `desviacion_max` | número | tope de desviación en %; **negativo = más barato de lo esperado** |
| `solo_estimados` | booleano | solo los anuncios a los que el modelo ha podido poner precio |
| `ids` | repetible | anuncios concretos por `fuente:id`; lo usa la vista de favoritos |
| `banos_min` | entero | baños mínimos |
| `planta_min` / `planta_max` | entero | planta; `planta_min=1` excluye bajos y sótanos |
| `anio_min` | entero | construido a partir de ese año |
| `estado` | texto | `obra_nueva`, `buen_estado` o `a_reformar` |
| `extras` | repetible | `ascensor`, `garaje`, `piscina`… se piden **todos** los indicados |
| `centro_max_km` | número | como mucho a esa distancia del centro |
| `metro_max_km` | número | como mucho a esa distancia de una boca de metro |
| `source` | texto | fuente de datos |
| `lat_min`, `lat_max`, `lon_min`, `lon_max` | número | bounding box del área visible; los cuatro o ninguno |
| `limit`, `offset` | entero | solo en `/listings`; `limit` ≤ 1000 |
| `intervalos` | entero | solo en `/stats`; número de barras del histograma |

Un parámetro desconocido es un **422**, no un filtro ignorado: `precio_minimo=1000`
falla en vez de devolver silenciosamente todo el catálogo.

```bash
# Pisos de 2+ habitaciones entre 200k y 400k en el centro de Madrid
curl "http://localhost:8000/api/listings?precio_min=200000&precio_max=400000&habitaciones_min=2&lat_min=40.40&lat_max=40.45&lon_min=-3.72&lon_max=-3.68"
```

---

## El frontend

Dos vistas sobre **el mismo conjunto filtrado**, intercambiables sin recargar:

- **Mapa** — **sin tope de anuncios**. `/listings/map` decide la resolución según
  cuántos coincidan: pocos llegan como marcadores individuales, muchos llegan
  agrupados en celdas calculadas en SQL, con el recuento dentro del punto y el radio
  proporcional a la raíz del número. En ningún caso se oculta nada: `total` es
  siempre exacto, de modo que el mapa nunca tiene que decir «1.000 de 149.923».
  Un clic en una celda acerca; al acercarse lo bastante aparecen los marcadores.

  Cada marcador lleva el **icono de su tipo de vivienda** —un SVG en línea con color
  y forma propios para piso, casa, estudio, dúplex, ático, habitación, terreno,
  garaje y local— y va hueco si es alquiler. Al hacer clic, el anuncio se abre en una
  **caja fija abajo a la derecha**, no en un globo sobre el propio marcador: un globo
  tapa justo lo que acabas de señalar y desaparece al mover el mapa.

  Cuatro **capas base** intercambiables: Mapa (OSM), Satélite (Esri), Terreno
  (OpenTopoMap) y Claro (CARTO).

  Y un conmutador **Marcadores / Calor** en el propio mapa. La capa de calor
  colorea cada celda por su **precio medio por m²**, calculado en SQL, y respeta
  los mismos filtros. Es un coropleto de celdas y no un difuminado tipo
  `Leaflet.heat` a propósito: una capa de calor pinta *densidad* —suma pesos de
  puntos solapados—, así que un barrio con muchos pisos baratos brillaría más que
  uno con pocos caros, y para un ratio como el €/m² eso responde a «dónde hay
  muchos anuncios» disfrazado de «dónde es caro». Cada celda aquí es un número, y
  su color significa ese número. Los tramos se cortan por **cuantiles**, no a
  intervalos iguales: el €/m² está sesgado y ocho tramos iguales dejarían casi
  todo en el primero. Se dibuja en canvas.
- **Lista** — tarjetas paginadas (24 por página) con portada, precio, €/m², m²,
  habitaciones y zona. El dataset no trae fotos, así que cada anuncio recibe un
  degradado SVG derivado de su id: distinguible al desplazarse e imposible de
  confundir con una foto real.

### Dónde buscar

Dos maneras, como en un portal inmobiliario:

- **Elegir la ciudad** en el panel lateral. Además de filtrar, el mapa vuela hasta
  ella. Los límites de cada ciudad los da `/listings/facets`, así que no hay ninguna
  coordenada escrita a mano; y se calculan como la media ± 3σ y no como el mínimo y
  el máximo, porque uno de los 75.804 anuncios de «Madrid» está en realidad en
  Almería y el extremo abriría el mapa sobre media España.
- **Dibujar el área** con *Dibujar zona* y arrastrando sobre el mapa. El trazo se
  simplifica en el navegador y viaja como `poligono=lat,lon;lat,lon;...`. El filtro
  se resuelve **dentro de SQL**, con una función registrada en la conexión y el
  *bounding box* del polígono por delante para que el índice haga el trabajo grueso.
  Hacerlo en SQL y no en Python después es lo que impide que el `COUNT(*)` se separe
  de las filas devueltas.

El panel lateral es la única fuente de verdad de los filtros: ambas vistas y las
estadísticas leen el mismo objeto y lo convierten en la misma query. La casilla
*«buscar solo en el área visible»* decide si el recuadro del mapa acota también
la lista y las estadísticas; la zona dibujada, en cambio, se aplica siempre a las
dos vistas y sobrevive a mover el mapa.

Las peticiones van con *debounce* (300 ms) y se cancelan con `AbortController`
al superarse, de modo que teclear «250000» en el precio es **una** consulta y no
seis, y el resultado anterior sigue en pantalla mientras llega el siguiente.

## Análisis y modelo de precio

[`notebooks/analisis.ipynb`](notebooks/analisis.ipynb) — análisis exploratorio de
los 149.922 anuncios y un modelo que estima el precio de una vivienda a partir de
sus características y su ubicación. Se ve en GitHub sin ejecutar nada: va
versionado con sus salidas y sus gráficos.

Lo que sale del análisis:

- El precio tiene cola larga (media 354.100 €, mediana 255.000 €), así que el
  modelo trabaja sobre `log(precio)`.
- **Barcelona es la más cara** en las dos escalas, con la misma superficie mediana
  que Madrid; **Valencia** tiene las viviendas más grandes y el metro más barato.
- La relación superficie–precio **deja de ser monótona al mezclar las tres
  ciudades** y se recompone dentro de cada una. La superficie solo dice lo que
  vale una vivienda si ya se sabe dónde está.
- Entre las 120 zonas del mapa hay **6× de diferencia** en €/m².

Y del modelo (`HistGradientBoostingRegressor`, test del 20 %):

| | MAE | MAPE | R² (log) |
| --- | ---: | ---: | ---: |
| Mediana (línea base) | 196.836 € | 64,5 % | 0,00 |
| Ridge sin zona | 112.717 € | 26,0 % | 0,81 |
| **Gradient boosting** | **47.943 €** | **14,1 %** | **0,935** |

Validación cruzada de 5 particiones: R²(log) = 0,932 ± 0,001. El error mediano es
del 10,3 %, y el 78 % de las estimaciones cae dentro de un ±20 % del precio real.

**Dónde está y cuánto mide** explican el 89 % de la importancia: `BARRIO` (0,61 de
caída de R² al permutarlo) y `CONSTRUCTEDAREA` (0,47), contra 0,02 de la siguiente
variable. Los baños pesan más que las habitaciones y el ascensor más que la planta.

### Favoritos

Un corazón en cada tarjeta y en el panel de detalle. Se guardan en
`localStorage` bajo `housing-explorer:favoritos`, y la pestaña **♥ Favoritos**
los lista. Sin cuenta y sin backend: un favorito es un `fuente:id` en el
navegador.

Lo que eso implica, y que la interfaz dice en vez de callar: **no viajan a otro
navegador ni a otro dispositivo**, y borrar los datos del sitio los pierde.

La única concesión del backend es el filtro `ids`, repetible: la vista pide sus
anuncios en **una** petición en vez de una por favorito. El valor viene de
`localStorage`, que el usuario puede editar a mano, así que va como parámetro
ligado y nunca concatenado —hay un test que lo comprueba con una inyección.

### Chollos

Cada anuncio guarda lo que el modelo estima que debería costar (`expected_price`)
y cuánto se aparta el precio pedido (`price_deviation`, en %, **negativo = más
barato de lo esperado**). Un anuncio es «posible chollo» cuando está un **25 % o
más** por debajo.

Ese umbral no es un número redondo elegido a ojo. El error mediano del modelo es
del 10,3 % y su MAPE del 14,1 %, así que dentro de un ±15 % no se distingue una
ganga de una equivocación del modelo; −25 % son unas 2,4 veces el error mediano.
Salen **6.227 de 149.923 anuncios (4,2 %)**, que es una proporción lo bastante
pequeña como para que la marca signifique algo.

```bash
cd backend
python -m scripts.score_listings          # ~12 s sobre los 149.923 anuncios
```

```bash
# los mayores chollos, primero el que más se aparta
curl "http://localhost:8000/api/listings?solo_chollos=true&orden=desviacion&limit=5"
# un umbral propio: al menos un 40 % por debajo, solo en Barcelona
curl "http://localhost:8000/api/listings?desviacion_max=-40&zona=Barcelona"
```

El filtro compone con todo lo demás — mapa, estadísticas, zona dibujada— porque
es un filtro más y no un endpoint aparte. En la web hay un interruptor **★ Solo
chollos** en el panel lateral; las tarjetas marcadas llevan borde y distintivo, y
los pines del mapa un contorno rojo con una estrella.

**Por qué se calcula en la ingesta y no al servir.** El modelo usa 35 variables;
la tabla `listings` guarda 5 de ellas, porque el resto son específicas de
idealista18 y el esquema normalizado no arrastra columnas de un proveedor
concreto. Alimentarlo solo con lo que guarda la tabla cuesta caro:

| entrada | MAE | MAPE | error mediano |
| --- | ---: | ---: | ---: |
| las 35 variables | 47.943 € | 14,1 % | 10,3 % |
| solo lo que guarda la tabla | 78.947 € | 23,6 % | 18,2 % |

Con un error mediano del 18 % casi nada está lo bastante por debajo, y la marca
sería ruido. Así que `score_listings` vuelve al dataset de origen, estima con todo
lo que el proveedor sabía, y escribe el resultado. Un anuncio que no se pueda
puntuar se queda en `NULL`, que es una respuesta legítima: no aparece en ninguna
búsqueda de chollos en vez de colarse con una estimación inventada.

> Un piso un 60 % por debajo de su estimación no suele ser una ganga: suele estar
> en ruinas, ser un bajo sin luz o tener un problema legal. La señal dice «esto es
> raro», no «esto es una oportunidad», y así está redactada en la web.

### Usarlo desde el backend

El notebook exporta a `backend/models/price_model.joblib` un paquete con el
modelo, el k-means que traduce coordenadas a zona, el orden exacto de las columnas
y las métricas. `backend/models/price_model.json` repite los metadatos en texto
plano, para poder leerlos sin abrir el pickle.

```bash
pip install -e "backend[serving]"     # scikit-learn y pandas, fuera del runtime
```

```python
from app.pricing import PriceModel, Property

modelo = PriceModel.load()
modelo.estimate(Property(city="Madrid", latitude=40.4168, longitude=-3.7038,
                         size_m2=90, rooms=3, bathrooms=2, floor=3))
# 410215.0
```

Solo ciudad, coordenadas y superficie son obligatorias: el modelo trata los nulos
de forma nativa, así que una planta desconocida se deja en `None` en vez de
inventarla. Todavía no hay endpoint que lo exponga; ese es el siguiente paso.

> El modelo estima **precios de oferta de 2018**, y las coordenadas y los precios
> del dataset llevan ruido añadido por sus editores. Sirve para estudiar el
> mercado, no para tasar una dirección concreta.

## Datos

`data/` está fuera de git salvo su `README.md` y `sample_listings.csv` (8
anuncios inventados de Madrid, para que el esqueleto arranque con algo en el
mapa). Cómo conseguir datos reales —incluida el alta en la API oficial de
Idealista— está en [`data/README.md`](data/README.md).

Este proyecto no redistribuye datos de terceros. Si usas la API de Idealista, sus
condiciones de uso mandan sobre límites de peticiones, caché y redistribución.

## Siguientes pasos

- Implementar `IdealistaApiSource.fetch_listings()`.
- Unir los polígonos de barrio del dataset para llenar `zone` con el barrio real
  en lugar de la ciudad (requiere un join espacial). Hoy `zona` es la ciudad, así
  que «precio medio por zona» son tres filas.
- Datos de alquiler: idealista18 es solo de venta, así que el filtro de operación
  solo tiene un valor con datos.
- Historial de precios: la tabla actual guarda el último estado, no la serie.
- Agregar los marcadores en el servidor (rejilla o geohash) para pintar el mapa
  completo sin el tope de 1.000 puntos por vista.
- Reflejar los filtros en la URL, para poder compartir una búsqueda.
