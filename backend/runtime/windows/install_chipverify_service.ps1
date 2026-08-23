param(
    [string]$ServiceName = "ChipVerifyRuntime",
    [string]$BackendRoot = "C:\chipverify\backend",
    [string]$NssmExe = "nssm.exe",
    [ValidateSet("runtime-script", "backend-exe")]
    [string]$Mode = "runtime-script",
    [string]$BackendExePath = "",
    [string]$RuntimeEnvFile = "",
    [switch]$SkipEnvInjection,
    [switch]$SkipPortAvailabilityCheck,
    [switch]$ForceReinstall
)

$ErrorActionPreference = "Stop"

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

function New-OrderedMap() {
    return [System.Collections.Specialized.OrderedDictionary]::new()
}

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

function Test-IsLoopbackHost([string]$HostValue) {
    if ([string]::IsNullOrWhiteSpace($HostValue)) {
        return $false
    }

    switch ($HostValue.Trim().ToLowerInvariant()) {
        "127.0.0.1" { return $true }
        "localhost" { return $true }
        "::1" { return $true }
        "[::1]" { return $true }
        default { return $false }
    }
}

function Assert-AbsoluteHttpUrl([string]$RawValue, [string]$KeyName) {
    if ([string]::IsNullOrWhiteSpace($RawValue)) {
        throw "$KeyName must be a non-empty http(s) URL."
    }

    $uri = $null
    if (-not [Uri]::TryCreate($RawValue.Trim(), [UriKind]::Absolute, [ref]$uri)) {
        throw "$KeyName must be an absolute URL. Received '$RawValue'."
    }

    if ($uri.Scheme -ne "http" -and $uri.Scheme -ne "https") {
        throw "$KeyName must use http or https. Received '$RawValue'."
    }

    return $uri.AbsoluteUri.TrimEnd('/')
}

function Test-IsSecretKey([string]$KeyName) {
    if ([string]::IsNullOrWhiteSpace($KeyName)) {
        return $false
    }

    $upper = $KeyName.ToUpperInvariant()
    return (
        $upper.Contains("KEY") -or
        $upper.Contains("TOKEN") -or
        $upper.Contains("SECRET") -or
        $upper.Contains("PASSWORD") -or
        $upper.Contains("PASS") -or
        $upper.Contains("CREDENTIAL")
    )
}

function Read-KeyValueEnvFile([string]$Path) {
    $map = New-OrderedMap
    $lineNumber = 0

    foreach ($rawLine in Get-Content -Path $Path -ErrorAction Stop) {
        $lineNumber += 1
        $line = $rawLine.Trim()

        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            continue
        }

        if ($line.StartsWith("export ")) {
            $line = $line.Substring(7).Trim()
        }

        $separatorIndex = $line.IndexOf("=")
        if ($separatorIndex -lt 1) {
            throw "Invalid environment line at '${Path}:$lineNumber': '$rawLine'"
        }

        $key = $line.Substring(0, $separatorIndex).Trim()
        $value = $line.Substring($separatorIndex + 1)

        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        if ($map.Contains($key)) {
            $map[$key] = $value
        } else {
            $map.Add($key, $value)
        }
    }

    return $map
}

function Add-EnvValueIfPresent([System.Collections.Specialized.OrderedDictionary]$Map, [string]$KeyName) {
    if ($Map.Contains($KeyName)) {
        return
    }

    $value = [Environment]::GetEnvironmentVariable($KeyName, "Process")
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        $Map.Add($KeyName, $value)
    }
}

function Add-DefaultIfMissing([System.Collections.Specialized.OrderedDictionary]$Map, [string]$KeyName, [string]$Value) {
    if ($Map.Contains($KeyName)) {
        return
    }

    $Map.Add($KeyName, $Value)
}

function Assert-RequiredKeys([System.Collections.Specialized.OrderedDictionary]$Map, [string[]]$RequiredKeys) {
    $missing = @()
    foreach ($key in $RequiredKeys) {
        if (-not $Map.Contains($key) -or [string]::IsNullOrWhiteSpace([string]$Map[$key])) {
            $missing += $key
        }
    }

    if ($missing.Count -gt 0) {
        throw "Missing required runtime values for service install: $($missing -join ', '). Provide them via -RuntimeEnvFile or current process environment."
    }
}

function Get-PortValue([System.Collections.Specialized.OrderedDictionary]$Map, [string]$KeyName, [int]$DefaultValue) {
    $raw = if ($Map.Contains($KeyName)) { [string]$Map[$KeyName] } else { "$DefaultValue" }
    [int]$value = 0
    if (-not [int]::TryParse($raw, [ref]$value)) {
        throw "$KeyName must be an integer port. Received '$raw'."
    }

    if ($value -lt 1 -or $value -gt 65535) {
        throw "$KeyName must be between 1 and 65535. Received '$value'."
    }

    return $value
}

function Test-PortInUse([int]$Port) {
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $false
    } catch {
        return $true
    } finally {
        if ($listener) {
            $listener.Stop()
        }
    }
}

function Invoke-Nssm([string[]]$Arguments, [switch]$IgnoreFailure) {
    & $NssmExe @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0 -and -not $IgnoreFailure) {
        throw "nssm command failed (exit_code=$LASTEXITCODE): nssm $($Arguments -join ' ')"
    }
}

if (-not (Test-Path $BackendRoot)) {
    throw "BackendRoot does not exist: '$BackendRoot'"
}

$BackendRoot = (Resolve-Path $BackendRoot).Path

$resolvedNssm = Resolve-ExecutablePath $NssmExe
if (-not $resolvedNssm) {
    throw "NSSM is required for this template. Install NSSM and ensure nssm.exe is on PATH."
}
$NssmExe = $resolvedNssm

$runtimeScript = Join-Path $BackendRoot "runtime\start_runtime_windows.ps1"
if ($Mode -eq "runtime-script" -and -not (Test-Path $runtimeScript)) {
    throw "Runtime script not found at '$runtimeScript'"
}

$serviceExists = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($serviceExists -and -not $ForceReinstall) {
    throw "Service '$ServiceName' already exists. Re-run with -ForceReinstall to replace it."
}

if ($ForceReinstall -and $serviceExists) {
    Invoke-Nssm @("stop", $ServiceName) -IgnoreFailure
    Invoke-Nssm @("remove", $ServiceName, "confirm") -IgnoreFailure
    Start-Sleep -Seconds 1
}

$serviceEnv = New-OrderedMap

if ([string]::IsNullOrWhiteSpace($RuntimeEnvFile)) {
    $defaultEnvFile = Join-Path $BackendRoot "runtime\windows\runtime.env"
    if (Test-Path $defaultEnvFile) {
        $RuntimeEnvFile = $defaultEnvFile
    }
}

if (-not [string]::IsNullOrWhiteSpace($RuntimeEnvFile)) {
    if (-not (Test-Path $RuntimeEnvFile)) {
        throw "Runtime env file not found: '$RuntimeEnvFile'"
    }

    $RuntimeEnvFile = (Resolve-Path $RuntimeEnvFile).Path
    $fromFile = Read-KeyValueEnvFile $RuntimeEnvFile
    foreach ($entry in $fromFile.GetEnumerator()) {
        if ($serviceEnv.Contains($entry.Key)) {
            $serviceEnv[$entry.Key] = $entry.Value
        } else {
            $serviceEnv.Add($entry.Key, $entry.Value)
        }
    }
}

$knownEnvKeys = @(
    "CHIPVERIFY_GGUF_PATH",
    "CHIPVERIFY_SECRET_KEY",
    "CHIPVERIFY_LLM_API_KEY",
    "CHIPVERIFY_LLM_PROVIDER",
    "CHIPVERIFY_LLM_BASE_URL",
    "CHIPVERIFY_LLAMACPP_BIN",
    "CHIPVERIFY_PYTHON_BIN",
    "CHIPVERIFY_LLM_BIND_HOST",
    "CHIPVERIFY_LLM_PORT",
    "CHIPVERIFY_LLM_CONTEXT_SIZE",
    "CHIPVERIFY_LLM_PARALLEL",
    "CHIPVERIFY_BACKEND_HOST",
    "CHIPVERIFY_BACKEND_PORT",
    "CHIPVERIFY_ALLOWED_ORIGINS",
    "CHIPVERIFY_ALLOW_PUBLIC_BIND",
    "CHIPVERIFY_LLM_API_KEY_REQUIRED",
    "CHIPVERIFY_LLM_MODEL_ALIAS",
    "MODEL_PROVIDER",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_BASE_URL",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_MODEL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_USE_AAD",
    "AZURE_OPENAI_TOKEN_SCOPE",
    "AZURE_OPENAI_AUTH_HEADER",
    "AZURE_OPENAI_API_STYLE",
    "AZURE_OPENAI_API_VERSION",
    "CHIPVERIFY_LOG_DIR",
    "CHIPVERIFY_ARTIFACT_MANIFEST_PATH"
)

foreach ($key in $knownEnvKeys) {
    Add-EnvValueIfPresent -Map $serviceEnv -KeyName $key
}

# Keep service startup deterministic even if launcher defaults change later.
Add-DefaultIfMissing -Map $serviceEnv -KeyName "CHIPVERIFY_LLM_BIND_HOST" -Value "127.0.0.1"
Add-DefaultIfMissing -Map $serviceEnv -KeyName "CHIPVERIFY_LLM_PORT" -Value "7349"
Add-DefaultIfMissing -Map $serviceEnv -KeyName "CHIPVERIFY_BACKEND_HOST" -Value "127.0.0.1"
Add-DefaultIfMissing -Map $serviceEnv -KeyName "CHIPVERIFY_BACKEND_PORT" -Value "7348"
Add-DefaultIfMissing -Map $serviceEnv -KeyName "CHIPVERIFY_LLM_PROVIDER" -Value "local"
Add-DefaultIfMissing -Map $serviceEnv -KeyName "CHIPVERIFY_LLM_BASE_URL" -Value "http://127.0.0.1:7349/v1"
Add-DefaultIfMissing -Map $serviceEnv -KeyName "CHIPVERIFY_ALLOW_PUBLIC_BIND" -Value "false"
Add-DefaultIfMissing -Map $serviceEnv -KeyName "CHIPVERIFY_LLM_API_KEY_REQUIRED" -Value "true"

if ($Mode -eq "runtime-script") {
    Assert-RequiredKeys -Map $serviceEnv -RequiredKeys @(
        "CHIPVERIFY_GGUF_PATH",
        "CHIPVERIFY_SECRET_KEY",
        "CHIPVERIFY_LLM_API_KEY"
    )

    $ggufPath = [string]$serviceEnv["CHIPVERIFY_GGUF_PATH"]
    if (-not (Test-Path $ggufPath)) {
        throw "CHIPVERIFY_GGUF_PATH does not exist: '$ggufPath'"
    }

    foreach ($exeKey in @("CHIPVERIFY_LLAMACPP_BIN", "CHIPVERIFY_PYTHON_BIN")) {
        if ($serviceEnv.Contains($exeKey) -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv[$exeKey])) {
            $resolvedExe = Resolve-ExecutablePath ([string]$serviceEnv[$exeKey])
            if (-not $resolvedExe) {
                throw "$exeKey points to a non-existent executable: '$($serviceEnv[$exeKey])'"
            }
            $serviceEnv[$exeKey] = $resolvedExe
        }
    }

    $backendPort = Get-PortValue -Map $serviceEnv -KeyName "CHIPVERIFY_BACKEND_PORT" -DefaultValue 7348
    $llmPort = Get-PortValue -Map $serviceEnv -KeyName "CHIPVERIFY_LLM_PORT" -DefaultValue 7349

    if ($backendPort -eq $llmPort) {
        throw "Port collision in configuration: backend and model runtime both use '$backendPort'."
    }

    if (-not $SkipPortAvailabilityCheck) {
        $inUse = @()
        if (Test-PortInUse -Port $backendPort) {
            $inUse += "backend:$backendPort"
        }
        if (Test-PortInUse -Port $llmPort) {
            $inUse += "model:$llmPort"
        }

        if ($inUse.Count -gt 0) {
            throw "Port collision detected on localhost for $($inUse -join ', '). Stop conflicting processes or re-run with -SkipPortAvailabilityCheck if intentional."
        }
    }
} else {
    Assert-RequiredKeys -Map $serviceEnv -RequiredKeys @("CHIPVERIFY_SECRET_KEY")

    Add-DefaultIfMissing -Map $serviceEnv -KeyName "CHIPVERIFY_LLM_MODEL_ALIAS" -Value "chipix-v0.1"

    $backendPort = Get-PortValue -Map $serviceEnv -KeyName "CHIPVERIFY_BACKEND_PORT" -DefaultValue 7348
    if (-not $SkipPortAvailabilityCheck) {
        if (Test-PortInUse -Port $backendPort) {
            throw "Port collision detected on localhost for backend:$backendPort. Stop conflicting processes or re-run with -SkipPortAvailabilityCheck if intentional."
        }
    }

    $apiKeyRequired = Convert-ToBoolean ([string]$serviceEnv["CHIPVERIFY_LLM_API_KEY_REQUIRED"])
    $serviceEnv["CHIPVERIFY_LLM_API_KEY_REQUIRED"] = if ($apiKeyRequired) { "true" } else { "false" }

    $provider = [string]$serviceEnv["CHIPVERIFY_LLM_PROVIDER"]
    if ([string]::IsNullOrWhiteSpace($provider)) {
        $provider = "local"
    }
    $provider = $provider.Trim().ToLowerInvariant()
    $serviceEnv["CHIPVERIFY_LLM_PROVIDER"] = $provider

    if ($provider -eq "azure_openai") {
        if ($serviceEnv.Contains("AZURE_OPENAI_ENDPOINT") -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv["AZURE_OPENAI_ENDPOINT"])) {
            $serviceEnv["CHIPVERIFY_LLM_BASE_URL"] = [string]$serviceEnv["AZURE_OPENAI_ENDPOINT"]
        }
        if ($serviceEnv.Contains("AZURE_OPENAI_DEPLOYMENT") -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv["AZURE_OPENAI_DEPLOYMENT"])) {
            $serviceEnv["CHIPVERIFY_LLM_MODEL_ALIAS"] = [string]$serviceEnv["AZURE_OPENAI_DEPLOYMENT"]
        }

        $llmBaseUrl = Assert-AbsoluteHttpUrl -RawValue ([string]$serviceEnv["CHIPVERIFY_LLM_BASE_URL"]) -KeyName "CHIPVERIFY_LLM_BASE_URL"
        $serviceEnv["CHIPVERIFY_LLM_BASE_URL"] = $llmBaseUrl
        Assert-RequiredKeys -Map $serviceEnv -RequiredKeys @("CHIPVERIFY_LLM_MODEL_ALIAS")

        $useAad = $false
        if ($serviceEnv.Contains("AZURE_OPENAI_USE_AAD")) {
            $useAad = Convert-ToBoolean ([string]$serviceEnv["AZURE_OPENAI_USE_AAD"])
            $serviceEnv["AZURE_OPENAI_USE_AAD"] = if ($useAad) { "true" } else { "false" }
        }
        if ($apiKeyRequired -and -not $useAad) {
            $hasAzureKey = $serviceEnv.Contains("AZURE_OPENAI_API_KEY") -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv["AZURE_OPENAI_API_KEY"])
            $hasRuntimeKey = $serviceEnv.Contains("CHIPVERIFY_LLM_API_KEY") -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv["CHIPVERIFY_LLM_API_KEY"])
            if (-not ($hasAzureKey -or $hasRuntimeKey)) {
                throw "azure_openai provider requires AZURE_OPENAI_API_KEY or CHIPVERIFY_LLM_API_KEY unless AZURE_OPENAI_USE_AAD=true."
            }
        }
    } elseif ($provider -eq "openai") {
        if ($serviceEnv.Contains("OPENAI_API_BASE") -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv["OPENAI_API_BASE"])) {
            $serviceEnv["CHIPVERIFY_LLM_BASE_URL"] = [string]$serviceEnv["OPENAI_API_BASE"]
        } elseif ($serviceEnv.Contains("OPENAI_BASE_URL") -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv["OPENAI_BASE_URL"])) {
            $serviceEnv["CHIPVERIFY_LLM_BASE_URL"] = [string]$serviceEnv["OPENAI_BASE_URL"]
        } elseif ([string]::IsNullOrWhiteSpace([string]$serviceEnv["CHIPVERIFY_LLM_BASE_URL"]) -or [string]$serviceEnv["CHIPVERIFY_LLM_BASE_URL"] -eq "http://127.0.0.1:7349/v1") {
            $serviceEnv["CHIPVERIFY_LLM_BASE_URL"] = "https://api.openai.com/v1"
        }

        if ($serviceEnv.Contains("OPENAI_MODEL") -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv["OPENAI_MODEL"])) {
            $serviceEnv["CHIPVERIFY_LLM_MODEL_ALIAS"] = [string]$serviceEnv["OPENAI_MODEL"]
        } elseif ([string]::IsNullOrWhiteSpace([string]$serviceEnv["CHIPVERIFY_LLM_MODEL_ALIAS"]) -or [string]$serviceEnv["CHIPVERIFY_LLM_MODEL_ALIAS"] -eq "chipix-v0.1" -or [string]$serviceEnv["CHIPVERIFY_LLM_MODEL_ALIAS"] -like "*gemini*") {
            $serviceEnv["CHIPVERIFY_LLM_MODEL_ALIAS"] = "gpt-5.4"
        }

        $llmBaseUrl = Assert-AbsoluteHttpUrl -RawValue ([string]$serviceEnv["CHIPVERIFY_LLM_BASE_URL"]) -KeyName "CHIPVERIFY_LLM_BASE_URL"
        $serviceEnv["CHIPVERIFY_LLM_BASE_URL"] = $llmBaseUrl
        $serviceEnv["OPENAI_API_BASE"] = $llmBaseUrl
        Assert-RequiredKeys -Map $serviceEnv -RequiredKeys @("CHIPVERIFY_LLM_MODEL_ALIAS")

        if ($serviceEnv.Contains("OPENAI_API_KEY") -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv["OPENAI_API_KEY"])) {
            $serviceEnv["CHIPVERIFY_LLM_API_KEY"] = [string]$serviceEnv["OPENAI_API_KEY"]
        }
        if ($apiKeyRequired) {
            Assert-RequiredKeys -Map $serviceEnv -RequiredKeys @("CHIPVERIFY_LLM_API_KEY")
        }
    } elseif ($provider -eq "nim") {
        if ($serviceEnv.Contains("NIM_API_BASE") -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv["NIM_API_BASE"])) {
            $serviceEnv["CHIPVERIFY_LLM_BASE_URL"] = [string]$serviceEnv["NIM_API_BASE"]
        } elseif ([string]::IsNullOrWhiteSpace([string]$serviceEnv["CHIPVERIFY_LLM_BASE_URL"]) -or [string]$serviceEnv["CHIPVERIFY_LLM_BASE_URL"] -eq "http://127.0.0.1:7349/v1") {
            $serviceEnv["CHIPVERIFY_LLM_BASE_URL"] = "https://integrate.api.nvidia.com/v1"
        }
        if ($serviceEnv.Contains("NIM_MODEL") -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv["NIM_MODEL"])) {
            $serviceEnv["CHIPVERIFY_LLM_MODEL_ALIAS"] = [string]$serviceEnv["NIM_MODEL"]
        } elseif ([string]::IsNullOrWhiteSpace([string]$serviceEnv["CHIPVERIFY_LLM_MODEL_ALIAS"]) -or [string]$serviceEnv["CHIPVERIFY_LLM_MODEL_ALIAS"] -eq "chipix-v0.1") {
            $serviceEnv["CHIPVERIFY_LLM_MODEL_ALIAS"] = "meta/llama-3.1-70b-instruct"
        }

        $llmBaseUrl = Assert-AbsoluteHttpUrl -RawValue ([string]$serviceEnv["CHIPVERIFY_LLM_BASE_URL"]) -KeyName "CHIPVERIFY_LLM_BASE_URL"
        $serviceEnv["CHIPVERIFY_LLM_BASE_URL"] = $llmBaseUrl
        Assert-RequiredKeys -Map $serviceEnv -RequiredKeys @("CHIPVERIFY_LLM_MODEL_ALIAS")

        if ($serviceEnv.Contains("NIM_API_KEY") -and -not [string]::IsNullOrWhiteSpace([string]$serviceEnv["NIM_API_KEY"])) {
            $serviceEnv["CHIPVERIFY_LLM_API_KEY"] = [string]$serviceEnv["NIM_API_KEY"]
        }
        if ($apiKeyRequired) {
            Assert-RequiredKeys -Map $serviceEnv -RequiredKeys @("CHIPVERIFY_LLM_API_KEY")
        }
    } elseif ($provider -eq "local") {
        $llmBaseUrl = Assert-AbsoluteHttpUrl -RawValue ([string]$serviceEnv["CHIPVERIFY_LLM_BASE_URL"]) -KeyName "CHIPVERIFY_LLM_BASE_URL"
        $serviceEnv["CHIPVERIFY_LLM_BASE_URL"] = $llmBaseUrl

        Assert-RequiredKeys -Map $serviceEnv -RequiredKeys @("CHIPVERIFY_LLM_MODEL_ALIAS")
        if ($apiKeyRequired) {
            Assert-RequiredKeys -Map $serviceEnv -RequiredKeys @("CHIPVERIFY_LLM_API_KEY")
        }

        $llmUri = [Uri]$llmBaseUrl
        $llmPath = $llmUri.AbsolutePath.TrimEnd('/').ToLowerInvariant()
        if ($llmPath -ne "/v1") {
            throw "CHIPVERIFY_LLM_BASE_URL must include the OpenAI-compatible /v1 path in local mode. Received '$llmBaseUrl'."
        }

        if (Test-IsLoopbackHost $llmUri.Host) {
            $llmPort = if ($llmUri.IsDefaultPort) {
                if ($llmUri.Scheme -eq "https") { 443 } else { 80 }
            } else {
                $llmUri.Port
            }

            if ($llmPort -eq $backendPort) {
                throw "Port collision in configuration: CHIPVERIFY_LLM_BASE_URL and CHIPVERIFY_BACKEND_PORT both resolve to '$backendPort' on localhost."
            }
        }
    }
}

$serviceCommand = "powershell.exe"
$serviceArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$runtimeScript`""

if ($Mode -eq "backend-exe") {
    if ([string]::IsNullOrWhiteSpace($BackendExePath)) {
        $BackendExePath = Join-Path $BackendRoot "runtime\windows\build\dist\chipverify-backend\chipverify-backend.exe"
    }

    if (-not (Test-Path $BackendExePath)) {
        throw "Backend EXE not found at '$BackendExePath'. Build it first with runtime/windows/build_backend_exe.ps1 or pass -BackendExePath explicitly."
    }

    $serviceCommand = (Resolve-Path $BackendExePath).Path
    $serviceArgs = ""
}

$logDir = Join-Path $BackendRoot "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

if ([string]::IsNullOrWhiteSpace($serviceArgs)) {
    Invoke-Nssm @("install", $ServiceName, $serviceCommand)
} else {
    Invoke-Nssm @("install", $ServiceName, $serviceCommand, $serviceArgs)
}
Invoke-Nssm @("set", $ServiceName, "AppDirectory", $BackendRoot)
Invoke-Nssm @("set", $ServiceName, "Start", "SERVICE_AUTO_START")
Invoke-Nssm @("set", $ServiceName, "AppStdout", (Join-Path $logDir "$ServiceName.stdout.log"))
Invoke-Nssm @("set", $ServiceName, "AppStderr", (Join-Path $logDir "$ServiceName.stderr.log"))
Invoke-Nssm @("set", $ServiceName, "AppRotateFiles", "1") -IgnoreFailure
Invoke-Nssm @("set", $ServiceName, "AppRotateOnline", "1") -IgnoreFailure

# Restart policy: always restart the process if it exits.
Invoke-Nssm @("set", $ServiceName, "AppExit", "Default", "Restart")
Invoke-Nssm @("set", $ServiceName, "AppRestartDelay", "5000")

if (-not $SkipEnvInjection) {
    if ($serviceEnv.Count -eq 0) {
        throw "No runtime environment values were resolved. Provide -RuntimeEnvFile or set required CHIPVERIFY_* values in current process env."
    }

    $envLines = @(
        $serviceEnv.GetEnumerator() |
            Sort-Object Key |
            ForEach-Object { "$($_.Key)=$($_.Value)" }
    )
    $envBlock = [string]::Join("`n", $envLines)
    Invoke-Nssm @("set", $ServiceName, "AppEnvironmentExtra", $envBlock)
}

Write-Host "Installed service '$ServiceName'."
Write-Host "Mode: $Mode"
Write-Host "Command: $serviceCommand $serviceArgs"
if ($RuntimeEnvFile) {
    Write-Host "Runtime env file: $RuntimeEnvFile"
}
if ($SkipEnvInjection) {
    Write-Host "Environment injection skipped. Ensure service-level CHIPVERIFY_* variables are configured separately."
} else {
    Write-Host "Injected service environment keys (secret values redacted):"
    foreach ($entry in ($serviceEnv.GetEnumerator() | Sort-Object Key)) {
        $value = [string]$entry.Value
        if (Test-IsSecretKey $entry.Key) {
            $value = "<redacted>"
        }
        Write-Host "- $($entry.Key)=$value"
    }
}

$resolvedBackendHost = [string]$serviceEnv["CHIPVERIFY_BACKEND_HOST"]
$resolvedBackendPort = [string]$serviceEnv["CHIPVERIFY_BACKEND_PORT"]
Write-Host "Validated backend endpoint: http://$resolvedBackendHost`:$resolvedBackendPort"

if ($Mode -eq "backend-exe") {
    Write-Host "Backend EXE mode expects a separately managed model runtime at: $([string]$serviceEnv["CHIPVERIFY_LLM_BASE_URL"])"
}

Write-Host "Desktop policy hint: set CHIPVERIFY_BACKEND_URL=http://$resolvedBackendHost`:$resolvedBackendPort"
Write-Host "Use 'nssm start $ServiceName' to start and 'nssm stop $ServiceName' to stop."
