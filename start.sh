#!/usr/bin/env bash
# Levanta Housing Explorer entero -API y web- con un solo comando.
#
# Prepara lo que falte (entorno virtual, dependencias de Python y de Node),
# arranca los dos procesos, espera a que respondan y abre el navegador.
# Ctrl+C cierra ambos. Es idempotente: la segunda vez arranca en segundos.
#
#   ./start.sh
#   BACKEND_PORT=8010 FRONTEND_PORT=5180 NO_BROWSER=1 ./start.sh
#
# El equivalente para Windows es start.ps1.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
VENV_DIR="$BACKEND_DIR/.venv"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
NO_BROWSER="${NO_BROWSER:-}"

# Git Bash sobre Windows coloca los ejecutables en Scripts/, no en bin/.
if [ -d "$VENV_DIR/Scripts" ]; then
  VENV_BIN="$VENV_DIR/Scripts"
  VENV_PYTHON="$VENV_BIN/python.exe"
  SITE_PACKAGES="$VENV_DIR/Lib/site-packages"
else
  VENV_BIN="$VENV_DIR/bin"
  VENV_PYTHON="$VENV_BIN/python"
  SITE_PACKAGES="$(echo "$VENV_DIR"/lib/python*/site-packages)"
fi

VITE_ENTRY="$FRONTEND_DIR/node_modules/vite/bin/vite.js"

backend_pid=""
frontend_pid=""

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
info() { printf '\033[90m    %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!!  %s\033[0m\n' "$1"; }
fail() { printf '\033[31m!!  %s\033[0m\n' "$1" >&2; }

cleanup_done=''

cleanup() {
  # El trap esta en TERM y en EXIT, asi que sin esta guarda se ejecuta dos veces
  # al recibir una senal: primero por TERM y luego al salir.
  [ -z "$cleanup_done" ] || return
  cleanup_done=1

  printf '\n'
  step 'Cerrando'
  # El negativo mata el grupo de procesos: vite y uvicorn lanzan hijos que si
  # no se quedan vivos reteniendo el puerto.
  for pid in "$frontend_pid" "$backend_pid"; do
    [ -n "$pid" ] || continue
    kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

require() {
  command -v "$1" >/dev/null 2>&1 || { fail "$2"; exit 1; }
}

# Espera a que una URL responda, abortando si el proceso se muere antes.
wait_for_url() {
  local url="$1" timeout="$2" pid="$3" what="$4"
  local deadline=$(( $(date +%s) + timeout ))

  while [ "$(date +%s)" -lt "$deadline" ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      fail "$what ha terminado antes de responder. El error está justo encima."
      exit 1
    fi
    if curl -fsS -o /dev/null --max-time 3 "$url" 2>/dev/null; then
      return 0
    fi
    sleep 0.4
  done

  fail "$what no respondió en ${timeout}s ($url)."
  exit 1
}

python_packages_present() {
  # Se mira el site-packages en vez de lanzar `python -c "import ..."` porque
  # aquí un fallo de import es una respuesta legítima, no un error que deba
  # tumbar el script bajo `set -e`.
  for package in fastapi uvicorn pydantic_settings; do
    [ -d "$SITE_PACKAGES/$package" ] || return 1
  done
  return 0
}

open_browser() {
  local url="$1"
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 &
  elif command -v open >/dev/null 2>&1; then open "$url" >/dev/null 2>&1 &
  elif command -v start >/dev/null 2>&1; then start "$url" >/dev/null 2>&1 &
  fi
}

# -- preparación -------------------------------------------------------------

printf '\n  \033[1mHousing Explorer\033[0m\n\n'

require curl 'Falta curl.'
require node 'No encuentro Node. Instala Node 18 o superior desde https://nodejs.org/.'

if [ ! -x "$VENV_PYTHON" ]; then
  step 'Creando el entorno virtual de Python'
  if command -v python3 >/dev/null 2>&1; then
    python3 -m venv "$VENV_DIR"
  elif command -v python >/dev/null 2>&1; then
    python -m venv "$VENV_DIR"
  else
    fail 'No encuentro Python. Instala Python 3.11 o superior.'
    exit 1
  fi
  # site-packages solo existe una vez creado el venv.
  [ -d "$VENV_DIR/Scripts" ] || SITE_PACKAGES="$(echo "$VENV_DIR"/lib/python*/site-packages)"
fi

if ! python_packages_present; then
  step 'Instalando las dependencias del backend (solo la primera vez)'
  "$VENV_PYTHON" -m pip install --quiet --upgrade pip
  "$VENV_PYTHON" -m pip install --quiet -e "$BACKEND_DIR"
fi

if [ ! -f "$VITE_ENTRY" ]; then
  step 'Instalando las dependencias del frontend (solo la primera vez, tarda un par de minutos)'
  require npm 'Node está instalado pero npm no aparece en el PATH.'
  (cd "$FRONTEND_DIR" && npm install)
fi

# -- arranque ----------------------------------------------------------------

# Control de trabajos: sin esto, un `( ... ) &` dentro de un script no encabeza
# su propio grupo de procesos, y `kill -- -$pid` no puede bajar el arbol. Es lo
# que hace que Ctrl+C se lleve por delante tambien a los hijos de vite.
set -m

step "Arrancando la API en el puerto $BACKEND_PORT"
# Sin --reload: un proceso único se cierra limpio al salir. setsid/set -m dan un
# grupo propio para poder matar el árbol entero de una vez.
(
  cd "$BACKEND_DIR"
  exec "$VENV_PYTHON" -m uvicorn app.main:app \
    --host 127.0.0.1 --port "$BACKEND_PORT" --log-level warning
) &
backend_pid=$!

wait_for_url "http://127.0.0.1:$BACKEND_PORT/api/health/ready" 60 "$backend_pid" 'La API'

listings="$(curl -fsS "http://127.0.0.1:$BACKEND_PORT/api/health/ready" |
  sed -n 's/.*"listings":[[:space:]]*\([0-9]*\).*/\1/p')"

if [ "${listings:-0}" = "0" ]; then
  warn 'La base de datos está vacía: la web arrancará sin ningún anuncio.'
  info 'Para cargar el dataset completo (149.923 anuncios):'
  info '    Rscript scripts/export_idealista18.R'
  info "    $VENV_BIN/python -m scripts.load_initial_data   # desde backend/"
  info 'O, para ver algo ya mismo, los 8 anuncios de ejemplo:'
  info "    $VENV_BIN/python -m app.cli ingest --source sample_csv"
else
  info "$listings anuncios en la base de datos."
fi

step "Arrancando la web en el puerto $FRONTEND_PORT"
(
  cd "$FRONTEND_DIR"
  # Lo lee vite.config.ts para saber a dónde mandar el proxy de /api.
  export VITE_API_TARGET="http://127.0.0.1:$BACKEND_PORT"
  exec node "$VITE_ENTRY" --port "$FRONTEND_PORT" --strictPort
) &
frontend_pid=$!

FRONTEND_URL="http://localhost:$FRONTEND_PORT"
wait_for_url "$FRONTEND_URL" 90 "$frontend_pid" 'La web'

printf '\n  \033[32mListo.\033[0m\n'
printf '  Web ........ %s\n' "$FRONTEND_URL"
printf '\033[90m  API ........ http://localhost:%s/api\033[0m\n' "$BACKEND_PORT"
printf '\033[90m  Docs ....... http://localhost:%s/docs\033[0m\n' "$BACKEND_PORT"
printf '\n\033[90m  Ctrl+C para parar.\033[0m\n\n'

[ -n "$NO_BROWSER" ] || open_browser "$FRONTEND_URL"

# Un bucle de sondeo, y no `wait`, porque bash solo ejecuta los traps pendientes
# entre comandos: bloqueado dentro de `wait` la senal de Ctrl+C puede quedarse
# sin atender y los procesos hijos sobreviven reteniendo los puertos. Con un
# `sleep` corto el trap entra siempre. Si cualquiera de los dos se cae, el trap
# cierra el otro.
while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done
