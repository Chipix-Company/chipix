param(
    [string]$OutputRoot = "dist-shareable/windows-no-cert"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..\..")).Path

function Assert-ChildPath([string]$Parent, [string]$Child) {
    $parentPath = [System.IO.Path]::GetFullPath($Parent)
    $childPath = [System.IO.Path]::GetFullPath($Child)
    if (-not $childPath.StartsWith($parentPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to write outside workspace: $childPath"
    }
}

function Invoke-RobocopyChecked([string]$Source, [string]$Destination, [string[]]$RobocopyArgs) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    & robocopy $Source $Destination @RobocopyArgs | Out-Host
    if ($LASTEXITCODE -gt 7) {
        throw "Robocopy failed with exit code $LASTEXITCODE"
    }
}

function Remove-DirectoryIfExists([string]$Path) {
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    Assert-ChildPath -Parent $repoRoot -Child $fullPath
    $longPath = "\\?\$fullPath"
    if (Test-Path -LiteralPath $longPath) {
        Remove-Item -LiteralPath $longPath -Recurse -Force -ErrorAction Stop
    }
}

Push-Location $repoRoot
try {
    Write-Host "[1/6] Building browser frontend..." -ForegroundColor Cyan
    & npm run build:frontend
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed."
    }

    $version = (Get-Content -Path (Join-Path $repoRoot "package.json") -Raw | ConvertFrom-Json).version
    $shareRoot = Join-Path $repoRoot $OutputRoot
    $bundleName = "ChipVerify-NoCert-WebDemo-$version-win"
    $previewDir = Join-Path $shareRoot $bundleName
    $stagingRoot = Join-Path $shareRoot "_staging"
    $bundleDir = Join-Path $stagingRoot $bundleName
    $zipPath = Join-Path $shareRoot "$bundleName.zip"

    Assert-ChildPath -Parent $repoRoot -Child $shareRoot
    Assert-ChildPath -Parent $repoRoot -Child $bundleDir
    Assert-ChildPath -Parent $repoRoot -Child $previewDir
    Assert-ChildPath -Parent $repoRoot -Child $stagingRoot
    Assert-ChildPath -Parent $repoRoot -Child $zipPath

    Write-Host "[2/6] Preparing clean no-cert bundle directory..." -ForegroundColor Cyan
    if (Test-Path $stagingRoot) {
        Remove-DirectoryIfExists -Path $stagingRoot
    }
    New-Item -ItemType Directory -Path $bundleDir -Force | Out-Null

    Write-Host "[3/6] Copying backend source without local caches/databases..." -ForegroundColor Cyan
    Invoke-RobocopyChecked `
        -Source (Join-Path $repoRoot "backend") `
        -Destination (Join-Path $bundleDir "backend") `
        -RobocopyArgs @(
            "/E",
            "/R:1",
            "/W:1",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
            "/XD", "__pycache__", ".pytest_cache", "logs", "scratch", "tests", "build", "bin",
            "/XF", "*.pyc", "*.pyo", "*.db", "*.db-shm", "*.db-wal", ".sys_id", "license.key", "parser.out", "parsetab.py"
        )

    Write-Host "[4/6] Copying optional RTL designer source..." -ForegroundColor Cyan
    $rtlDesignerDir = Join-Path $repoRoot "RTL_designer"
    if (Test-Path $rtlDesignerDir) {
        Invoke-RobocopyChecked `
            -Source $rtlDesignerDir `
            -Destination (Join-Path $bundleDir "RTL_designer") `
            -RobocopyArgs @(
                "/E",
                "/R:1",
                "/W:1",
                "/NFL",
                "/NDL",
                "/NJH",
                "/NJS",
                "/XD", "__pycache__", ".pytest_cache", "output", "outputs", ".venv", "venv", "build", "dist",
                "/XF", "*.pyc", "*.pyo", "*.db", "*.db-shm", "*.db-wal", ".env", "license.key", ".sys_id"
            )
    }

    Write-Host "[5/6] Copying built frontend and launch scripts..." -ForegroundColor Cyan
    Copy-Item -Path (Join-Path $repoRoot "frontend\dist") -Destination (Join-Path $bundleDir "frontend-dist") -Recurse -Force

    $runScript = @'
@echo off
setlocal

set "DEMO_ROOT=%~dp0"
set "DEMO_ROOT=%DEMO_ROOT:~0,-1%"

cd /d "%DEMO_ROOT%"
if not exist "workspace" mkdir "workspace"
if not exist "outputs" mkdir "outputs"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  set "PY_LAUNCHER=py -3"
) else (
  where python >nul 2>nul
  if %ERRORLEVEL% NEQ 0 (
    echo Python 3 was not found. Install Python 3 from python.org, then run this file again.
    pause
    exit /b 1
  )
  set "PY_LAUNCHER=python"
)

set "CHIPVERIFY_SECRET_FILE=%DEMO_ROOT%\workspace\chipverify_secret.key"
if not exist "%CHIPVERIFY_SECRET_FILE%" (
  echo Creating local demo secret...
  %PY_LAUNCHER% -c "import pathlib,secrets; pathlib.Path(r'%CHIPVERIFY_SECRET_FILE%').write_text(secrets.token_urlsafe(48), encoding='utf-8')"
  if %ERRORLEVEL% NEQ 0 (
    echo Failed to create local demo secret.
    pause
    exit /b 1
  )
)
for /f "usebackq delims=" %%s in ("%CHIPVERIFY_SECRET_FILE%") do set "CHIPVERIFY_SECRET_KEY=%%s"
if "%CHIPVERIFY_SECRET_KEY%"=="" (
  echo Local demo secret is empty. Delete workspace\chipverify_secret.key and run again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  %PY_LAUNCHER% -m venv ".venv"
  if %ERRORLEVEL% NEQ 0 (
    echo Failed to create Python environment.
    pause
    exit /b 1
  )
)

echo Installing or updating backend dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if %ERRORLEVEL% NEQ 0 (
  echo Failed to upgrade pip.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install -r "backend\requirements.txt"
if %ERRORLEVEL% NEQ 0 (
  echo Failed to install backend dependencies.
  pause
  exit /b 1
)

set "CHIPVERIFY_FRONTEND_DIST=%DEMO_ROOT%\frontend-dist"
set "CHIPVERIFY_WORKSPACE_ROOT=%DEMO_ROOT%\workspace"
set "CHIPVERIFY_OUTPUTS_DIR=%DEMO_ROOT%\outputs"
set "CHIPVERIFY_REQUIRE_ACTIVATION=false"
set "CHIPVERIFY_BACKEND_HOST=127.0.0.1"
set "CHIPVERIFY_BACKEND_PORT=7348"
set "DATABASE_URL=sqlite:///../workspace/chipverify.db"

echo Starting ChipVerify backend and browser UI...
start "ChipVerify Backend" /D "%DEMO_ROOT%\backend" cmd /k ""%DEMO_ROOT%\.venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 7348"
timeout /t 6 /nobreak >nul
start "" "http://127.0.0.1:7348/app/"

echo.
echo ChipVerify is opening at http://127.0.0.1:7348/app/
echo Keep the backend window open while testing.
pause
'@

    Set-Content -Path (Join-Path $bundleDir "RUN_CHIPVERIFY_WEB_DEMO.cmd") -Value $runScript -Encoding ASCII

    $stopScript = @'
@echo off
echo Stopping processes listening on port 7348...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":7348"') do taskkill /PID %%p /F >nul 2>nul
echo Done.
pause
'@

    Set-Content -Path (Join-Path $bundleDir "STOP_CHIPVERIFY_WEB_DEMO.cmd") -Value $stopScript -Encoding ASCII

    $readme = @"
ChipVerify No-Certificate Web Demo
==================================

This bundle avoids the unsigned Windows desktop EXE path.

How it works:
- No ChipVerify Desktop.exe is included.
- No PyInstaller backend EXE is included.
- The app runs in the user's browser at http://127.0.0.1:7348/app/.
- The backend runs with the user's installed Python 3.

Prerequisites:
- Windows 10 or 11
- Python 3 installed from python.org
- Internet access on first run so pip can install backend dependencies

Start:
1. Extract this folder.
2. Double-click RUN_CHIPVERIFY_WEB_DEMO.cmd.
3. Keep the backend console window open.

Stop:
- Double-click STOP_CHIPVERIFY_WEB_DEMO.cmd, or close the backend console.

Notes:
- This avoids the unsigned Electron/PyInstaller EXE warning, but Windows can still warn about downloaded scripts depending on local policy.
- The launcher creates a local workspace\chipverify_secret.key on first run so the backend can start securely without a checked-in secret.
- If you include a backend .env with an API key, anyone with this bundle can read it. Use this only for trusted demos.
"@

    Set-Content -Path (Join-Path $bundleDir "README_FIRST.txt") -Value $readme -Encoding UTF8

    Write-Host "[6/6] Creating ZIP..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $shareRoot -Force | Out-Null
    if (Test-Path $zipPath) {
        Remove-Item -Path $zipPath -Force
    }
    Compress-Archive -Path $bundleDir -DestinationPath $zipPath -Force

    $runnableDir = $bundleDir
    try {
        if (Test-Path $previewDir) {
            Remove-DirectoryIfExists -Path $previewDir
        }
        Copy-Item -Path $bundleDir -Destination $shareRoot -Recurse -Force
        $runnableDir = $previewDir
    } catch {
        Write-Warning "Could not refresh extracted preview folder, probably because a previous demo runtime is still locked. ZIP output is clean. Details: $($_.Exception.Message)"
    }

    Write-Host "NO_CERT_WEB_DEMO_ZIP=$zipPath" -ForegroundColor Green
    Write-Host "RUN_SCRIPT=$(Join-Path $runnableDir 'RUN_CHIPVERIFY_WEB_DEMO.cmd')" -ForegroundColor Green
}
finally {
    Pop-Location
}
