FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# uv resolves and installs from pyproject.toml.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# `-r pyproject.toml` installs the declared dependencies without building the
# project itself, so this layer is cached until the dependencies change and the
# list lives in exactly one place. The `app` package is importable from the
# working directory; it does not need to be installed.
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

COPY app ./app
# Los cargadores de datos viajan con la imagen para poder poblar la base
# desde dentro:  docker compose exec backend python -m scripts.load_initial_data
COPY scripts ./scripts
# Y el modelo de precios, para que `scripts.score_listings` funcione dentro del
# contenedor. Son 1,4 MB; dejarlo fuera obligaría a montar un volumen más solo
# para eso, y a que la imagen no fuera autosuficiente.
COPY models ./models
# Y los barrios y puntos de interés, por lo mismo: 386 kB de geografía fija que
# los endpoints /neighbourhoods y /points-of-interest sirven tal cual.
COPY geo ./geo

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
