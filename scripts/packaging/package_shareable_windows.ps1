param(
    [string]$OutputRoot = "dist-shareable/windows"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\..")).Path

function Append-EnvIfMissing([string]$EnvPath, [string]$Key, [string]$Value) {
    if (Test-Path $EnvPath) {
        $existing = Get-Content -Path $EnvPath -ErrorAction SilentlyContinue
        if ($existing | Where-Object { $_ -match "^\s*$([regex]::Escape($Key))\s*=" }) {
            return
        }
    }
    Add-Content -Path $EnvPath -Value "$Key=$Value"
}

Push-Location $repoRoot
try {
    Write-Host "[1/6] Preparing isolated Python packaging environment..." -ForegroundColor Cyan
    $venvRoot = Join-Path $repoRoot ".packaging\pyinstaller-venv"
    $venvPython = Join-Path $venvRoot "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        & python -m venv $venvRoot
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create packaging venv."
        }
    }

    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip in packaging venv."
    }
    & $venvPython -m pip install -r ".\backend\requirements.txt" pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install backend packaging dependencies."
    }

    Write-Host "[2/6] Building backend runtime EXE..." -ForegroundColor Cyan
    $backendBuildRoot = Join-Path $repoRoot "backend\runtime\windows\build"
    $backendRoot = Join-Path $repoRoot "backend"
    & powershell -NoProfile -ExecutionPolicy Bypass -File ".\backend\runtime\windows\build_backend_exe.ps1" -BackendRoot $backendRoot -OutputRoot $backendBuildRoot -PythonExe $venvPython
    if ($LASTEXITCODE -ne 0) {
        throw "Backend EXE build failed."
    }

    Write-Host "[3/6] Staging runtime resources for Electron..." -ForegroundColor Cyan
    $stageRoot = Join-Path $repoRoot "build-resources\runtime"
    if (Test-Path $stageRoot) {
        Remove-Item -Path $stageRoot -Recurse -Force
    }

    $stageBackend = Join-Path $stageRoot "backend"
    $stageBin = Join-Path $stageRoot "bin"
    New-Item -ItemType Directory -Path $stageBackend -Force | Out-Null
    New-Item -ItemType Directory -Path $stageBin -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $stageRoot ".gitkeep") -Force | Out-Null

    $backendDistDir = Join-Path $backendBuildRoot "dist\chipverify-backend"
    if (-not (Test-Path $backendDistDir)) {
        throw "Backend EXE directory missing: $backendDistDir"
    }
    Copy-Item -Path (Join-Path $backendDistDir "*") -Destination $stageBackend -Recurse -Force

    $sourceEnv = Join-Path $repoRoot "backend\.env"
    $stagedEnv = Join-Path $stageBackend ".env"
    if (Test-Path $sourceEnv) {
        Copy-Item -Path $sourceEnv -Destination $stagedEnv -Force
    } else {
        New-Item -ItemType File -Path $stagedEnv -Force | Out-Null
    }

    if ($env:CHIPVERIFY_SECRET_KEY) {
        Append-EnvIfMissing -EnvPath $stagedEnv -Key "CHIPVERIFY_SECRET_KEY" -Value $env:CHIPVERIFY_SECRET_KEY
    } else {
        $generatedSecret = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
        Append-EnvIfMissing -EnvPath $stagedEnv -Key "CHIPVERIFY_SECRET_KEY" -Value $generatedSecret
    }

    Append-EnvIfMissing -EnvPath $stagedEnv -Key "CHIPVERIFY_REQUIRE_ACTIVATION" -Value "true"

    $sourceLicense = Join-Path $repoRoot "backend\license.key"
    if (Test-Path $sourceLicense) {
        Copy-Item -Path $sourceLicense -Destination (Join-Path $stageBackend "license.key") -Force
    }

    $runtimeBin = Join-Path $repoRoot "backend\runtime\bin"
    if (Test-Path $runtimeBin) {
        Copy-Item -Path (Join-Path $runtimeBin "*") -Destination $stageBin -Force
    }

    $manifest = [ordered]@{
        product = "chipverify-desktop"
        created_at = (Get-Date).ToUniversalTime().ToString("o")
        backend_runtime = "runtime/backend/chipverify-backend.exe"
        embedded_env = (Test-Path $stagedEnv)
        activation_required = $true
    } | ConvertTo-Json -Depth 4
    Set-Content -Path (Join-Path $stageRoot "runtime-manifest.json") -Value $manifest -Encoding UTF8

    Write-Host "[4/6] Building frontend..." -ForegroundColor Cyan
    & npm run build:frontend
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed."
    }

    Write-Host "[5/6] Building Windows app folder with embedded runtime..." -ForegroundColor Cyan
    $distElectron = Join-Path $repoRoot "dist-electron"
    if (Test-Path $distElectron) {
        Get-ChildItem -Path $distElectron -Filter "*.exe" -File | Remove-Item -Force
    }
    & npx electron-builder --win dir --x64
    if ($LASTEXITCODE -ne 0) {
        throw "Electron app folder build failed."
    }

    Write-Host "[6/6] Creating shareable ZIP from complete app folder..." -ForegroundColor Cyan
    $version = (Get-Content -Path (Join-Path $repoRoot "package.json") -Raw | ConvertFrom-Json).version
    $shareRoot = Join-Path $repoRoot $OutputRoot
    New-Item -ItemType Directory -Path $shareRoot -Force | Out-Null

    $unpackedDir = Join-Path $repoRoot "dist-electron\win-unpacked"
    if (-not (Test-Path (Join-Path $unpackedDir "ffmpeg.dll"))) {
        throw "Electron app folder is incomplete: ffmpeg.dll is missing."
    }

    $shareableDir = Join-Path $shareRoot "ChipVerify-Shareable-$version-win-x64"
    if (Test-Path $shareableDir) {
        Remove-Item -Path $shareableDir -Recurse -Force
    }
    Copy-Item -Path $unpackedDir -Destination $shareableDir -Recurse -Force

    $zipPath = Join-Path $shareRoot "ChipVerify-Shareable-$version-win-x64.zip"
    if (Test-Path $zipPath) {
        Remove-Item -Path $zipPath -Force
    }
    Compress-Archive -Path $shareableDir -DestinationPath $zipPath -Force

    if (Test-Path $stageRoot) {
        Remove-Item -Path $stageRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $stageRoot ".gitkeep") -Force | Out-Null

    Write-Host "SHAREABLE_ZIP=$zipPath" -ForegroundColor Green
    Write-Host "RUN_EXE=$(Join-Path $shareableDir 'ChipVerify Desktop.exe')" -ForegroundColor Green
    Write-Host "Generate a user Machine Key with: npm run license:machine-key -- <Machine ID>" -ForegroundColor Green
}
finally {
    Pop-Location
}
