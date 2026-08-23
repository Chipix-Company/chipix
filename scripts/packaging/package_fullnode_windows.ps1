param(
    [string]$OutputRoot = "dist-fullnode/windows"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\.." )).Path

Push-Location $repoRoot
try {
    Write-Host "[1/5] Building Windows desktop installer..." -ForegroundColor Cyan
    & npm run package:desktop:installer:win
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop installer build failed."
    }

    Write-Host "[2/5] Building backend EXE runtime bundle..." -ForegroundColor Cyan
    $backendBuildRoot = Join-Path $repoRoot "backend\runtime\windows\build"
    & powershell -NoProfile -ExecutionPolicy Bypass -File ".\backend\runtime\windows\build_backend_exe.ps1" -BackendRoot ".\backend" -OutputRoot $backendBuildRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Backend EXE build failed."
    }

    $version = (Get-Content -Path (Join-Path $repoRoot "package.json") -Raw | ConvertFrom-Json).version
    $bundleRoot = Join-Path $repoRoot $OutputRoot
    $bundleName = "chipverify-fullnode-win-x64-v$version"
    $bundleDir = Join-Path $bundleRoot $bundleName

    if (Test-Path $bundleDir) {
        Remove-Item -Path $bundleDir -Recurse -Force
    }

    New-Item -ItemType Directory -Path $bundleDir -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $bundleDir "desktop-installer") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $bundleDir "runtime\backend") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $bundleDir "runtime\bin") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $bundleDir "runtime\artifacts") -Force | Out-Null

    Write-Host "[3/5] Collecting installer and runtime payload..." -ForegroundColor Cyan
    $installer = Get-ChildItem -Path (Join-Path $repoRoot "dist-electron") -Filter "*win-x64*.exe" | Select-Object -First 1
    if (-not $installer) {
        throw "Windows installer not found in dist-electron."
    }

    Copy-Item -Path $installer.FullName -Destination (Join-Path $bundleDir "desktop-installer") -Force

    $backendDistDir = Join-Path $backendBuildRoot "dist\chipverify-backend"
    if (-not (Test-Path $backendDistDir)) {
        throw "Backend EXE directory missing: $backendDistDir"
    }
    Copy-Item -Path (Join-Path $backendDistDir "*") -Destination (Join-Path $bundleDir "runtime\backend") -Recurse -Force

    Copy-Item -Path (Join-Path $repoRoot "backend\runtime\bin\*") -Destination (Join-Path $bundleDir "runtime\bin") -Force
    Copy-Item -Path (Join-Path $repoRoot "backend\runtime\windows\runtime.env.example") -Destination (Join-Path $bundleDir "runtime\runtime.env.example") -Force
    Copy-Item -Path (Join-Path $repoRoot "backend\runtime\verify_artifacts.py") -Destination (Join-Path $bundleDir "runtime\verify_artifacts.py") -Force
    Copy-Item -Path (Join-Path $repoRoot "backend\runtime\artifacts\artifacts.manifest.example.json") -Destination (Join-Path $bundleDir "runtime\artifacts\artifacts.manifest.example.json") -Force
    Copy-Item -Path (Join-Path $repoRoot "backend\runtime\start_runtime_windows.ps1") -Destination (Join-Path $bundleDir "runtime\start_runtime_windows.ps1") -Force
    Copy-Item -Path (Join-Path $repoRoot "backend\runtime\windows\install_chipverify_service.ps1") -Destination (Join-Path $bundleDir "runtime\install_chipverify_service.ps1") -Force

    Write-Host "[4/5] Writing operator guide..." -ForegroundColor Cyan
    $readme = @"
# ChipVerify Full Node Bundle (Windows x64)

This bundle includes:
- Desktop installer (`desktop-installer/*.exe`)
- Backend runtime EXE payload (`runtime/backend/`)
- llama.cpp runtime binaries (`runtime/bin/`)
- Runtime startup + service installation scripts
- Artifact verification tooling + manifest template

Model handling:
- GGUF model is intentionally NOT embedded.
- Place the approved model in your chosen model directory and update `runtime/runtime.env.example`.
- Update `runtime/artifacts/artifacts.manifest.example.json` with the approved model file name and SHA-256.

Recommended deployment sequence:
1. Install desktop app from `desktop-installer/*.exe`.
2. Configure `runtime/runtime.env` from the example.
3. Validate artifact hashes with `runtime/verify_artifacts.py`.
4. Start runtime with `runtime/start_runtime_windows.ps1` (manual) or install Windows service with `runtime/install_chipverify_service.ps1`.
"@

    Set-Content -Path (Join-Path $bundleDir "README.txt") -Value $readme -Encoding UTF8

    Write-Host "[5/5] Creating distributable archive..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $bundleRoot -Force | Out-Null
    $zipPath = Join-Path $bundleRoot "$bundleName.zip"
    if (Test-Path $zipPath) {
        Remove-Item -Path $zipPath -Force
    }
    Compress-Archive -Path (Join-Path $bundleDir "*") -DestinationPath $zipPath -Force

    Write-Host "FULLNODE_BUNDLE=$zipPath" -ForegroundColor Green
}
finally {
    Pop-Location
}
