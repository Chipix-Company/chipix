param(
    [string]$BackendRoot = "",
    [string]$PythonExe = "",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($BackendRoot)) {
    $BackendRoot = (Resolve-Path (Join-Path $scriptDir "..\..")).Path
}
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = if ($env:CHIPVERIFY_PYTHON_BIN) { $env:CHIPVERIFY_PYTHON_BIN } else { "python" }
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $scriptDir "build"
}

function Resolve-ExecutablePath([string]$Command) {
    if ([string]::IsNullOrWhiteSpace($Command)) {
        return $null
    }

    if (Test-Path $Command) {
        return (Resolve-Path $Command).Path
    }

    $cmd = Get-Command $Command -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    return $null
}

$resolvedPython = Resolve-ExecutablePath $PythonExe
if (-not $resolvedPython) {
    throw "Python not found: '$PythonExe'. Set -PythonExe or CHIPVERIFY_PYTHON_BIN."
}

$serviceEntrypoint = Join-Path $BackendRoot "runtime\windows\backend_service_entrypoint.py"
if (-not (Test-Path $serviceEntrypoint)) {
    throw "Backend service entrypoint not found at '$serviceEntrypoint'"
}
$legacyCoreRoot = Join-Path $BackendRoot "original_core"
if (-not (Test-Path $legacyCoreRoot)) {
    throw "Legacy core import root not found at '$legacyCoreRoot'"
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$distPath = Join-Path $OutputRoot "dist"
$workPath = Join-Path $OutputRoot "work"

Write-Host "Installing/ensuring PyInstaller is available..."
& $resolvedPython -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install/upgrade pyinstaller."
}

$pyinstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name",
    "chipverify-backend",
    "--distpath",
    $distPath,
    "--workpath",
    $workPath,
    "--specpath",
    $OutputRoot,
    "--paths",
    $BackendRoot,
    "--paths",
    $legacyCoreRoot,
    "--collect-all",
    "pydantic_core",
    "--collect-all",
    "passlib",
    "--collect-all",
    "jose",
    "--collect-all",
    "pyverilog",
    "--collect-submodules",
    "original_core",
    "--collect-submodules",
    "core",
    "--collect-submodules",
    "agents",
    "--collect-submodules",
    "parsers",
    "--collect-submodules",
    "routes",
    "--collect-submodules",
    "database",
    "--collect-submodules",
    "services",
    "--collect-submodules",
    "observability",
    "--collect-submodules",
    "sentry_sdk",
    "--collect-submodules",
    "agent_tools",
    "--collect-submodules",
    "graphify",
    "--collect-submodules",
    "simulator_plugins",
    "--hidden-import",
    "simulator_plugins.cadence_integration_fixture",
    "--hidden-import",
    "simulator_plugins.xcelium_runner",
    "--hidden-import",
    "main",
    "--hidden-import",
    "config",
    "--hidden-import",
    "uvicorn.logging",
    "--hidden-import",
    "uvicorn.lifespan.on",
    "--hidden-import",
    "uvicorn.loops.auto",
    "--hidden-import",
    "uvicorn.protocols.http.auto",
    "--hidden-import",
    "uvicorn.protocols.websockets.auto",
    $serviceEntrypoint
)

Push-Location $BackendRoot
try {
    Write-Host "Building backend EXE bundle..."
    & $resolvedPython -m PyInstaller @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed (exit_code=$LASTEXITCODE)."
    }
} finally {
    Pop-Location
}

$exePath = Join-Path $distPath "chipverify-backend\chipverify-backend.exe"
if (-not (Test-Path $exePath)) {
    throw "Build finished but EXE not found at '$exePath'."
}

& $exePath --self-test-packaged
if ($LASTEXITCODE -ne 0) {
    throw "Backend packaged self-test failed (exit_code=$LASTEXITCODE)."
}

Write-Host "BUILT_EXE=$exePath"
Write-Host "Run with required env vars (CHIPVERIFY_SECRET_KEY, CHIPVERIFY_BACKEND_PORT, CHIPVERIFY_LLM_BASE_URL, etc.) before starting."
