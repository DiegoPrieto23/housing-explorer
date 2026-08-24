#!/bin/sh
# Prepara la base y cede el control a lo que se le haya pedido ejecutar.
set -e

# Si hay una base ya construida en el host y aquí todavía no, se copia al
# volumen. No es una optimización caprichosa: SQLite consultando sobre el
# montaje de Windows va 217 veces más lento (ver docker-compose.yml), así que la
# base tiene que acabar en el volumen sí o sí. Copiarla es una lectura
# secuencial, que es lo único que ese puente hace a velocidad aceptable, y
# conserva de paso las estimaciones de precio ya calculadas.
#
# Si no la hay, `ensure_data` siembra desde el CSV como siempre.
if [ -n "${SEED_DATABASE:-}" ] && [ -f "$SEED_DATABASE" ] && [ ! -f "${DATABASE_PATH:-/data/housing.db}" ]; then
    echo "Copiando la base del host al volumen; tarda unos minutos la primera vez."
    mkdir -p "$(dirname "${DATABASE_PATH:-/data/housing.db}")"
    # A un temporal y luego mv: si la copia se corta a medias, lo que queda no
    # es media base que el siguiente arranque daría por buena.
    cp "$SEED_DATABASE" "${DATABASE_PATH:-/data/housing.db}.parcial"
    mv "${DATABASE_PATH:-/data/housing.db}.parcial" "${DATABASE_PATH:-/data/housing.db}"
    echo "Base copiada."
fi

# `ensure_data` es un no-op en cuanto hay filas, así que un reinicio cuesta un
# COUNT(*), y nunca tumba el arranque: una web vacía se diagnostica, un
# contenedor que se niega a arrancar no.
python -m scripts.ensure_data

exec "$@"
