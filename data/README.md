# data/

Local datasets and the SQLite database. **Nothing here is versioned** except this
README and `sample_listings.csv`, the tiny fake dataset used by `SampleCsvSource`.

| File | Versioned | What it is |
| --- | --- | --- |
| `sample_listings.csv` | yes | 8 invented adverts, used by `SampleCsvSource` |
| `idealista18_sale.csv` | no | the exported idealista18 dataset (~39 MB), all three cities |
| `idealista18_{madrid,barcelona,valencia}.csv` | no | the same data, one file per city |
| `.idealista18-rda/` | no | download cache for the raw `.rda` files |
| `housing.db` | no | SQLite database, created by the loader |

---

## Primary dataset: idealista18

[idealista18](https://github.com/paezha/idealista18) is an open data product with
2018 real-estate listings for Madrid, Barcelona and Valencia, published by
Rey-Blanco, Arbues, Lopez and Paez
([doi:10.1177/23998083241242844](https://doi.org/10.1177/23998083241242844)).

| | |
| --- | --- |
| Rows | 189,923 (Madrid 94,815 · Barcelona 61,486 · Valencia 33,622) |
| Unique `ASSETID` | 149,923 — a dwelling can appear in several quarters |
| Variables | 42 per listing |
| Periods | `201803`, `201806`, `201809`, `201812` |
| Geometry | `sf` POINT, **EPSG:4326**, plus plain `LONGITUDE` / `LATITUDE` columns |
| Operation | **sale only** — there is no rent data in this dataset |
| Licence | ODbL v1.0 |

Coordinates and prices are anonymised with a small amount of random noise by the
publishers, so it is fine for exploration but not for valuing a specific address.

### Step 1 — export it to CSV

The dataset ships as an R package, so it has to be converted once. The script
uses **base R only** — no `sf`, no `arrow`, no `devtools`:

```bash
Rscript scripts/export_idealista18.R              # all three cities -> ./data
Rscript scripts/export_idealista18.R data Madrid  # just Madrid
```

It downloads the `.rda` files (cached in `data/.idealista18-rda/`), drops the
`sf` geometry list-column, keeps the columns common to all three cities, adds a
`CITY` column, and writes one CSV per city plus a combined
`idealista18_sale.csv` **sorted by `PERIOD` ascending**.

That sort matters: the loader upserts on `ASSETID`, so the last row read wins and
the surviving row is the most recent quarter.

### Step 2 — load it into SQLite

```bash
cd backend
python -m scripts.load_initial_data
```

Runs once and skips if the source already has rows (`--force` reloads). The API
itself never ingests — `app.main` only creates the schema.

A full load takes about **60 s** and leaves **149,923** rows: the 189,923 input
rows collapse onto 149,923 unique `ASSETID`s, and the loader says so. Pass
`--keep-all-periods` to store all 189,923 as separate listings instead.

### Exported columns

`ASSETID`, `PERIOD`, `PRICE`, `UNITPRICE`, `CONSTRUCTEDAREA`, `ROOMNUMBER`,
`BATHNUMBER`, the `HAS*` / `IS*` amenity flags, `CONSTRUCTIONYEAR`, `FLOORCLEAN`,
`FLATLOCATIONID`, the `CAD*` cadastral fields, `BUILTTYPEID_1..3`,
`DISTANCE_TO_CITY_CENTER`, `DISTANCE_TO_METRO`, `LONGITUDE`, `LATITUDE`, `CITY`.

The city-specific distance columns (`DISTANCE_TO_CASTELLANA` in Madrid,
`DISTANCE_TO_DIAGONAL` in Barcelona, `DISTANCE_TO_BLASCO` in Valencia) are kept
in the per-city files and left out of the combined one.

Of these, `StaticDatasetSource` requires `ASSETID`, `PERIOD`, `PRICE`,
`CONSTRUCTEDAREA`, `ROOMNUMBER`, `LATITUDE` and `LONGITUDE`; a missing one is a
hard error, not a silent skip.

### How the columns map onto `Listing`

| `Listing` | Source | Note |
| --- | --- | --- |
| `id` | `ASSETID` | `ASSETID-PERIOD` with `--keep-all-periods` |
| `source` | — | always `idealista18` |
| `title` | synthesised | e.g. *Piso de 2 hab. y 75 m2 en Madrid* — the dataset has no advert text |
| `url` | — | always null, the dataset carries no links |
| `operation` | — | always `venta`; the dataset is sale-only |
| `property_type` | `ISSTUDIO` / `ISDUPLEX` | `estudio` / `duplex`, otherwise `piso` |
| `price` | `PRICE` | EUR, must be > 0 |
| `size_m2` | `CONSTRUCTEDAREA` | null when NA or ≤ 0 |
| `rooms` | `ROOMNUMBER` | null when NA or negative |
| `latitude` / `longitude` | `LATITUDE` / `LONGITUDE` | mandatory, checked against a Spain bounding box |
| `address` | — | always null, not present in the dataset |
| `zone` | `CITY` | the city; neighbourhood needs a spatial join, see below |
| `ingested_at` | — | load time, UTC |

### Rows the loader discards

A row is dropped, counted and reported — never stored half-formed — when it has
no `ASSETID`, no price or a non-positive one, missing coordinates, coordinates
outside Spain, or when it fails Pydantic validation. On the real dataset every
row passes; the checks exist for re-exports and edited files.

### Known gap: neighbourhood names

`Madrid_Polygons` & co. carry `LOCATIONID` / `LOCATIONNAME`, but the `_Sale`
tables have no join key — matching a listing to its neighbourhood needs a
point-in-polygon join, which needs the `sf` R package (or `shapely` on the
Python side). Until then `zone` holds the city name.

---

## Alternative export routes

Considered and rejected in favour of the base-R script:

- **A published CSV.** None exists. The data is distributed only as the R
  package; the paper has no CSV supplement and there is no Zenodo mirror.
- **`rpy2`.** Works, but it needs a working R installation *and* a compiled
  Python↔R bridge at runtime — awkward on Windows, and it makes the backend
  permanently depend on R for what is a one-time conversion.
- **`pyreadr`** (`pip install pyreadr`, no R needed) looks like the obvious
  no-R fallback, but it **does not work on this dataset**. Tested with pyreadr
  0.5.6 on `Valencia_Sale.rda`:

  ```
  LibrdataError: Invalid file, or file has unsupported features
  ```

  The underlying `librdata` cannot parse the `sf` geometry list-column, and
  there is no way to drop that column without first reading the file. If you
  have no R at all, install it (it is a ~90 MB download and the script needs
  nothing beyond base R) rather than looking for a Python shortcut.

---

## Other data sources (no credentials needed)

- **INE** — sale/rent price indices by municipality: <https://www.ine.es/>
- **Ministerio de Vivienda** — quarterly housing statistics:
  <https://www.mivau.gob.es/vivienda/estadisticas>
- **Municipal open data portals**, e.g. <https://datos.madrid.es/>

## Official Idealista API (the planned live source)

1. Request access at <https://developers.idealista.com/access-request>. Approval
   grants a key/secret and a monthly request quota.
2. Put the credentials in `.env` at the repo root:

   ```dotenv
   IDEALISTA_API_KEY=your-key
   IDEALISTA_API_SECRET=your-secret
   ```

3. Implement `IdealistaApiSource.fetch_listings()` in
   `backend/app/ingestion/sources/idealista.py`, then
   `python -m app.cli ingest --source idealista`.

Respect the API terms of use: they govern rate limits, caching and
redistribution. Do not scrape the site as a substitute — it is against their
terms. This project redistributes no third-party data.

## CSV format expected by `SampleCsvSource`

Header row required, UTF-8, comma-separated:

```
id,title,url,operation,property_type,price,size_m2,rooms,latitude,longitude,address,zone
```

`operation` is `venta` or `alquiler`. Empty cells are read as null for every
optional column.
