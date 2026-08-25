#!/usr/bin/env python3
"""Compila la base de datos en un paquete estático que el navegador puede consultar solo.

Por qué existe
--------------
GitHub Pages sirve ficheros y nada más: no hay FastAPI, no hay SQLite, no hay
proceso al que preguntar. Pero el conjunto de datos es **estático** —una foto de
idealista18 en 2018 que no va a cambiar—, así que no hace falta un servidor para
responder a un filtro: hace falta que los 149.923 anuncios estén en el navegador
y que el filtro se resuelva ahí.

Este script es el puente. Lee ``data/housing.db`` —la misma que sirve el backend,
ya con los barrios asignados y el modelo de precio pasado— y escribe en
``frontend/public/data/`` lo que el motor del navegador necesita. El resultado se
versiona en el repositorio: la base de datos no está en git (hacen falta R, el
dataset original y dos scripts de ingesta para reconstruirla), así que el
workflow de Pages no podría regenerarla, y sin el paquete compilado la web no
tendría datos que enseñar.

El formato
----------
Un fichero binario **columnar**, no una lista de objetos JSON. La diferencia no
es estética: los mismos 149.923 anuncios son ~44 MB en JSON y 8,5 MB aquí, y
sobre todo llegan al navegador como ``TypedArray`` sobre los que un filtro es un
bucle sobre memoria contigua en vez de 149.923 accesos a propiedades de objeto.

    magic   'HEXP'            4 bytes
    header  uint32 longitud + JSON UTF-8 de esa longitud
    datos   los bloques de columna, uno detrás de otro

El JSON de cabecera declara cada columna con su tipo, su desplazamiento y su
longitud, de modo que el lector de TypeScript no tiene que repetir a mano una
tabla de offsets que este fichero podría cambiar sin avisar. Los vocabularios
—ciudades, tipos de inmueble, barrios— también viajan ahí, y las columnas
guardan índices contra ellos.

Todo se escribe además comprimido con gzip (`.gz`). No es solo por el tamaño en
git: el navegador lo descomprime él mismo con `DecompressionStream`, así que la
transferencia es pequeña aunque el servidor no sepa comprimir un `.bin`.

Uso
---
    python scripts/build_static_data.py
    python scripts/build_static_data.py --db data/housing.db --out frontend/public/data
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sqlite3
import struct
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# El repositorio, no el directorio del script: los dos caminos por defecto
# —la base de datos y la carpeta de salida— son relativos a la raíz.
ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DB = ROOT / "data" / "housing.db"
DEFAULT_OUT = ROOT / "frontend" / "public" / "data"
DEFAULT_CHECKS = ROOT / "frontend" / "tools" / "checks.json"
GEO_DIR = ROOT / "backend" / "geo"

MAGIC = b"HEXP"

#: Frontera a la que se alinean la cabecera y cada bloque de columna. Ocho bytes
#: valen para cualquier tipo que el formato use hoy o pueda usar mañana (float64
#: incluido), y el desperdicio máximo es de siete bytes por columna.
ALIGNMENT = 8
#: Sube cuando el formato cambie de forma que un lector viejo lo malinterprete.
#: El motor del navegador comprueba este número y falla con un mensaje claro en
#: vez de decodificar basura.
FORMAT_VERSION = 1

#: El orden es el contrato con el frontend: las columnas guardan el índice
#: dentro de estas listas, no la cadena. Añadir al final es compatible; reordenar
#: no lo es, y por eso va junto a FORMAT_VERSION.
OPERATIONS = ["venta", "alquiler"]
PROPERTY_TYPES = [
    "piso", "casa", "estudio", "duplex", "atico",
    "habitacion", "terreno", "garaje", "local", "otro",
]
CONDITIONS = ["obra_nueva", "buen_estado", "a_reformar"]
AMENITIES = [
    "ascensor", "terraza", "garaje", "aire_acondicionado", "piscina",
    "portero", "jardin", "trastero", "armarios",
]
AMENITY_COLUMNS = [
    "has_lift", "has_terrace", "has_parking", "has_air_conditioning", "has_pool",
    "has_doorman", "has_garden", "has_storage", "has_wardrobes",
]

#: Centinelas de nulo, uno por tipo. Un valor imposible dentro del rango de la
#: columna sale más barato que una máscara de bits aparte, y para estas columnas
#: hay valores imposibles de sobra: no existe una planta -128 ni una desviación
#: de -3276,8 %.
NULL_U8 = 0xFF
NULL_U16 = 0xFFFF
NULL_I8 = -128
NULL_I16 = -32768

#: Grados por unidad de las columnas de coordenadas. 1e-6 grados son ~11 cm, muy
#: por debajo de lo que un anuncio geocodificado sabe de sí mismo, y un int32
#: llega de sobra a los ±180 grados que hay que representar.
COORD_SCALE = 1_000_000

#: Las distancias van en centésimas de kilómetro —10 m— dentro de un uint16, que
#: tope a 655 km. La mayor del conjunto es de 416.
DISTANCE_SCALE = 100

#: El precio estimado se guarda en céntimos. La desviación **no se guarda**: es
#: exactamente `100 * (precio - estimado) / estimado`, así que una columna suya
#: sería el mismo dato dos veces y con menos precisión que el original.
#:
#: Los céntimos no son escrupulosidad contable. El umbral de chollo corta en un
#: -25 % exacto, y con la desviación redondeada a una décima de punto —que es lo
#: que cabía en un int16— 41 anuncios de 6.227 cruzaban la línea al recompilar.
#: Con el estimado al céntimo el error de la desviación es de 2e-5 puntos, y ya
#: no hay ninguno lo bastante cerca del corte como para moverse. El estimado más
#: caro del conjunto son 6,6 M€, que en céntimos siguen cabiendo en un uint32.
EXPECTED_SCALE = 100

#: Todos los identificadores del dataset son 'A' seguido de dígitos. Se guarda
#: solo la parte numérica, como ASCII, y la letra la repone el lector: son
#: 149.923 bytes que no hacen falta, y sobre todo deja el bloque en dígitos
#: puros, que es lo que gzip sabe comprimir a la mitad.
ID_PREFIX = "A"

#: Orden alfabético que un lector español reconoce. Mismo criterio —y misma
#: razón— que `_sort_key` en el repositorio: ordenar por punto de código manda
#: "Ávila" detrás de "Zamora" y el selector de barrios parece roto.
def _sort_key(name: str) -> str:
    stripped = unicodedata.normalize("NFKD", name)
    return "".join(c for c in stripped if not unicodedata.combining(c)).casefold()


class Column:
    """Un bloque de columna: sus valores, su tipo y cómo se serializa.

    El tipo se declara una vez aquí y viaja en la cabecera JSON. El lector de
    TypeScript construye el ``TypedArray`` que corresponda leyendo ese nombre,
    de modo que ninguna de las dos partes tiene una tabla de tipos que
    mantener en sincronía con la otra.
    """

    #: Nombre de tipo -> (código de struct, bytes por valor).
    FORMATS = {
        "u8": ("B", 1),
        "i8": ("b", 1),
        "u16": ("H", 2),
        "i16": ("h", 2),
        "u32": ("I", 4),
        "i32": ("i", 4),
    }

    def __init__(self, name: str, kind: str) -> None:
        if kind not in self.FORMATS:
            raise ValueError(f"tipo de columna desconocido: {kind}")
        self.name = name
        self.kind = kind
        self.values: list[int] = []

    def encode(self) -> bytes:
        code, _ = self.FORMATS[self.kind]
        return struct.pack(f"<{len(self.values)}{code}", *self.values)


def _clamp(value: float, low: int, high: int) -> int:
    """Redondea a entero sin salirse del rango que la columna puede guardar.

    Un valor fuera de rango se recorta en vez de desbordar en silencio: un
    desbordamiento convierte un piso caro en uno barato sin que nada lo diga,
    y el aviso que imprime `build` al final cuenta cuántos se han recortado.
    """
    return max(low, min(high, int(round(value))))


def read_neighbourhood_geometry() -> dict[str, dict[str, Any]]:
    """Nombre, ciudad y caja de cada barrio, sacados del propio polígono.

    De la geometría y no de un GROUP BY sobre los anuncios, igual que hace el
    backend: la caja de un barrio es su extensión real, y un barrio sin anuncios
    sigue existiendo, sigue teniendo forma en el mapa y sigue teniendo que
    aparecer en el selector con un cero al lado en vez de desaparecer.
    """
    path = GEO_DIR / "neighbourhoods.geojson"
    if not path.exists():
        raise SystemExit(
            f"No está {path}. Es el fichero que exporta scripts/export_idealista18.R; "
            "sin él no hay barrios que compilar."
        )

    collection = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}

    for feature in collection["features"]:
        properties = feature["properties"]
        lats: list[float] = []
        lons: list[float] = []

        def walk(node: Any) -> None:
            """Baja por los anidamientos de GeoJSON hasta los pares [lon, lat]."""
            if isinstance(node, (int, float)):
                return
            if len(node) == 2 and all(isinstance(part, (int, float)) for part in node):
                lons.append(float(node[0]))
                lats.append(float(node[1]))
                return
            for child in node:
                walk(child)

        walk(feature["geometry"]["coordinates"])
        result[properties["location_id"]] = {
            "id": properties["location_id"],
            "name": properties["name"],
            "city": properties["city"],
            "lat_min": min(lats),
            "lat_max": max(lats),
            "lon_min": min(lons),
            "lon_max": max(lons),
        }

    return result


def zone_box(
    lat_mean: float, lat_sq: float, lon_mean: float, lon_sq: float,
    lat_min: float, lat_max: float, lon_min: float, lon_max: float,
) -> dict[str, float]:
    """La caja de una ciudad, inmune al anuncio mal archivado.

    Copia deliberada de `ListingRepository._zone_box`, y por el mismo motivo: el
    MIN/MAX puro no sirve para volar el mapa, porque uno de los 75.804 anuncios
    de Madrid está en Almería y la extensión completa abriría el mapa sobre
    media España. Media más/menos tres sigmas, recortado a la extensión real.
    """
    def spread(mean: float, mean_of_squares: float) -> float:
        return math.sqrt(max(mean_of_squares - mean * mean, 0.0))

    lat_sigma = spread(lat_mean, lat_sq)
    lon_sigma = spread(lon_mean, lon_sq)
    margin = 0.005

    return {
        "lat_min": max(lat_mean - 3 * lat_sigma - margin, lat_min),
        "lat_max": min(lat_mean + 3 * lat_sigma + margin, lat_max),
        "lon_min": max(lon_mean - 3 * lon_sigma - margin, lon_min),
        "lon_max": min(lon_mean + 3 * lon_sigma + margin, lon_max),
    }


def build_facets(
    connection: sqlite3.Connection, neighbourhoods: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Las opciones y los topes del panel de filtros, calculados una vez aquí.

    En el backend esto es una consulta cacheada; aquí es un cálculo de compilación,
    porque las facetas no dependen de la selección **por diseño** —los topes de un
    deslizador no deben moverse bajo el cursor mientras se arrastra— y lo que no
    depende de la selección no tiene por qué recalcularse en el navegador.
    """
    counts = {
        row["neighbourhood_id"]: int(row["count"])
        for row in connection.execute(
            "SELECT neighbourhood_id, COUNT(*) AS count FROM listings"
            " WHERE neighbourhood_id IS NOT NULL GROUP BY neighbourhood_id"
        )
    }

    by_city: dict[str, list[dict[str, Any]]] = {}
    for item in neighbourhoods.values():
        by_city.setdefault(item["city"], []).append({**item, "count": counts.get(item["id"], 0)})
    for entries in by_city.values():
        entries.sort(key=lambda entry: _sort_key(entry["name"]))

    zones = []
    for row in connection.execute(
        "SELECT zone, COUNT(*) AS count,"
        " MIN(latitude) AS lat_min, MAX(latitude) AS lat_max,"
        " MIN(longitude) AS lon_min, MAX(longitude) AS lon_max,"
        " AVG(latitude) AS lat_mean, AVG(latitude * latitude) AS lat_sq,"
        " AVG(longitude) AS lon_mean, AVG(longitude * longitude) AS lon_sq"
        " FROM listings WHERE zone IS NOT NULL AND latitude IS NOT NULL"
        " GROUP BY zone ORDER BY COUNT(*) DESC, zone"
    ):
        zones.append({
            "value": row["zone"],
            "count": int(row["count"]),
            **zone_box(
                row["lat_mean"], row["lat_sq"], row["lon_mean"], row["lon_sq"],
                row["lat_min"], row["lat_max"], row["lon_min"], row["lon_max"],
            ),
            "neighbourhoods": by_city.get(row["zone"], []),
        })

    def tally(column: str, *, skip_null: bool = False) -> list[dict[str, Any]]:
        where = f" WHERE {column} IS NOT NULL" if skip_null else ""
        return [
            {"value": row[column], "count": int(row["count"])}
            for row in connection.execute(
                f"SELECT {column}, COUNT(*) AS count FROM listings{where}"
                f" GROUP BY {column} ORDER BY COUNT(*) DESC, {column}"
            )
        ]

    amenity_row = connection.execute(
        "SELECT " + ", ".join(
            f"SUM(CASE WHEN {column} = 1 THEN 1 ELSE 0 END) AS c{index}"
            for index, column in enumerate(AMENITY_COLUMNS)
        ) + " FROM listings"
    ).fetchone()
    amenities = sorted(
        (
            {"value": name, "count": int(amenity_row[f"c{index}"] or 0)}
            for index, name in enumerate(AMENITIES)
            if amenity_row[f"c{index}"]
        ),
        key=lambda item: item["count"],
        reverse=True,
    )

    ranges = connection.execute(
        "SELECT COUNT(*) AS total,"
        " MIN(price) AS price_min, MAX(price) AS price_max,"
        " MIN(size_m2) AS size_min, MAX(size_m2) AS size_max,"
        " MIN(rooms) AS rooms_min, MAX(rooms) AS rooms_max,"
        " MIN(bathrooms) AS bathrooms_min, MAX(bathrooms) AS bathrooms_max,"
        " MIN(floor) AS floor_min, MAX(floor) AS floor_max,"
        " MIN(year_built) AS year_min, MAX(year_built) AS year_max,"
        " MAX(distance_to_center_km) AS center_max,"
        " MAX(distance_to_metro_km) AS metro_max"
        " FROM listings"
    ).fetchone()

    return {
        "total": int(ranges["total"]),
        "zones": zones,
        "operations": tally("operation"),
        "property_types": tally("property_type"),
        "price_min": ranges["price_min"],
        "price_max": ranges["price_max"],
        "size_min": ranges["size_min"],
        "size_max": ranges["size_max"],
        "rooms_min": ranges["rooms_min"],
        "rooms_max": ranges["rooms_max"],
        "amenities": amenities,
        "conditions": tally("condition", skip_null=True),
        "bathrooms_min": ranges["bathrooms_min"],
        "bathrooms_max": ranges["bathrooms_max"],
        "floor_min": ranges["floor_min"],
        "floor_max": ranges["floor_max"],
        "year_min": ranges["year_min"],
        "year_max": ranges["year_max"],
        "center_max_km": ranges["center_max"],
        "metro_max_km": ranges["metro_max"],
    }


def build_payload(connection: sqlite3.Connection) -> tuple[bytes, dict[str, Any]]:
    """Recorre la tabla en el orden «reciente» y llena las columnas.

    El orden de las filas **es** el orden por defecto de la aplicación. En el
    backend `orden=reciente` es ``ORDER BY ingested_at DESC, global_id``; aquí esa
    consulta se ejecuta una vez, al compilar, y el resultado queda congelado en el
    orden del fichero. Así el navegador no necesita guardar 149.923 marcas de
    tiempo para reproducir una ordenación que ya conoce: la posición de la fila es
    la ordenación.
    """
    neighbourhood_geometry = read_neighbourhood_geometry()
    facets = build_facets(connection, neighbourhood_geometry)

    total = int(connection.execute("SELECT COUNT(*) FROM listings").fetchone()[0])

    # Los vocabularios contra los que indexan las columnas. Ciudades y barrios
    # salen ordenados de forma estable para que dos compilaciones de la misma
    # base de datos den ficheros idénticos byte a byte.
    cities = sorted({row["zone"] for row in connection.execute(
        "SELECT DISTINCT zone FROM listings WHERE zone IS NOT NULL"
    )})
    city_index = {name: index for index, name in enumerate(cities)}

    neighbourhood_list = sorted(neighbourhood_geometry.values(), key=lambda item: item["id"])
    neighbourhood_index = {item["id"]: index for index, item in enumerate(neighbourhood_list)}

    operation_index = {name: index for index, name in enumerate(OPERATIONS)}
    property_index = {name: index for index, name in enumerate(PROPERTY_TYPES)}
    condition_index = {name: index for index, name in enumerate(CONDITIONS)}

    columns = {
        name: Column(name, kind)
        for name, kind in [
            ("price", "u32"), ("size", "u16"), ("rooms", "u8"), ("baths", "u8"),
            ("floor", "i8"), ("year", "u16"), ("operation", "u8"), ("ptype", "u8"),
            ("condition", "u8"), ("amenities", "u16"), ("lat", "i32"), ("lon", "i32"),
            ("city", "u8"), ("neighbourhood", "u16"), ("distanceCenter", "u16"),
            ("distanceMetro", "u16"), ("expected", "u32"), ("idLength", "u8"),
        ]
    }
    id_digits: list[bytes] = []

    clamped = 0
    unknown_neighbourhoods: set[str] = set()

    query = (
        "SELECT id, price, size_m2, rooms, bathrooms, floor, year_built,"
        " operation, property_type, condition, latitude, longitude, zone,"
        " neighbourhood_id, distance_to_center_km, distance_to_metro_km,"
        " expected_price, price_deviation, "
        + ", ".join(AMENITY_COLUMNS) +
        " FROM listings ORDER BY ingested_at DESC, global_id"
    )

    for row in connection.execute(query):
        listing_id = row["id"]
        if not listing_id.startswith(ID_PREFIX) or not listing_id[1:].isdigit():
            raise SystemExit(
                f"El identificador {listing_id!r} no es 'A' + dígitos, que es lo que "
                "asume el empaquetado de ids. Hay que cambiar ID_PREFIX y el lector."
            )
        digits = listing_id[len(ID_PREFIX):].encode("ascii")
        if len(digits) > 255:
            raise SystemExit(f"Identificador demasiado largo: {listing_id!r}")
        id_digits.append(digits)
        columns["idLength"].values.append(len(digits))

        price = _clamp(row["price"], 0, 0xFFFFFFFF)
        if price != round(row["price"]):
            clamped += 1
        columns["price"].values.append(price)

        size = row["size_m2"]
        columns["size"].values.append(
            NULL_U16 if size is None else _clamp(size, 0, NULL_U16 - 1)
        )
        rooms = row["rooms"]
        columns["rooms"].values.append(
            NULL_U8 if rooms is None else _clamp(rooms, 0, NULL_U8 - 1)
        )
        baths = row["bathrooms"]
        columns["baths"].values.append(
            NULL_U8 if baths is None else _clamp(baths, 0, NULL_U8 - 1)
        )
        floor = row["floor"]
        columns["floor"].values.append(
            NULL_I8 if floor is None else _clamp(floor, NULL_I8 + 1, 127)
        )
        year = row["year_built"]
        columns["year"].values.append(
            NULL_U16 if year is None else _clamp(year, 0, NULL_U16 - 1)
        )

        columns["operation"].values.append(operation_index[row["operation"]])
        columns["ptype"].values.append(property_index[row["property_type"]])
        columns["condition"].values.append(
            NULL_U8 if row["condition"] is None else condition_index[row["condition"]]
        )

        mask = 0
        for bit, column in enumerate(AMENITY_COLUMNS):
            if row[column]:
                mask |= 1 << bit
        columns["amenities"].values.append(mask)

        # Sin coordenadas no hay anuncio que pintar, y el dataset no tiene
        # ninguno así; si algún día lo hubiera, el centinela es un valor
        # imposible como latitud (200 grados) que el lector traduce a null.
        latitude, longitude = row["latitude"], row["longitude"]
        columns["lat"].values.append(
            200 * COORD_SCALE if latitude is None else _clamp(latitude * COORD_SCALE, -2**31, 2**31 - 1)
        )
        columns["lon"].values.append(
            200 * COORD_SCALE if longitude is None else _clamp(longitude * COORD_SCALE, -2**31, 2**31 - 1)
        )

        zone = row["zone"]
        columns["city"].values.append(NULL_U8 if zone is None else city_index[zone])

        nid = row["neighbourhood_id"]
        if nid is None:
            columns["neighbourhood"].values.append(NULL_U16)
        elif nid in neighbourhood_index:
            columns["neighbourhood"].values.append(neighbourhood_index[nid])
        else:
            # Un barrio asignado en la base de datos que no está en el GeoJSON:
            # se cuenta y se avisa, en vez de reventar la compilación entera.
            unknown_neighbourhoods.add(nid)
            columns["neighbourhood"].values.append(NULL_U16)

        for key, source in (
            ("distanceCenter", "distance_to_center_km"),
            ("distanceMetro", "distance_to_metro_km"),
        ):
            value = row[source]
            if value is None:
                columns[key].values.append(NULL_U16)
            else:
                scaled = _clamp(value * DISTANCE_SCALE, 0, NULL_U16 - 1)
                if scaled == NULL_U16 - 1:
                    clamped += 1
                columns[key].values.append(scaled)

        # Cero es «sin estimar»: un estimado de cero céntimos no existe, así que
        # el centinela no le quita ningún valor legítimo a la columna.
        expected = row["expected_price"]
        columns["expected"].values.append(
            0 if expected is None else _clamp(expected * EXPECTED_SCALE, 1, 0xFFFFFFFF)
        )

    if unknown_neighbourhoods:
        print(
            f"  aviso: {len(unknown_neighbourhoods)} barrios asignados no están en "
            "neighbourhoods.geojson; esos anuncios quedan sin barrio",
            file=sys.stderr,
        )

    # Los bloques se concatenan en el orden en que se declararon, y la cabecera
    # guarda el desplazamiento de cada uno. Nada de esto se escribe a mano dos
    # veces: el lector no tiene una tabla de offsets que mantener al día.
    #
    # Cada bloque empieza en un múltiplo de ALIGNMENT y la cabecera se rellena
    # hasta que el primero también lo esté. No es cosmética: del otro lado un
    # bloque se lee como `new Int32Array(buffer, offset, ...)`, y eso **lanza**
    # si el desplazamiento no es múltiplo del tamaño del elemento. Alinear aquí
    # es lo que permite leer las columnas sin copiarlas.
    blocks: list[bytes] = []
    layout: list[dict[str, Any]] = []
    offset = 0

    def add(name: str, kind: str, encoded: bytes) -> None:
        nonlocal offset
        layout.append({"name": name, "type": kind, "offset": offset, "length": len(encoded)})
        blocks.append(encoded)
        offset += len(encoded)
        padding = -len(encoded) % ALIGNMENT
        if padding:
            blocks.append(b"\0" * padding)
            offset += padding

    for column in columns.values():
        add(column.name, column.kind, column.encode())
    add("idDigits", "u8", b"".join(id_digits))

    header = {
        "version": FORMAT_VERSION,
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "count": total,
        "source": "idealista18",
        "idPrefix": ID_PREFIX,
        "scales": {
            "coord": COORD_SCALE,
            "distance": DISTANCE_SCALE,
            "expected": EXPECTED_SCALE,
        },
        "nulls": {"u8": NULL_U8, "u16": NULL_U16, "i8": NULL_I8, "i16": NULL_I16},
        "vocabularies": {
            "operations": OPERATIONS,
            "propertyTypes": PROPERTY_TYPES,
            "conditions": CONDITIONS,
            "amenities": AMENITIES,
            "cities": cities,
            "neighbourhoods": neighbourhood_list,
        },
        "columns": layout,
        "facets": facets,
    }

    encoded_header = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # Espacios y no ceros: el relleno queda dentro del JSON, que los ignora, y
    # así la longitud declarada sigue siendo la del texto que hay que parsear.
    encoded_header += b" " * (-(len(MAGIC) + 4 + len(encoded_header)) % ALIGNMENT)

    payload = b"".join([
        MAGIC,
        struct.pack("<I", len(encoded_header)),
        encoded_header,
        *blocks,
    ])
    return payload, header


#: Escenarios con los que se comprueba que el motor del navegador responde lo
#: mismo que SQL. Cada uno lleva dos escrituras de la **misma** pregunta: el
#: `WHERE` que la contesta en SQLite y los filtros que la contestan en
#: TypeScript. Que estén escritas por separado es lo que hace que la
#: comprobación valga algo — si el oráculo saliera del mismo código que se está
#: comprobando, no comprobaría nada.
#:
#: Se eligen para tocar las esquinas: los nulos que no cumplen comparaciones, el
#: AND de los extras contra el OR de los barrios, el umbral de chollo, y el corte
#: por barrio que cambia cómo se agrupan las estadísticas.
CHECKS: list[dict[str, Any]] = [
    {
        "name": "todo",
        "sql": "",
        "filters": {},
    },
    {
        "name": "una ciudad",
        "sql": "WHERE zone = 'Madrid' COLLATE NOCASE",
        "filters": {"zone": "Madrid"},
    },
    {
        "name": "precio y superficie",
        "sql": "WHERE price >= 200000 AND price <= 400000 AND size_m2 >= 80",
        "filters": {"priceMin": 200000, "priceMax": 400000, "sizeMin": 80},
    },
    {
        "name": "extras, todos a la vez",
        "sql": "WHERE has_lift = 1 AND has_parking = 1 AND has_pool = 1",
        "filters": {"amenities": ["ascensor", "garaje", "piscina"]},
    },
    {
        "name": "solo chollos",
        "sql": "WHERE price_deviation <= -25",
        "filters": {"deviationMax": -25},
    },
    {
        "name": "planta y año, que tienen nulos",
        "sql": "WHERE floor >= 3 AND year_built >= 1980",
        "filters": {"floorMin": 3, "yearMin": 1980},
    },
    {
        "name": "cerca del metro, en buen estado",
        "sql": "WHERE distance_to_metro_km <= 0.5 AND condition = 'buen_estado'",
        "filters": {"metroMaxKm": 0.5, "condition": "buen_estado"},
    },
    {
        "name": "área visible del mapa",
        "sql": (
            "WHERE latitude BETWEEN 40.40 AND 40.45"
            " AND longitude BETWEEN -3.72 AND -3.68"
        ),
        "filters": {
            "bbox": {"lat_min": 40.40, "lat_max": 40.45, "lon_min": -3.72, "lon_max": -3.68}
        },
    },
]


#: Una vista sobre `listings` con los valores **tal y como quedan en el paquete**.
#:
#: El formato binario es una compresión con pérdida y a propósito: las
#: coordenadas se guardan en millonésimas de grado (~11 cm), las distancias en
#: centésimas de kilómetro (10 m) y el precio estimado en céntimos. Ninguna
#: de esas pérdidas se nota en pantalla, pero todas mueven a algún anuncio al
#: otro lado de un filtro que corte justo por ahí.
#:
#: Redondear igual aquí es lo que hace que la comprobación diga lo que tiene que
#: decir. Sin esto compararía dos cosas a la vez —si el puerto filtra bien y si
#: el códec pierde precisión— y fallaría por lo segundo dejando lo primero sin
#: comprobar. El códec tiene su propio sitio donde justificarse: las constantes
#: de escala de arriba.
_COMPILED_VIEW = f"""
CREATE TEMP VIEW compiled AS SELECT
    price, size_m2, rooms, bathrooms, floor, year_built,
    operation, property_type, condition, zone, neighbourhood_id,
    CAST(ROUND(latitude * {COORD_SCALE}) AS INTEGER) / {COORD_SCALE}.0 AS latitude,
    CAST(ROUND(longitude * {COORD_SCALE}) AS INTEGER) / {COORD_SCALE}.0 AS longitude,
    CAST(ROUND(distance_to_center_km * {DISTANCE_SCALE}) AS INTEGER)
        / {DISTANCE_SCALE}.0 AS distance_to_center_km,
    CAST(ROUND(distance_to_metro_km * {DISTANCE_SCALE}) AS INTEGER)
        / {DISTANCE_SCALE}.0 AS distance_to_metro_km,
    100.0 * (price - CAST(ROUND(expected_price * {EXPECTED_SCALE}) AS INTEGER)
        / {EXPECTED_SCALE}.0)
        / (CAST(ROUND(expected_price * {EXPECTED_SCALE}) AS INTEGER)
        / {EXPECTED_SCALE}.0) AS price_deviation,
    {", ".join(AMENITY_COLUMNS)}
FROM listings
"""


def build_checks(
    connection: sqlite3.Connection, neighbourhoods: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Los números que el motor del navegador tiene que reproducir.

    Se calculan en SQL, que es la implementación de referencia, y `npm run
    verify:static` los vuelve a calcular con el motor de TypeScript y compara.
    """
    connection.execute(_COMPILED_VIEW)
    # Un barrio de verdad, elegido por tamaño para que las medias signifiquen
    # algo. Se añade aquí y no en CHECKS porque el LOCATIONID no se puede
    # escribir a mano: sale del dataset.
    busiest = connection.execute(
        "SELECT neighbourhood_id FROM listings WHERE neighbourhood_id IS NOT NULL"
        " GROUP BY neighbourhood_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    known = {item["id"] for item in neighbourhoods}
    checks = list(CHECKS)
    if busiest and busiest[0] in known:
        checks.append({
            "name": "un barrio",
            "sql": f"WHERE neighbourhood_id = '{busiest[0]}'",
            "filters": {"neighbourhoods": [busiest[0]]},
        })

    results = []
    for check in checks:
        where = check["sql"]
        aggregate = connection.execute(
            "SELECT COUNT(*) AS count, AVG(price) AS avg_price,"
            " MIN(price) AS min_price, MAX(price) AS max_price,"
            " AVG(CASE WHEN size_m2 > 0 THEN price / size_m2 END) AS avg_price_per_m2"
            f" FROM compiled {where}"
        ).fetchone()

        prices = [
            row[0] for row in connection.execute(f"SELECT price FROM compiled {where} ORDER BY price")
        ]

        def at(quantile: float) -> float | None:
            if not prices:
                return None
            index = max(0, min(math.ceil(len(prices) * quantile) - 1, len(prices) - 1))
            return prices[index]

        # Una fila de `by_rooms` y una de `amenity_impact`: son las agregaciones
        # con más sitios donde equivocarse (nulos, divisiones, los dos lados de
        # una comparación) y con el resultado más fácil de contrastar.
        rooms_where = f"{where} AND" if where else "WHERE"
        rooms = [
            {
                "bucket": int(row["bucket"]),
                "count": int(row["count"]),
                "avg_price": round(row["avg_price"]),
                "avg_price_per_m2": round(row["avg_price_per_m2"]),
            }
            for row in connection.execute(
                "SELECT CASE WHEN rooms > 6 THEN 6 ELSE rooms END AS bucket,"
                " COUNT(*) AS count, AVG(price) AS avg_price,"
                " AVG(price / size_m2) AS avg_price_per_m2"
                f" FROM compiled {rooms_where} rooms IS NOT NULL AND size_m2 > 0"
                " GROUP BY bucket ORDER BY bucket"
            )
        ]

        lift = connection.execute(
            "SELECT SUM(CASE WHEN has_lift = 1 THEN 1 ELSE 0 END) AS count,"
            " AVG(CASE WHEN has_lift = 1 THEN price / size_m2 END) AS with_it,"
            " AVG(CASE WHEN has_lift = 0 THEN price / size_m2 END) AS without_it"
            f" FROM compiled {rooms_where} size_m2 > 0"
        ).fetchone()

        results.append({
            "name": check["name"],
            "filters": check["filters"],
            "expected": {
                "count": int(aggregate["count"]),
                "avg_price": aggregate["avg_price"],
                "min_price": aggregate["min_price"],
                "max_price": aggregate["max_price"],
                "avg_price_per_m2": aggregate["avg_price_per_m2"],
                "p25_price": at(0.25),
                "median_price": at(0.50),
                "p75_price": at(0.75),
                "p99_price": at(0.99),
                "by_rooms": rooms,
                "ascensor": (
                    None
                    if lift["with_it"] is None or not lift["without_it"]
                    else {
                        "count": int(lift["count"]),
                        "with_it": round(lift["with_it"]),
                        "without_it": round(lift["without_it"]),
                    }
                ),
            },
        })

    return results


def write(path: Path, body: bytes) -> None:
    """Escribe el fichero comprimido, y solo comprimido.

    `mtime=0` en el gzip para que dos compilaciones de la misma base de datos den
    el mismo fichero byte a byte: sin eso el paquete cambiaría en cada ejecución
    y git registraría 8 MB de diferencia por nada.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(filename="", fileobj=path.open("wb"), mode="wb", compresslevel=9, mtime=0) as out:
        out.write(body)
    print(f"  {path.name}: {len(body) / 1e6:.2f} MB -> {path.stat().st_size / 1e6:.2f} MB comprimido")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Base de datos SQLite de origen")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Carpeta de destino")
    parser.add_argument(
        "--checks",
        type=Path,
        default=DEFAULT_CHECKS,
        help="Dónde dejar las consultas de referencia para `npm run verify:static`",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(
            f"No está {args.db}.\n"
            "Se construye con `docker compose up` (siembra la base de datos) o a mano con\n"
            "  python -m app.cli ingest && python -m backend.scripts.score_listings\n"
            "Ver README_TECHNICAL.md.",
            file=sys.stderr,
        )
        return 1

    print(f"Leyendo {args.db}")
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        payload, header = build_payload(connection)
        checks = build_checks(connection, header["vocabularies"]["neighbourhoods"])
    finally:
        connection.close()

    print(f"Escribiendo en {args.out}")
    write(args.out / "listings.bin.gz", payload)

    # Fuera de `public/`: son la referencia con la que `npm run verify:static`
    # contrasta el motor del navegador, no algo que ningún visitante descargue.
    args.checks.parent.mkdir(parents=True, exist_ok=True)
    args.checks.write_text(
        json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"  {args.checks.name}: {len(checks)} consultas de referencia")

    # La geografía se copia tal cual: son los mismos ficheros que sirve el
    # backend, y el navegador los quiere igual de crudos para `L.geoJSON`.
    for name in ("neighbourhoods.geojson", "points_of_interest.geojson"):
        source = GEO_DIR / name
        if not source.exists():
            print(f"  aviso: falta {source}, la capa correspondiente no cargará", file=sys.stderr)
            continue
        write(args.out / f"{name}.gz", source.read_bytes())

    print(
        f"\n{header['count']:,} anuncios, "
        f"{len(header['vocabularies']['neighbourhoods'])} barrios, "
        f"{len(header['vocabularies']['cities'])} ciudades."
        .replace(",", ".")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
