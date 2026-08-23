# ChipVerify - Automated Orchestrator for Local-First Production Runtime
# Launches: 1. Model Runtime (7349) + 2. Backend API (7348) + 3. Frontend Dev Server (5173 / Electron)

$ErrorActionPreference = "Stop"

# Use virtual environment if present
$minicondaPython = Join-Path $env:USERPROFILE "miniconda3\python.exe"
$pythonBin = if (Test-Path ".venv/Scripts/python.exe") {
    "./.venv/Scripts/python.exe"
} elseif (Test-Path $minicondaPython) {
    $minicondaPython
} else {
    (Get-Command python.exe -ErrorAction Stop).Source
}
$pythonBinResolved = (Resolve-Path $pythonBin).Path
Write-Host "Using Python runtime: $pythonBinResolved" -ForegroundColor DarkGray

$backendEnvPath = Join-Path (Get-Location).Path "backend\.env"
$backendEnv = @{}
if (Test-Path $backendEnvPath) {
    try {
        $backendEnvLines = Get-Content $backendEnvPath
        foreach ($line in $backendEnvLines) {
            if ($line -match '^\s*#' -or $line -match '^\s*$') {
                continue
            }
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
                $backendEnv[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
            }
        }
    } catch {
        Write-Warning "Could not read backend .env provider: $($_.Exception.Message)"
    }
}

$backendEnvProvider = if ($backendEnv.ContainsKey("CHIPVERIFY_LLM_PROVIDER")) {
    $backendEnv["CHIPVERIFY_LLM_PROVIDER"]
} elseif ($backendEnv.ContainsKey("MODEL_PROVIDER")) {
    $backendEnv["MODEL_PROVIDER"]
} else {
    ""
}
$backendHasBedrockKey = (
    $backendEnv.ContainsKey("BEDROCK_API_KEY") -and
    -not [string]::IsNullOrWhiteSpace([string]$backendEnv["BEDROCK_API_KEY"])
) -or (
    $backendEnv.ContainsKey("AWS_BEARER_TOKEN_BEDROCK") -and
    -not [string]::IsNullOrWhiteSpace([string]$backendEnv["AWS_BEARER_TOKEN_BEDROCK"])
)

$configuredProvider = if ($env:CHIPVERIFY_LLM_PROVIDER) {
    $env:CHIPVERIFY_LLM_PROVIDER
} elseif ($env:MODEL_PROVIDER) {
    $env:MODEL_PROVIDER
} elseif ($backendEnvProvider) {
    $backendEnvProvider
} elseif ($env:BEDROCK_API_KEY -or $env:AWS_BEARER_TOKEN_BEDROCK -or $backendHasBedrockKey) {
    "bedrock"
} else {
    "gemini"
}

$provider = $configuredProvider.Trim().ToLower()
$useLocalRuntime = $provider -eq "local"
$repoRoot = (Get-Location).Path
$demoRelaxMentalModelGates = if ($null -ne $env:CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES) {
    $env:CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES
} else {
    "true"
}

Write-Host "--- ChipVerify Orchestrator: Initializing ---" -ForegroundColor Cyan

$activationPath = Join-Path $env:APPDATA "chipverify-desktop\activation.json"
if (Test-Path $activationPath) {
    try {
        $activation = Get-Content $activationPath -Raw | ConvertFrom-Json
        if ($activation.machineId -and $activation.activationHash) {
            $env:CHIPVERIFY_DESKTOP_MACHINE_ID = [string]$activation.machineId
            $env:CHIPVERIFY_DESKTOP_ACTIVATION_HASH = [string]$activation.activationHash
            $env:CHIPVERIFY_DESKTOP_ACTIVATED = "1"
            $env:CHIPVERIFY_DESKTOP_ACTIVATION_BYPASS_LICENSE = "true"
            Write-Host "Desktop activation cache found; backend license bypass env prepared." -ForegroundColor Green
        }
    } catch {
        Write-Warning "Desktop activation cache could not be read: $($_.Exception.Message)"
    }
}

if (-not $env:CHIPVERIFY_DESKTOP_ACTIVATION_BYPASS_LICENSE) {
    try {
        $activationModule = Join-Path $repoRoot "electron\security\activation.js"
        if (Test-Path $activationModule) {
            $activationJson = node -e "const a=require(process.argv[1]); const machineId=a.getMachineId(); const machineKey=a.expectedMachineKey(machineId); process.stdout.write(JSON.stringify({machineId, activationHash:a.hashActivation(machineId, machineKey)}));" $activationModule
            $activation = $activationJson | ConvertFrom-Json
            if ($activation.machineId -and $activation.activationHash) {
                $env:CHIPVERIFY_DESKTOP_MACHINE_ID = [string]$activation.machineId
                $env:CHIPVERIFY_DESKTOP_ACTIVATION_HASH = [string]$activation.activationHash
                $env:CHIPVERIFY_DESKTOP_ACTIVATED = "1"
                $env:CHIPVERIFY_DESKTOP_ACTIVATION_BYPASS_LICENSE = "true"
                Write-Host "Temporary local dev activation prepared for backend project APIs." -ForegroundColor Green
            }
        }
    } catch {
        Write-Warning "Could not prepare temporary local dev activation: $($_.Exception.Message)"
    }
}

$desktopLicenseEnvScript = ""
if ($env:CHIPVERIFY_DESKTOP_ACTIVATION_BYPASS_LICENSE) {
    $machineId = ($env:CHIPVERIFY_DESKTOP_MACHINE_ID -replace "'", "''")
    $activationHash = ($env:CHIPVERIFY_DESKTOP_ACTIVATION_HASH -replace "'", "''")
    $desktopLicenseEnvScript = "`$env:CHIPVERIFY_DESKTOP_ACTIVATION_BYPASS_LICENSE='true'; `$env:CHIPVERIFY_DESKTOP_MACHINE_ID='$machineId'; `$env:CHIPVERIFY_DESKTOP_ACTIVATION_HASH='$activationHash'; "
}

# 0. Force Cleanup of existing Ghost Processes
# - Kill all Electron instances (main culprit for "Missing Bridge" error)
# - Kill all Node processes on 5173 (Vite server)
# - Kill all Python processes on 7348 (Backend)
Write-Host "Performing force cleanup of previous sessions..." -ForegroundColor Gray
Get-Process electron -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

$frontendPort = 5173
$backendPort = 7348

# Kill anything on Frontend port
$frontendConnections = Get-NetTCPConnection -LocalPort $frontendPort -State Listen -ErrorAction SilentlyContinue
if ($frontendConnections) {
    $frontendPids = $frontendConnections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($frontendPid in $frontendPids) {
        Stop-Process -Id $frontendPid -Force -ErrorAction SilentlyContinue
    }
}

# Kill anything on Backend port
$backendConnections = Get-NetTCPConnection -LocalPort $backendPort -State Listen -ErrorAction SilentlyContinue
if ($backendConnections) {
    $backendPids = $backendConnections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($backendPid in $backendPids) {
        Stop-Process -Id $backendPid -Force -ErrorAction SilentlyContinue
    }
}

# 1. Start backend in selected provider mode.
if ($useLocalRuntime) {
    Write-Host "Launching Local Runtime (Model + Backend)..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "Set-Location '$repoRoot'; $desktopLicenseEnvScript `$env:CHIPVERIFY_WORKSPACE_ROOT='$repoRoot'; `$env:CHIPVERIFY_SECRET_KEY='chipverify-local-secret-20260321'; `$env:CHIPVERIFY_LLM_API_KEY='chipverify-local-runtime-key'; `$env:CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES='$demoRelaxMentalModelGates'; `$env:CHIPVERIFY_GGUF_PATH='backend/runtime/models/chipix-v0.1-gguf/qwen-3.5-2b-verilog-h100-fullft-v2.Q4_K_M.gguf'; `$env:CHIPVERIFY_LLAMACPP_BIN='backend/runtime/bin/llama-server.exe'; .\backend\runtime\start_runtime_windows.ps1"
} else {
    Write-Host "Launching Backend API (External Provider: $provider)..." -ForegroundColor Yellow
    Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "Set-Location '$repoRoot'; Set-Location backend; $desktopLicenseEnvScript `$env:CHIPVERIFY_WORKSPACE_ROOT='$repoRoot'; `$env:CHIPVERIFY_SECRET_KEY='chipverify-local-secret-20260321'; `$env:CHIPVERIFY_ALLOW_DEV_AUTH='true'; `$env:CHIPVERIFY_LLM_PROVIDER='$provider'; `$env:MODEL_PROVIDER='$provider'; `$env:CHIPVERIFY_ALLOW_LOCAL_LLM='false'; `$env:CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES='$demoRelaxMentalModelGates'; & '$pythonBinResolved' -m uvicorn main:app --host 127.0.0.1 --port 7348"
}

# 2. Wait for backend health (7348)
Write-Host "Waiting for services to initialize..." -ForegroundColor Gray
$backendReady = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:7348/api/v1/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($health.status -eq "ok") {
            $backendReady = $true
            break
        }
    } catch {}
    Start-Sleep -Seconds 1
}

if (-not $backendReady) {
    Write-Warning "Backend service did not respond on 7348. Check the dedicated logs window."
} else {
    Write-Host "Backend service is ONLINE on 7348." -ForegroundColor Green
}

# 3. Launch Frontend / Desktop Shell
Write-Host "Launching Frontend Dev Server on port 5173..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "Set-Location '$repoRoot'; Set-Location frontend; `$env:CHIPVERIFY_WORKSPACE_ROOT='$repoRoot'; npm.cmd run dev"

$launchElectronEnv = if ($null -ne $env:CHIPVERIFY_LAUNCH_ELECTRON) { $env:CHIPVERIFY_LAUNCH_ELECTRON } else { "true" }
$launchElectron = @("1", "true", "yes", "on") -contains $launchElectronEnv.Trim().ToLower()

if ($launchElectron) {
    Write-Host "Waiting for frontend dev server before launching Electron shell..." -ForegroundColor Gray
    $frontendReady = $false
    $frontendWaitSeconds = 60
    for ($i = 0; $i -lt $frontendWaitSeconds; $i++) {
        try {
            $frontendListening = Get-NetTCPConnection -LocalPort $frontendPort -State Listen -ErrorAction SilentlyContinue
            if ($frontendListening) {
                try {
                    $null = Invoke-WebRequest -Uri "http://localhost:5173" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                    $frontendReady = $true
                    break
                } catch {
                    try {
                        $null = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                        $frontendReady = $true
                        break
                    } catch {
                        # Vite can start listening before first successful HTTP response; keep waiting briefly.
                    }
                }
            }
        } catch {
            # Ignore transient errors during startup polling.
        }

        if (($i + 1) % 5 -eq 0) {
            Write-Host "Frontend startup wait: $($i + 1)s / $frontendWaitSeconds s" -ForegroundColor DarkGray
        }
        Start-Sleep -Seconds 1
    }

    if (-not $frontendReady) {
        Write-Warning "Frontend dev server did not become ready on 5173 after $frontendWaitSeconds seconds; skipping Electron launch. Check the frontend terminal window for npm/vite errors."
    } else {
        Write-Host "Launching Electron desktop shell..." -ForegroundColor Yellow
        Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", "Set-Location '$repoRoot'; `$env:CHIPVERIFY_WORKSPACE_ROOT='$repoRoot'; `$env:ELECTRON_ENABLE_LOGGING='1'; `$env:ELECTRON_ENABLE_STACK_DUMPING='1'; npm.cmd run dev:electron"

        $electronReady = $false
        for ($i = 0; $i -lt 20; $i++) {
            $electronProc = Get-Process electron -ErrorAction SilentlyContinue
            if ($electronProc) {
                $electronReady = $true
                break
            }
            Start-Sleep -Seconds 1
        }

        if (-not $electronReady) {
            Write-Warning "Electron process did not start. The UI will open in browser-only mode and show bridge-missing errors. Check the Electron terminal window for startup errors."
        } else {
            Start-Sleep -Seconds 3
            $electronStillRunning = Get-Process electron -ErrorAction SilentlyContinue
            if (-not $electronStillRunning) {
                Write-Warning "Electron started then exited. Bridge will be unavailable. Open the Electron terminal window and inspect the crash output."
            } else {
                Write-Host "Electron shell is ONLINE." -ForegroundColor Green
            }
        }
    }
} else {
    Write-Host "Skipping Electron launch because CHIPVERIFY_LAUNCH_ELECTRON=$launchElectronEnv" -ForegroundColor DarkGray
}

Write-Host "--- ChipVerify Orchestrator: Complete ---" -ForegroundColor Cyan
Write-Host "1. Verification Backend: http://127.0.0.1:7348"
if ($useLocalRuntime) {
    Write-Host "2. Model Server: http://127.0.0.1:7349"
    Write-Host "3. Frontend UI: http://localhost:5173"
} else {
    Write-Host "2. Frontend UI: http://localhost:5173"
}
if ($launchElectron) {
    $electronSummary = Get-Process electron -ErrorAction SilentlyContinue
    if ($electronSummary) {
        Write-Host "Desktop Shell: Electron process detected"
    } else {
        Write-Host "Desktop Shell: NOT running (bridge unavailable; browser mode only)" -ForegroundColor Yellow
    }
}
Write-Host "-------------------------------------------"
