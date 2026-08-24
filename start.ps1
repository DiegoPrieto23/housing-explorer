<#
.SYNOPSIS
    Levanta Housing Explorer entero —API y web— con un solo comando.

.DESCRIPTION
    Prepara lo que falte (entorno virtual, dependencias de Python y de Node),
    arranca los dos procesos, espera a que respondan y abre el navegador.
    Ctrl+C cierra ambos.

    Es idempotente: la segunda vez no reinstala nada y arranca en segundos.

.EXAMPLE
    .\start.ps1

.EXAMPLE
    .\start.ps1 -BackendPort 8010 -FrontendPort 5180 -NoBrowser
#>

#Requires -Version 5.1
[CmdletBinding()]
param(
    [int] $BackendPort = 8000,
    [int] $FrontendPort = 5173,
    # No abrir el navegador al terminar de arrancar.
    [switch] $NoBrowser,
    # Saltarse las comprobaciones de dependencias, cuando ya sabes que están.
    [switch] $SkipInstall
)

$ErrorActionPreference = 'Stop'

$Root = $PSScriptRoot
$BackendDir = Join-Path $Root 'backend'
$FrontendDir = Join-Path $Root 'frontend'
$VenvDir = Join-Path $BackendDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$SitePackages = Join-Path $VenvDir 'Lib\site-packages'
$ViteEntry = Join-Path $FrontendDir 'node_modules\vite\bin\vite.js'

# Procesos que hay que cerrar pase lo que pase.
$script:Started = @()

# -- salida ------------------------------------------------------------------

function Write-Step([string] $Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Info([string] $Message) { Write-Host "    $Message" -ForegroundColor DarkGray }
function Write-Warn([string] $Message) { Write-Host "!!  $Message" -ForegroundColor Yellow }
function Write-Fail([string] $Message) { Write-Host "!!  $Message" -ForegroundColor Red }

# -- utilidades --------------------------------------------------------------

function Find-Command([string[]] $Names) {
    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { return $command }
    }
    return $null
}

<#
    Cierra el proceso y todo lo que haya lanzado.

    Stop-Process solo mata el proceso pedido: si ha lanzado hijos, esos se
    quedan vivos reteniendo el puerto y el siguiente arranque falla con
    "address already in use". taskkill /T sí baja el árbol entero.
#>
function Stop-Tree($Process) {
    if ($null -eq $Process) { return }
    try {
        if ($Process.HasExited) { return }
        & taskkill.exe /PID $Process.Id /T /F | Out-Null
    } catch {
        # Ya estaba muerto: no hay nada que arreglar.
    }
}

function Test-PortFree([int] $Port) {
    try {
        $inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return ($null -eq $inUse)
    } catch {
        # Get-NetTCPConnection no está en todas las ediciones de Windows. Si no
        # se puede comprobar, seguimos y que falle el bind con su propio error.
        return $true
    }
}

function Test-PythonPackages {
    # Se mira el site-packages en vez de lanzar `python -c "import ..."`: en
    # PowerShell 5.1, capturar la salida de error de un ejecutable nativo la
    # convierte en excepción, y aquí un fallo de import es una respuesta
    # legítima ("hay que instalar"), no un error.
    foreach ($package in @('fastapi', 'uvicorn', 'pydantic_settings')) {
        if (-not (Test-Path (Join-Path $SitePackages $package))) { return $false }
    }
    return $true
}

function Wait-ForUrl([string] $Url, [int] $TimeoutSeconds, $Process, [string] $What) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            $Process.WaitForExit(1000) | Out-Null
            throw "$What ha terminado antes de responder. El error está justo encima."
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) { return $response }
        } catch {
            # Todavía no escucha: es lo normal durante el arranque.
        }
        Start-Sleep -Milliseconds 400
    }
    throw "$What no respondió en $TimeoutSeconds s ($Url)."
}

# -- preparación -------------------------------------------------------------

function Initialize-Backend {
    if (-not (Test-Path $VenvPython)) {
        Write-Step 'Creando el entorno virtual de Python'
        $python = Find-Command @('python', 'python3', 'py')
        if ($null -eq $python) {
            throw 'No encuentro Python. Instala Python 3.11 o superior desde https://www.python.org/downloads/ y marca "Add python.exe to PATH".'
        }

        # El lanzador `py` necesita que se le diga la versión; los ejecutables
        # `python` / `python3` no aceptan ese argumento.
        if ($python.Name -eq 'py.exe' -or $python.Name -eq 'py') {
            & $python.Source -3 -m venv $VenvDir
        } else {
            & $python.Source -m venv $VenvDir
        }

        if (-not (Test-Path $VenvPython)) {
            throw "No se pudo crear el entorno virtual en $VenvDir."
        }
    }

    if ($SkipInstall -or (Test-PythonPackages)) { return }

    Write-Step 'Instalando las dependencias del backend (solo la primera vez)'
    & $VenvPython -m pip install --quiet --upgrade pip
    & $VenvPython -m pip install --quiet -e $BackendDir
    if (-not (Test-PythonPackages)) {
        throw 'Falló la instalación de las dependencias de Python; mira el error de pip más arriba.'
    }
}

function Initialize-Frontend {
    if (Test-Path $ViteEntry) { return }
    if ($SkipInstall) { throw "Falta node_modules en $FrontendDir. Ejecuta el script sin -SkipInstall." }

    Write-Step 'Instalando las dependencias del frontend (solo la primera vez, tarda un par de minutos)'
    $npm = Find-Command @('npm.cmd', 'npm')
    if ($null -eq $npm) {
        throw 'No encuentro npm. Instala Node 18 o superior desde https://nodejs.org/.'
    }

    Push-Location $FrontendDir
    try {
        & $npm.Source install
    } finally {
        Pop-Location
    }

    if (-not (Test-Path $ViteEntry)) { throw 'Falló `npm install`; mira el error más arriba.' }
}

# -- arranque ----------------------------------------------------------------

function Start-Backend {
    Write-Step "Arrancando la API en el puerto $BackendPort"
    # Sin --reload: un proceso único se cierra limpio al salir. Para desarrollar
    # con recarga en caliente, arranca uvicorn a mano (ver README).
    $arguments = @(
        '-m', 'uvicorn', 'app.main:app',
        '--host', '127.0.0.1', '--port', "$BackendPort",
        '--log-level', 'warning'
    )
    $process = Start-Process -FilePath $VenvPython -ArgumentList $arguments `
        -WorkingDirectory $BackendDir -NoNewWindow -PassThru
    $script:Started += $process
    return $process
}

function Start-Frontend([string] $NodeExe) {
    Write-Step "Arrancando la web en el puerto $FrontendPort"

    # Start-Process -Environment no existe en PowerShell 5.1, pero el proceso
    # hijo hereda el entorno de este, así que basta con exportarlo aquí. Lo lee
    # vite.config.ts para saber a dónde mandar el proxy de /api.
    $env:VITE_API_TARGET = "http://127.0.0.1:$BackendPort"

    # Se invoca vite con node directamente, y no `npm run dev`, para no dejar de
    # por medio el .cmd de npm, que añade un nivel más al árbol de procesos.
    # La ruta va entrecomillada a mano: Start-Process pega los argumentos en una
    # sola línea de comando sin escaparlos, así que un directorio con espacios
    # ("C:\Users\Diego Prieto\...") llegaría a node partido en dos.
    $arguments = @("`"$ViteEntry`"", '--port', "$FrontendPort", '--strictPort')
    $process = Start-Process -FilePath $NodeExe -ArgumentList $arguments `
        -WorkingDirectory $FrontendDir -NoNewWindow -PassThru
    $script:Started += $process
    return $process
}

# -- programa ----------------------------------------------------------------

try {
    Write-Host ''
    Write-Host '  Housing Explorer' -ForegroundColor White
    Write-Host ''

    $node = Find-Command @('node')
    if ($null -eq $node) {
        throw 'No encuentro Node. Instala Node 18 o superior desde https://nodejs.org/.'
    }

    foreach ($port in @($BackendPort, $FrontendPort)) {
        if (-not (Test-PortFree $port)) {
            throw "El puerto $port ya está ocupado. Cierra lo que lo esté usando, o pasa otros puertos: .\start.ps1 -BackendPort 8010 -FrontendPort 5180"
        }
    }

    Initialize-Backend
    Initialize-Frontend

    $backend = Start-Backend
    $ready = Wait-ForUrl "http://127.0.0.1:$BackendPort/api/health/ready" 60 $backend 'La API'
    $listings = ($ready.Content | ConvertFrom-Json).listings

    if ($listings -eq 0) {
        Write-Warn 'La base de datos está vacía: la web arrancará sin ningún anuncio.'
        Write-Info 'Para cargar el dataset completo (149.923 anuncios):'
        Write-Info '    Rscript scripts/export_idealista18.R'
        Write-Info '    backend\.venv\Scripts\python -m scripts.load_initial_data'
        Write-Info 'O, para ver algo ya mismo, los 8 anuncios de ejemplo:'
        Write-Info '    backend\.venv\Scripts\python -m app.cli ingest --source sample_csv'
    } else {
        Write-Info ("{0:N0} anuncios en la base de datos." -f $listings)
    }

    $frontend = Start-Frontend $node.Source
    $frontendUrl = "http://localhost:$FrontendPort"
    Wait-ForUrl $frontendUrl 90 $frontend 'La web' | Out-Null

    Write-Host ''
    Write-Host '  Listo.' -ForegroundColor Green
    Write-Host "  Web ........ $frontendUrl" -ForegroundColor White
    Write-Host "  API ........ http://localhost:$BackendPort/api" -ForegroundColor DarkGray
    Write-Host "  Docs ....... http://localhost:$BackendPort/docs" -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  Ctrl+C para parar.' -ForegroundColor DarkGray
    Write-Host ''

    if (-not $NoBrowser) { Start-Process $frontendUrl | Out-Null }

    # Un bucle de espera corto, en vez de Wait-Process, para que Ctrl+C llegue
    # al bloque finally y no deje procesos huérfanos reteniendo los puertos.
    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Milliseconds 400
    }

    if ($backend.HasExited) { Write-Fail "La API se ha parado (código $($backend.ExitCode))." }
    if ($frontend.HasExited) { Write-Fail "La web se ha parado (código $($frontend.ExitCode))." }
} catch {
    Write-Host ''
    Write-Fail $_.Exception.Message
    exit 1
} finally {
    Write-Host ''
    Write-Step 'Cerrando'
    foreach ($process in $script:Started) { Stop-Tree $process }
}
