#!/usr/bin/env Rscript
#
# Exports the idealista18 dataset (Rey-Blanco, Arbues, Lopez & Paez, 2024) to CSV
# so the Python backend never needs R at runtime.
#
#   Rscript scripts/export_idealista18.R [outdir] [city ...]
#
# Defaults: outdir = ./data, cities = Madrid Barcelona Valencia
#
# Deliberately uses BASE R ONLY -- no sf, no arrow, no devtools. The .rda files
# hold sf objects, but an sf object is a data.frame with an extra geometry
# list-column, and the coordinates are already duplicated in the plain
# LONGITUDE / LATITUDE columns (CRS EPSG:4326). Dropping the geometry column
# leaves an ordinary data.frame that write.csv handles.
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

# Columns the Python normaliser relies on. The export fails loudly if a future
# release of the dataset drops one of them.
REQUIRED <- c("ASSETID", "PERIOD", "PRICE", "CONSTRUCTEDAREA", "ROOMNUMBER",
              "LONGITUDE", "LATITUDE")

read_city <- function(city) {
  file <- paste0(city, "_Sale.rda")
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
  df <- env[[name]]

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
message("")
message("Next: python -m scripts.load_initial_data   (from backend/)")
