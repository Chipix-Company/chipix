# Build the embedded Linux AppImage on Depot and export to ./dist-electron.
# Windows-native wrapper (avoids Git Bash depot/node PATH issues).
#
# Prerequisites:
#   npm install -g @depot/cli
#   depot login
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/packaging/build_appimage_depot.ps1
#   npm run package:appimage:depot:win
param(
    [string]$OutputDir = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "dist-electron")
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

if (-not (Get-Command depot -ErrorAction SilentlyContinue)) {
    Write-Error "Depot CLI not found. Install: npm install -g @depot/cli`nThen run: depot login"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "Building Linux AppImage on Depot (target: artifact) -> $OutputDir"

$sentryEnv = if ($env:CHIPVERIFY_SENTRY_ENVIRONMENT) { $env:CHIPVERIFY_SENTRY_ENVIRONMENT } else { "production" }
$buildArgs = @(
    "build",
    "-f", "Dockerfile.appimage",
    "--platform", "linux/amd64",
    "--target", "artifact",
    "--build-arg", "CHIPVERIFY_DEMO_GEMINI_API_KEY=$($env:CHIPVERIFY_DEMO_GEMINI_API_KEY)",
    "--build-arg", "CHIPVERIFY_DEMO_OPENAI_API_KEY=$($env:CHIPVERIFY_DEMO_OPENAI_API_KEY)",
    "--build-arg", "CHIPVERIFY_SENTRY_DSN=$($env:CHIPVERIFY_SENTRY_DSN)",
    "--build-arg", "CHIPVERIFY_SENTRY_ENVIRONMENT=$sentryEnv",
    "-o", "type=local,dest=dist-electron",
    "."
)

& depot @buildArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Done. Artifacts in ${OutputDir}:"
Get-ChildItem -Path $OutputDir -File | Where-Object {
    $_.Extension -eq ".AppImage" -or $_.Name -eq "latest-linux.yml" -or $_.Extension -eq ".blockmap"
} | ForEach-Object { $_.FullName }

$appImage = Get-ChildItem -Path $OutputDir -Filter "*.AppImage" -File -ErrorAction SilentlyContinue | Select-Object -First 1
if ($appImage) {
    Write-Host ""
    Write-Host "Run on Linux:  chmod +x $($appImage.Name) && ./$($appImage.Name)"
}
