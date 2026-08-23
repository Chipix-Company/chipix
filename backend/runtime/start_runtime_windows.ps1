$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = (Resolve-Path (Join-Path $scriptDir "..")).Path

$pythonBin = if ($env:CHIPVERIFY_PYTHON_BIN) { $env:CHIPVERIFY_PYTHON_BIN } else { "python" }
$llamaBin = if ($env:CHIPVERIFY_LLAMACPP_BIN) { $env:CHIPVERIFY_LLAMACPP_BIN } else { "llama-server.exe" }
$ggufPath = $env:CHIPVERIFY_GGUF_PATH
$artifactManifestPath = $env:CHIPVERIFY_ARTIFACT_MANIFEST_PATH

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

function Assert-Executable([string]$Command, [string]$EnvVarName, [string]$FriendlyName) {
    $resolved = Resolve-ExecutablePath $Command
    if (-not $resolved) {
        throw "$FriendlyName not found: '$Command'. Set $EnvVarName to an absolute path or ensure it is on PATH."
    }
    return $resolved
}

function Assert-PythonModule([string]$PythonPath, [string]$ModuleName) {
    & $PythonPath -c "import $ModuleName" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Python at '$PythonPath' cannot import '$ModuleName'. Install backend dependencies (e.g. pip install -r backend/requirements.txt) or set CHIPVERIFY_PYTHON_BIN to the correct venv python."
    }
}

function Write-RedactedTail([string]$Path, [int]$TailLines = 60) {
    if (-not (Test-Path $Path)) {
        return
    }

    $lines = Get-Content -Path $Path -Tail $TailLines -ErrorAction SilentlyContinue
    if (-not $lines) {
        return
    }

    $apiKey = $env:CHIPVERIFY_LLM_API_KEY
    if (-not [string]::IsNullOrWhiteSpace($apiKey)) {
        $escaped = [Regex]::Escape($apiKey)
        $lines = $lines | ForEach-Object { $_ -replace $escaped, "<redacted>" }
    }

    $lines | ForEach-Object { Write-Host $_ }
}

if ([string]::IsNullOrWhiteSpace($ggufPath)) {
    throw "CHIPVERIFY_GGUF_PATH is required and must point to a GGUF file."
}

if (-not (Test-Path $ggufPath)) {
    throw "GGUF file not found: $ggufPath"
}

$pythonBin = Assert-Executable $pythonBin "CHIPVERIFY_PYTHON_BIN" "Python"
$llamaBin = Assert-Executable $llamaBin "CHIPVERIFY_LLAMACPP_BIN" "llama.cpp server (llama-server.exe)"
Assert-PythonModule $pythonBin "uvicorn"

if (-not [string]::IsNullOrWhiteSpace($artifactManifestPath)) {
    if (-not (Test-Path $artifactManifestPath)) {
        throw "Artifact manifest not found: '$artifactManifestPath' (set CHIPVERIFY_ARTIFACT_MANIFEST_PATH)"
    }

    Write-Host "Verifying runtime artifacts with manifest: $artifactManifestPath"
    & $pythonBin (Join-Path $scriptDir "verify_artifacts.py") --manifest $artifactManifestPath --llama-bin $llamaBin --gguf-path $ggufPath --require-expected
    if ($LASTEXITCODE -ne 0) {
        throw "Artifact verification failed (exit_code=$LASTEXITCODE)."
    }
}

$llmHost = if ($env:CHIPVERIFY_LLM_BIND_HOST) { $env:CHIPVERIFY_LLM_BIND_HOST } else { "127.0.0.1" }
$llmPort = if ($env:CHIPVERIFY_LLM_PORT) { $env:CHIPVERIFY_LLM_PORT } else { "7349" }
$llmContextSize = if ($env:CHIPVERIFY_LLM_CONTEXT_SIZE) { $env:CHIPVERIFY_LLM_CONTEXT_SIZE } else { "8192" }
$llmParallel = if ($env:CHIPVERIFY_LLM_PARALLEL) { $env:CHIPVERIFY_LLM_PARALLEL } else { "1" }

$llmApiKeyRequiredRaw = if ($env:CHIPVERIFY_LLM_API_KEY_REQUIRED) { $env:CHIPVERIFY_LLM_API_KEY_REQUIRED } else { "true" }
$allowPublicBindRaw = if ($env:CHIPVERIFY_ALLOW_PUBLIC_BIND) { $env:CHIPVERIFY_ALLOW_PUBLIC_BIND } else { "false" }

function Convert-ToBoolean([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }

    switch ($Value.Trim().ToLowerInvariant()) {
        "1" { return $true }
        "true" { return $true }
        "yes" { return $true }
        "on" { return $true }
        default { return $false }
    }
}

function Test-IsPublicBindHost([string]$HostValue) {
    switch ($HostValue) {
        "0.0.0.0" { return $true }
        "::" { return $true }
        "[::]" { return $true }
        "*" { return $true }
        default { return $false }
    }
}

function Stop-ProcessOnPort([int]$Port) {
    Write-Host "Checking for existing processes on port $Port..."
    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    if ($connections) {
        foreach ($conn in $connections) {
            $ownerPid = $conn.OwningProcess
            if ($ownerPid -gt 0) {
                try {
                    $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
                    if ($proc) {
                        Write-Host "Stopping process $($proc.ProcessName) (PID: $ownerPid) on port $Port..."
                        Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
                        Start-Sleep -Milliseconds 500
                    }
                } catch {
                    Write-Warning "Failed to stop process with PID $ownerPid on port $Port."
                }
            }
        }
    }
}

$llmApiKeyRequired = Convert-ToBoolean $llmApiKeyRequiredRaw
$allowPublicBind = Convert-ToBoolean $allowPublicBindRaw

$backendHost = if ($env:CHIPVERIFY_BACKEND_HOST) { $env:CHIPVERIFY_BACKEND_HOST } else { "127.0.0.1" }
$backendPort = if ($env:CHIPVERIFY_BACKEND_PORT) { $env:CHIPVERIFY_BACKEND_PORT } else { "7348" }

Stop-ProcessOnPort -Port $llmPort
Stop-ProcessOnPort -Port $backendPort

if ($llmApiKeyRequired -and [string]::IsNullOrWhiteSpace($env:CHIPVERIFY_LLM_API_KEY)) {
    throw "CHIPVERIFY_LLM_API_KEY is required because CHIPVERIFY_LLM_API_KEY_REQUIRED=true."
}

if (-not $allowPublicBind) {
    if (Test-IsPublicBindHost $llmHost) {
        throw "Refusing public model runtime bind host '$llmHost'. Set CHIPVERIFY_ALLOW_PUBLIC_BIND=true to override."
    }

    if (Test-IsPublicBindHost $backendHost) {
        throw "Refusing public backend bind host '$backendHost'. Set CHIPVERIFY_ALLOW_PUBLIC_BIND=true to override."
    }
}

$logDir = if ($env:CHIPVERIFY_LOG_DIR) { $env:CHIPVERIFY_LOG_DIR } else { Join-Path $backendDir "logs" }
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$llamaStdOut = Join-Path $logDir "llama-server.stdout.log"
$llamaStdErr = Join-Path $logDir "llama-server.stderr.log"

$llamaArgs = @(
    "-m", $ggufPath,
    "--host", $llmHost,
    "--port", $llmPort,
    "-c", $llmContextSize,
    "-np", $llmParallel,
    "--chat-template", "qwen2"
)

# Start-Process joins ArgumentList into a command line string in Windows
# PowerShell, so quote values that may contain spaces.
$llamaArgs[1] = '"' + $llamaArgs[1] + '"'

if ($env:CHIPVERIFY_LLM_API_KEY) {
    $llamaArgs += @("--api-key", $env:CHIPVERIFY_LLM_API_KEY)
}

Write-Host "Starting llama.cpp server on $llmHost`:$llmPort"
$llamaProcess = Start-Process -FilePath $llamaBin -ArgumentList $llamaArgs -PassThru -NoNewWindow -RedirectStandardOutput $llamaStdOut -RedirectStandardError $llamaStdErr

$ready = $false
$runtimeExitCode = $null
$healthUrl = "http://$llmHost`:$llmPort/health"
for ($i = 0; $i -lt 90; $i++) {
    if ($llamaProcess -and $llamaProcess.HasExited) {
        $runtimeExitCode = $llamaProcess.ExitCode
        break
    }

    try {
        $healthResponse = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        if ($healthResponse.status -eq "ok") {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}

if (-not $ready) {
    if ($llamaProcess -and -not $llamaProcess.HasExited) {
        Stop-Process -Id $llamaProcess.Id -Force
    }

    Write-Host "Local runtime did not become ready in time: $healthUrl"
    if ($runtimeExitCode -ne $null) {
        Write-Host "llama.cpp process exited before readiness (exit_code=$runtimeExitCode)"
    }
    Write-Host "Inspect runtime logs:"
    Write-Host "- stdout: $llamaStdOut"
    Write-Host "- stderr: $llamaStdErr"
    Write-Host "---- llama-server.stderr (tail) ----"
    Write-RedactedTail -Path $llamaStdErr -TailLines 60
    Write-Host "-----------------------------------"

    throw "Local runtime did not become ready in time: $healthUrl"
}

$modelAlias = if ($env:CHIPVERIFY_LLM_MODEL_ALIAS) { $env:CHIPVERIFY_LLM_MODEL_ALIAS } else { Split-Path $ggufPath -Leaf }
$env:CHIPVERIFY_LLM_PROVIDER = "local"
$env:CHIPVERIFY_LLM_BASE_URL = "http://$llmHost`:$llmPort/v1"
$env:CHIPVERIFY_LLM_MODEL_ALIAS = $modelAlias
$env:CHIPVERIFY_LLM_API_KEY_REQUIRED = if ($llmApiKeyRequired) { "true" } else { "false" }

Push-Location $backendDir
try {
    & $pythonBin -m uvicorn main:app --host $backendHost --port $backendPort
} finally {
    Pop-Location
    if ($llamaProcess -and -not $llamaProcess.HasExited) {
        Write-Host "Stopping llama.cpp server (pid=$($llamaProcess.Id))"
        Stop-Process -Id $llamaProcess.Id -Force
    }
}
