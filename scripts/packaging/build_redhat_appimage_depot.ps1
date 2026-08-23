# Build RHEL-compatible AppImage on Depot (AlmaLinux 8, Bedrock defaults) -> ./dist-redhat
param(
    [string]$OutputDir = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "dist-redhat")
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

if (-not (Get-Command depot -ErrorAction SilentlyContinue)) {
    Write-Error "Depot CLI not found. Install: npm install -g @depot/cli`nThen: depot login"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

if (-not $env:CHIPVERIFY_DEMO_BEDROCK_API_KEY) {
    $mainJs = Join-Path $RepoRoot "electron\main.js"
    $mainContent = Get-Content -Path $mainJs -Raw
    if ($mainContent -match 'PACKAGED_BEDROCK_API_KEY\s*=\s*\r?\n\s*"([^"]+)"') {
        $env:CHIPVERIFY_DEMO_BEDROCK_API_KEY = $Matches[1]
        Write-Host "Using PACKAGED_BEDROCK_API_KEY from electron/main.js for Depot build"
    }
}

Write-Host "Building Red Hat AppImage on Depot (AlmaLinux 8, Bedrock) -> $OutputDir"

$sentryEnv = if ($env:CHIPVERIFY_SENTRY_ENVIRONMENT) { $env:CHIPVERIFY_SENTRY_ENVIRONMENT } else { "production" }
$buildArgs = @(
    "build",
    "-f", "Dockerfile.redhat.appimage",
    "--platform", "linux/amd64",
    "--target", "artifact",
    "--build-arg", "CHIPVERIFY_DEMO_BEDROCK_API_KEY=$($env:CHIPVERIFY_DEMO_BEDROCK_API_KEY)",
    "--build-arg", "CHIPVERIFY_DEMO_GEMINI_API_KEY=$($env:CHIPVERIFY_DEMO_GEMINI_API_KEY)",
    "--build-arg", "CHIPVERIFY_DEMO_OPENAI_API_KEY=$($env:CHIPVERIFY_DEMO_OPENAI_API_KEY)",
    "--build-arg", "CHIPVERIFY_SENTRY_DSN=$($env:CHIPVERIFY_SENTRY_DSN)",
    "--build-arg", "CHIPVERIFY_SENTRY_ENVIRONMENT=$sentryEnv",
    "-o", "type=local,dest=dist-redhat",
    "."
)

& depot @buildArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Done. Artifacts in ${OutputDir}:"
Get-ChildItem -Path $OutputDir -File | Where-Object {
    $_.Name -like '*redhat*.AppImage' -or $_.Name -eq 'latest-linux.yml' -or $_.Extension -eq '.blockmap'
} | ForEach-Object { $_.FullName }

$appImage = Get-ChildItem -Path $OutputDir -Filter '*redhat*.AppImage' -File -ErrorAction SilentlyContinue | Select-Object -First 1
if ($appImage) {
    Write-Host ""
    Write-Host "Run on Red Hat:"
    Write-Host "  chmod +x $($appImage.Name)"
    Write-Host "  ./$($appImage.Name) --appimage-extract-and-run"
    Write-Host ""
    $version = if ($appImage.Name -match '-(\d+\.\d+\.\d+)-redhat-') { $Matches[1] } else { (node -p "require('./package.json').version") }
    Write-Host "Publish to Convex (metadata only - binaries stay on GitHub Releases):"
    Write-Host "  ./scripts/convex/set-release-version.sh --version $version --platform linux --github-tag v$version"
}
