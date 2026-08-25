#!/usr/bin/env Rscript
#
# Exports the idealista18 dataset (Rey-Blanco, Arbues, Lopez & Paez, 2024) so the
# Python backend never needs R at runtime.
#
#   Rscript scripts/export_idealista18.R [outdir] [city ...]
#
# Defaults: outdir = ./data, cities = Madrid Barcelona Valencia
#
# Three things come out of it:
#
#   data/idealista18_*.csv          the listings, one file per city plus a combined one
#   backend/geo/neighbourhoods.geojson      the LOCATIONID/LOCATIONNAME polygons
#   backend/geo/points_of_interest.geojson  city centre, metro stations, main street
#
# Deliberately uses BASE R ONLY -- no sf, no arrow, no devtools. The .rda files
# hold sf objects, but an sf object is a data.frame with an extra geometry
# list-column. For the listings the coordinates are already duplicated in the
# plain LONGITUDE / LATITUDE columns, so dropping the geometry leaves an
# ordinary data.frame that write.csv handles. For the polygons the geometry *is*
# the point, but an sfg MULTIPOLYGON is a list of polygons, each a list of
# rings, each a two-column matrix -- exactly the nesting GeoJSON asks for. And
# the CRS is already EPSG:4326 in all three files (checked), so there is no
# reprojection to do either.
#
# Data licence: ODbL v1.0. https://github.com/paezha/idealista18

BASE_URL <- "https://github.com/paezha/idealista18/raw/master/data"
ALL_CITIES <- c("Madrid", "Barcelona", "Valencia")

args <- commandArgs(trailingOnly = TRUE)
outdir <- if (length(args) >= 1) args[1] else "data"
cities <- if (length(args) >= 2) args[-1] else ALL_CITIES

unknown <- setdiff(cities, ALL_CITIES)
if (length(unknown)) {
  stop("Unknown city/cities: ", paste(unknown, collapse = ", "),
       ". Valid: ", paste(ALL_CITIES, collapse = ", "))
}

dir.create(outdir, recursive = TRUE, showWarnings = FALSE)
cachedir <- file.path(outdir, ".idealista18-rda")
dir.create(cachedir, recursive = TRUE, showWarnings = FALSE)

#' Download an .rda from the dataset repo once, then read it from the cache.
#'
#' Returns the single object the file contains, whatever it happens to be
#' called: an sf data.frame for the listings and the polygons, a plain list for
#' the points of interest.
read_rda <- function(file) {
  path <- file.path(cachedir, file)

  if (!file.exists(path)) {
    url <- paste(BASE_URL, file, sep = "/")
    message("Downloading ", url)
    ok <- tryCatch({
      download.file(url, path, mode = "wb", quiet = TRUE)
      TRUE
    }, error = function(e) {
      message("  failed: ", conditionMessage(e))
      FALSE
    })
    if (!ok || !file.exists(path) || file.size(path) == 0) {
      unlink(path)
      stop("Could not download ", url)
    }
  } else {
    message("Using cached ", path)
  }

  env <- new.env()
  name <- load(path, envir = env)
  env[[name[1]]]
}


# ==============================================================================
# 1. Listings
# ==============================================================================

# Columns the Python normaliser relies on. The export fails loudly if a future
# release of the dataset drops one of them.
REQUIRED <- c("ASSETID", "PERIOD", "PRICE", "CONSTRUCTEDAREA", "ROOMNUMBER",
              "LONGITUDE", "LATITUDE")

read_city <- function(city) {
  df <- read_rda(paste0(city, "_Sale.rda"))

  # Drop the sf geometry list-column; LONGITUDE / LATITUDE already carry it.
  geom <- attr(df, "sf_column")
  if (!is.null(geom)) df[[geom]] <- NULL
  df <- as.data.frame(df, stringsAsFactors = FALSE)

  missing <- setdiff(REQUIRED, names(df))
  if (length(missing)) {
    stop(city, " is missing expected column(s): ", paste(missing, collapse = ", "))
  }

  df$ASSETID <- as.character(df$ASSETID)
  df$CITY <- city
  message(sprintf("  %-10s %6d rows, %2d columns", city, nrow(df), ncol(df)))
  df
}

frames <- lapply(cities, read_city)
names(frames) <- cities

# Cities carry a few bespoke columns (DISTANCE_TO_CASTELLANA in Madrid,
# DISTANCE_TO_BLASCO in Valencia, ...). Keep the intersection so every row in
# the combined file has the same shape.
common <- Reduce(intersect, lapply(frames, names))
dropped <- setdiff(Reduce(union, lapply(frames, names)), common)
if (length(dropped)) {
  message("City-specific columns left out of the combined file: ",
          paste(sort(dropped), collapse = ", "))
}

for (city in cities) {
  path <- file.path(outdir, sprintf("idealista18_%s.csv", tolower(city)))
  write.csv(frames[[city]][, common, drop = FALSE], path,
            row.names = FALSE, na = "", fileEncoding = "UTF-8")
  message("Wrote ", path)
}

combined <- do.call(rbind, lapply(frames, function(d) d[, common, drop = FALSE]))

# Sort by quarter ascending. The loader upserts on ASSETID, so when the same
# dwelling appears in several quarters the most recent row is the one that
# survives.
combined <- combined[order(combined$PERIOD), , drop = FALSE]

path <- file.path(outdir, "idealista18_sale.csv")
write.csv(combined, path, row.names = FALSE, na = "", fileEncoding = "UTF-8")

message("")
message("Wrote ", path)
message(sprintf("  %d rows, %d unique ASSETIDs, quarters: %s",
                nrow(combined),
                length(unique(combined$ASSETID)),
                paste(sort(unique(combined$PERIOD)), collapse = ", ")))

# Freed before the geometry work: the three cities together are ~1 GB in memory.
rm(frames, combined)
invisible(gc())


# ==============================================================================
# 2. GeoJSON writer (base R)
# ==============================================================================
#
# The polygons and the points of interest come out as GeoJSON, not CSV, because
# a polygon in a CSV is not a polygon -- it is a string somebody has to parse
# again. And they go to `backend/geo/` rather than `data/` because they are a
# different kind of artefact: ~300 kB of fixed geography that never changes,
# versioned with the repo and shipped inside the Docker image, exactly like the
# price model. `data/` is for the heavy datasets that get downloaded.

# Decimal places for coordinates. The fifth is worth 1.1 m of latitude: below
# the error of the data itself, and far below what a map of neighbourhoods can
# resolve. Dropping the three after it saves about a third of the file.
COORD_DIGITS <- 5

#' Get a string into real UTF-8, whatever R thinks it is holding.
#'
#' This is not paranoia, it is a bug that already happened. The names in the
#' .rda are UTF-8 bytes with no encoding declared, and R on Windows defaults to
#' a Latin-1 code page, so enc2utf8() "converted" them a second time: the export
#' wrote a doubly-encoded name instead of the real one. The tell is a c3/c2
#' prefix on every accent, and it survives a round-trip through JSON looking
#' almost right, which is what makes it easy to ship.
#'
#' So: if the bytes are already valid UTF-8, say so and convert nothing. If they
#' are not -- genuine Latin-1, where an accent is one byte that cannot open a
#' UTF-8 sequence -- fall back to converting from the native encoding. ASCII is
#' valid UTF-8 either way, so the common case takes the first branch and is a
#' no-op.
as_utf8 <- function(x) {
  x <- as.character(x)
  if (Encoding(x) == "unknown" && !is.na(validUTF8(x)) && validUTF8(x)) {
    Encoding(x) <- "UTF-8"
    return(x)
  }
  enc2utf8(x)
}

#' A JSON string in pure ASCII.
#'
#' Everything above 127 comes out as \uXXXX. Escaping sidesteps the encoding
#' question for good: the file is ASCII, anything reads it without negotiating,
#' and that is what you want from an artefact that lives in git. It does not
#' rescue you from feeding it the wrong characters in the first place, which is
#' what as_utf8 above is for.
json_string <- function(x) {
  chars <- vapply(utf8ToInt(as_utf8(x)), function(cp) {
    if (cp == 34L) "\\\""
    else if (cp == 92L) "\\\\"
    else if (cp == 10L) "\\n"
    else if (cp == 13L) "\\r"
    else if (cp == 9L) "\\t"
    else if (cp < 32L) sprintf("\\u%04x", cp)
    else if (cp < 127L) intToUtf8(cp)
    else if (cp <= 0xFFFFL) sprintf("\\u%04x", cp)
    else {
      # Outside the basic plane: surrogate pair. Should never turn up in a
      # neighbourhood name, but an emoji in some future dataset should not
      # produce a broken file.
      v <- cp - 0x10000L
      sprintf("\\u%04x\\u%04x", 0xD800L + v %/% 0x400L, 0xDC00L + v %% 0x400L)
    }
  }, character(1))
  paste0("\"", paste(chars, collapse = ""), "\"")
}

num <- function(x) formatC(x, format = "f", digits = COORD_DIGITS, drop0trailing = TRUE)

# [lon, lat] is the GeoJSON order (RFC 7946 section 3.1.1), which is the
# opposite of Leaflet's. The library flips it when it draws; the file follows
# the standard.
positions <- function(lon, lat) paste0("[", num(lon), ",", num(lat), "]")

ring_json <- function(m) paste0("[", paste(positions(m[, 1], m[, 2]), collapse = ","), "]")

multipolygon_json <- function(geometry) {
  polygons <- vapply(geometry, function(poly) {
    paste0("[", paste(vapply(poly, ring_json, character(1)), collapse = ","), "]")
  }, character(1))
  paste0("{\"type\":\"MultiPolygon\",\"coordinates\":[",
         paste(polygons, collapse = ","), "]}")
}

point_json <- function(lon, lat) {
  paste0("{\"type\":\"Point\",\"coordinates\":", positions(lon, lat), "}")
}

linestring_json <- function(lon, lat) {
  paste0("{\"type\":\"LineString\",\"coordinates\":[",
         paste(positions(lon, lat), collapse = ","), "]}")
}

feature_json <- function(geometry, properties) {
  pairs <- vapply(names(properties),
                  function(k) paste0(json_string(k), ":", properties[[k]]),
                  character(1))
  paste0("{\"type\":\"Feature\",\"properties\":{", paste(pairs, collapse = ","),
         "},\"geometry\":", geometry, "}")
}

write_geojson <- function(path, features, label) {
  body <- paste0("{\"type\":\"FeatureCollection\",\"features\":[\n",
                 paste(features, collapse = ",\n"),
                 "\n]}\n")
  # Written as bytes: the string is already pure ASCII, and going through a text
  # connection would let R decide about encodings and line endings. LF
  # everywhere, which is what .gitattributes asks for.
  con <- file(path, open = "wb")
  on.exit(close(con))
  writeBin(charToRaw(body), con)
  message(sprintf("Wrote %s  --  %d features, %.0f kB of %s",
                  path, length(features), nchar(body) / 1024, label))
}


# ==============================================================================
# 3. Neighbourhood polygons
# ==============================================================================

count_vertices <- function(geometry) {
  sum(vapply(geometry, function(g)
    sum(vapply(g, function(poly)
      sum(vapply(poly, nrow, integer(1))), integer(1))), integer(1)))
}

export_neighbourhoods <- function(cities, outfile) {
  features <- character(0)

  for (city in cities) {
    df <- read_rda(paste0(city, "_Polygons.rda"))

    geom_col <- attr(df, "sf_column")
    if (is.null(geom_col)) stop(city, "_Polygons has no geometry column")

    missing <- setdiff(c("LOCATIONID", "LOCATIONNAME"), names(df))
    if (length(missing)) {
      stop(city, "_Polygons is missing column(s): ", paste(missing, collapse = ", "))
    }

    geometry <- df[[geom_col]]

    # Everything below assumes MULTIPOLYGON. Saying so out loud means a future
    # release that switches to plain POLYGON fails here, with a message, rather
    # than writing a file full of malformed coordinates.
    kinds <- unique(vapply(geometry, function(g) class(g)[2], character(1)))
    if (!identical(sort(kinds), "MULTIPOLYGON")) {
      stop(city, "_Polygons has unexpected geometry type(s): ",
           paste(kinds, collapse = ", "))
    }

    message(sprintf("  %-10s %3d neighbourhoods, %5d vertices",
                    city, nrow(df), count_vertices(geometry)))

    features <- c(features, vapply(seq_len(nrow(df)), function(i) {
      feature_json(
        multipolygon_json(geometry[[i]]),
        list(location_id = json_string(df$LOCATIONID[i]),
             name        = json_string(df$LOCATIONNAME[i]),
             city        = json_string(city))
      )
    }, character(1)))
  }

  write_geojson(outfile, features, "neighbourhood polygons")
}


# ==============================================================================
# 4. Points of interest
# ==============================================================================
#
# Three kinds, and each one is drawn differently on the map: the centre is a
# single point, the metro is many points, and the main street is a line. Making
# the street a LineString instead of 155 loose points is the difference between
# seeing the Castellana and seeing a smear of dots.

# What the package calls the main street -> what it is actually called. The only
# hand-written strings in this script; everything else comes from the dataset.
MAIN_STREETS <- list(
  Castellana = "Paseo de la Castellana",
  Diagonal   = "Avinguda Diagonal",
  # Escaped rather than written out: this file stays pure ASCII so that R reads
  # it identically whatever locale it starts in. R resolves \uXXXX in a string
  # literal, and json_string escapes it straight back on the way out.
  Blasco     = "Avinguda de Blasco Ib\u00e1\u00f1ez"
)

# Degrees -> km, good enough for a sanity check at these latitudes.
KM_PER_DEG_LAT <- 110.540
KM_PER_DEG_LON <- 111.320

km_between <- function(lon1, lat1, lon2, lat2, lat0) {
  sqrt(((lon2 - lon1) * KM_PER_DEG_LON * cos(lat0 * pi / 180))^2 +
       ((lat2 - lat1) * KM_PER_DEG_LAT)^2)
}

export_pois <- function(cities, outfile) {
  features <- character(0)

  for (city in cities) {
    pois <- read_rda(paste0(city, "_POIS.rda"))

    for (required in c("City_Center", "Metro")) {
      if (is.null(pois[[required]])) stop(city, "_POIS has no ", required)
    }

    # --- city centre ------------------------------------------------------
    centre <- pois$City_Center
    if (nrow(centre) != 1) stop(city, " has ", nrow(centre), " city centres")

    features <- c(features, feature_json(
      point_json(centre$Lon, centre$Lat),
      list(kind = json_string("centro"),
           city = json_string(city),
           name = json_string(paste("Centro de", city)))
    ))

    # --- metro ------------------------------------------------------------
    metro <- pois$Metro

    # The dataset gives coordinates and nothing else -- no station names. Exact
    # duplicates are most likely separate entrances to one station; two markers
    # on top of each other say nothing, so they collapse into one.
    key <- paste(round(metro$Lon, COORD_DIGITS), round(metro$Lat, COORD_DIGITS))
    duplicates <- sum(duplicated(key))
    metro <- metro[!duplicated(key), , drop = FALSE]

    # And a sanity filter, because at least one point is wrong: Valencia has a
    # "station" at lon +0.4026 when the whole network sits at negative
    # longitudes. The flipped sign puts it 67 km out, in the Mediterranean. It
    # is dropped rather than corrected: negating it would give a plausible
    # location, but that would be a guess of mine, not a datum. Madrid's real
    # network reaches 25 km (Arganda), so the threshold has room to spare.
    km <- km_between(centre$Lon, centre$Lat, metro$Lon, metro$Lat, centre$Lat)
    far <- km > 40
    if (any(far)) {
      message(sprintf("  %-10s dropped %d metro station(s) more than 40 km out (furthest %.0f km)",
                      city, sum(far), max(km)))
    }
    metro <- metro[!far, , drop = FALSE]

    features <- c(features, vapply(seq_len(nrow(metro)), function(i) {
      feature_json(point_json(metro$Lon[i], metro$Lat[i]),
                   list(kind = json_string("metro"), city = json_string(city)))
    }, character(1)))

    # --- main street ------------------------------------------------------
    street_key <- setdiff(names(pois), c("City_Center", "Metro"))
    if (length(street_key) != 1) {
      stop(city, "_POIS: expected exactly one main street, got ",
           paste(street_key, collapse = ", "))
    }
    street_name <- MAIN_STREETS[[street_key]]
    if (is.null(street_name)) {
      stop("No display name known for main street ", street_key, " (", city, ")")
    }
    street <- pois[[street_key]]

    # The points come in almost the right order, but only almost: followed as
    # given, the Diagonal measures 6,995 m, and sorted it measures 6,720 -- 275 m
    # of zigzag. All three streets are straight, so ordering by the dominant
    # direction (first principal component) straightens them without inventing
    # anything.
    lat0 <- mean(street$Lat)
    x <- (street$Lon - mean(street$Lon)) * cos(lat0 * pi / 180)
    y <- street$Lat - lat0
    street <- street[order(prcomp(cbind(x, y))$x[, 1]), , drop = FALSE]

    # The check that makes the line above honest: sorting along a straight line
    # is only valid if the street *is* a straight line. If the path walked is
    # much longer than the distance between its ends, it is not, and this script
    # should say so instead of pretending.
    last <- nrow(street)
    walked <- sum(km_between(street$Lon[-last], street$Lat[-last],
                             street$Lon[-1], street$Lat[-1], lat0))
    ends <- km_between(street$Lon[1], street$Lat[1],
                       street$Lon[last], street$Lat[last], lat0)
    if (walked > 1.15 * ends) {
      warning(sprintf("%s: %s does not look straight (path %.1f km vs ends %.1f km); the principal-component ordering may be wrong",
                      city, street_name, walked, ends))
    }

    message(sprintf("  %-10s %-24s %3d points, %.1f km  |  %3d metro stations (%d duplicates dropped)",
                    city, street_name, last, ends, nrow(metro), duplicates))

    features <- c(features, feature_json(
      linestring_json(street$Lon, street$Lat),
      list(kind = json_string("calle"),
           city = json_string(city),
           name = json_string(street_name))
    ))
  }

  write_geojson(outfile, features, "points of interest")
}


# ==============================================================================
# 5. Run it
# ==============================================================================

# Where the GeoJSON lands: <repo>/backend/geo, next to the price model. Derived
# from the script's own path so it follows the repo rather than the directory it
# was launched from. GEO_OUTDIR overrides it.
script_path <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))
geodir <- Sys.getenv("GEO_OUTDIR", unset = NA)
if (is.na(geodir)) {
  geodir <- if (length(script_path)) {
    file.path(dirname(dirname(normalizePath(script_path[1]))), "backend", "geo")
  } else {
    file.path(outdir, "geo")
  }
}
dir.create(geodir, recursive = TRUE, showWarnings = FALSE)

message("")
message("Neighbourhoods and points of interest -> ", geodir)
export_neighbourhoods(cities, file.path(geodir, "neighbourhoods.geojson"))
export_pois(cities, file.path(geodir, "points_of_interest.geojson"))

message("")
message("Next: python -m scripts.load_initial_data   (from backend/)")
