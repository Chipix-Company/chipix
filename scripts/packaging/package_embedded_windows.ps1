param(
    [ValidateSet("nsis", "dir")]
    [string]$ElectronTarget = "nsis"
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

function Set-EnvValue([string]$EnvPath, [string]$Key, [string]$Value) {
    $line = "$Key=$Value"
    if (-not (Test-Path $EnvPath)) {
        New-Item -ItemType File -Path $EnvPath -Force | Out-Null
        Set-Content -Path $EnvPath -Value $line -Encoding UTF8
        return
    }

    $pattern = "^\s*$([regex]::Escape($Key))\s*="
    $lines = @(Get-Content -Path $EnvPath -ErrorAction SilentlyContinue)
    $found = $false
    $updated = foreach ($item in $lines) {
        if ($item -match $pattern) {
            $found = $true
            $line
        } else {
            $item
        }
    }
    if (-not $found) {
        $updated += $line
    }
    Set-Content -Path $EnvPath -Value $updated -Encoding UTF8
}

function Remove-EnvKeys([string]$EnvPath, [string[]]$Keys) {
    if (-not (Test-Path $EnvPath)) {
        return
    }
    $escapedKeys = $Keys | ForEach-Object { [regex]::Escape($_) }
    $pattern = "^\s*($($escapedKeys -join '|'))\s*="
    $lines = @(Get-Content -Path $EnvPath -ErrorAction SilentlyContinue)
    $filtered = $lines | Where-Object { $_ -notmatch $pattern }
    Set-Content -Path $EnvPath -Value $filtered -Encoding UTF8
}

function Reset-StageRoot([string]$StageRoot) {
    if (Test-Path $StageRoot) {
        Remove-Item -Path $StageRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null
}

function Clear-StageRoot([string]$StageRoot) {
    if (Test-Path $StageRoot) {
        Remove-Item -Path $StageRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $StageRoot ".gitkeep") -Force | Out-Null
}

function New-PackagingRequirementsFile([string]$SourcePath, [string]$OutputPath) {
    $sourceDir = Split-Path -Parent (Resolve-Path $SourcePath).Path
    $filtered = New-Object System.Collections.Generic.List[string]

    foreach ($line in Get-Content -Path $SourcePath) {
        if ($line -match "^\s*-e\s+(.+?)\s*$") {
            $editablePath = $Matches[1].Trim().Trim("'`"")
            $resolvedEditablePath = if ([System.IO.Path]::IsPathRooted($editablePath)) {
                $editablePath
            } else {
                Join-Path $sourceDir $editablePath
            }

            $isInstallableLocalPackage = (
                (Test-Path $resolvedEditablePath) -and
                (
                    (Test-Path (Join-Path $resolvedEditablePath "pyproject.toml")) -or
                    (Test-Path (Join-Path $resolvedEditablePath "setup.py")) -or
                    (Test-Path (Join-Path $resolvedEditablePath "setup.cfg"))
                )
            )

            if (-not $isInstallableLocalPackage) {
                Write-Warning "Skipping missing or non-installable editable requirement for packaging: $line"
                continue
            }
        }
        $filtered.Add($line)
    }

    Set-Content -Path $OutputPath -Value $filtered -Encoding UTF8
    return $OutputPath
}

Push-Location $repoRoot
try {
    Write-Host "[1/5] Preparing isolated Python packaging environment..." -ForegroundColor Cyan
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
    $packagingRequirements = New-PackagingRequirementsFile `
        -SourcePath ".\backend\requirements.txt" `
        -OutputPath (Join-Path $repoRoot ".packaging\requirements-packaging.txt")
    & $venvPython -m pip install -r $packagingRequirements pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install backend packaging dependencies."
    }

    Write-Host "[2/5] Building backend EXE with PyInstaller..." -ForegroundColor Cyan
    $backendBuildRoot = Join-Path $repoRoot "backend\runtime\windows\build"
    $backendRoot = Join-Path $repoRoot "backend"
    & powershell -NoProfile -ExecutionPolicy Bypass -File ".\backend\runtime\windows\build_backend_exe.ps1" -BackendRoot $backendRoot -OutputRoot $backendBuildRoot -PythonExe $venvPython
    if ($LASTEXITCODE -ne 0) {
        throw "Backend EXE build failed."
    }

    Write-Host "[3/5] Staging backend runtime as Electron extraResources..." -ForegroundColor Cyan
    $stageRoot = Join-Path $repoRoot "build-resources\runtime"
    Reset-StageRoot $stageRoot

    $stageBackend = Join-Path $stageRoot "backend"
    $stageBackendSource = Join-Path $stageRoot "backend_source"
    $stageBin = Join-Path $stageRoot "bin"
    New-Item -ItemType Directory -Path $stageBackend -Force | Out-Null
    New-Item -ItemType Directory -Path $stageBackendSource -Force | Out-Null
    New-Item -ItemType Directory -Path $stageBin -Force | Out-Null

    $backendDistDir = Join-Path $backendBuildRoot "dist\chipverify-backend"
    $backendExe = Join-Path $backendDistDir "chipverify-backend.exe"
    if (-not (Test-Path $backendExe)) {
        throw "Backend EXE missing: $backendExe"
    }
    Copy-Item -Path (Join-Path $backendDistDir "*") -Destination $stageBackend -Recurse -Force

    Write-Host "Staging backend source fallback..." -ForegroundColor Cyan
    $sourceFallbackExcludeDirs = @(
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "runtime",
        "logs",
        "tests",
        "benchmarks",
        (Join-Path $backendRoot "__pycache__"),
        (Join-Path $backendRoot ".pytest_cache"),
        (Join-Path $backendRoot ".mypy_cache"),
        (Join-Path $backendRoot "runtime"),
        (Join-Path $backendRoot "logs"),
        (Join-Path $backendRoot "tests"),
        (Join-Path $backendRoot "benchmarks")
    )
    $sourceFallbackExcludeFiles = @(
        "*.pyc",
        "*.pyo",
        "*.log",
        "*.db",
        "*.db-*",
        ".env",
        ".sys_id",
        "parser.out",
        "parsetab.py",
        "_ws_*.py"
    )
    $robocopyArgs = @(
        $backendRoot,
        $stageBackendSource,
        "/MIR",
        "/XD"
    )
    $robocopyArgs += $sourceFallbackExcludeDirs
    $robocopyArgs += "/XF"
    $robocopyArgs += $sourceFallbackExcludeFiles
    & robocopy @robocopyArgs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "Failed to stage backend source fallback (robocopy exit_code=$LASTEXITCODE)."
    }
    $global:LASTEXITCODE = 0

    $unexpectedFallbackRuntime = Join-Path $stageBackendSource "runtime"
    if (Test-Path $unexpectedFallbackRuntime) {
        Remove-Item -LiteralPath $unexpectedFallbackRuntime -Recurse -Force
    }

    $sourceEnv = Join-Path $repoRoot "backend\.env"
    $stagedEnv = Join-Path $stageBackend ".env"
    if (Test-Path $sourceEnv) {
        Copy-Item -Path $sourceEnv -Destination $stagedEnv -Force
    } else {
        New-Item -ItemType File -Path $stagedEnv -Force | Out-Null
    }
    Remove-EnvKeys -EnvPath $stagedEnv -Keys @(
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_GENERATIVE_AI_API_KEY",
        "OPENAI_API_KEY",
        "CHIPVERIFY_OPENAI_API_KEY",
        "CHIPVERIFY_LLM_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "NIM_API_KEY"
    )

    Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_CLOUD_CONTROL_DISABLED" -Value "true"
    Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_PROVIDER" -Value "bedrock"
    Set-EnvValue -EnvPath $stagedEnv -Key "MODEL_PROVIDER" -Value "bedrock"
    Set-EnvValue -EnvPath $stagedEnv -Key "BEDROCK_REGION" -Value "us-east-1"
    Set-EnvValue -EnvPath $stagedEnv -Key "BEDROCK_MODEL" -Value "deepseek.v3.2"
    Set-EnvValue -EnvPath $stagedEnv -Key "MODEL_NAME" -Value "deepseek.v3.2"
    Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_MODEL_ALIAS" -Value "deepseek.v3.2"
    Set-EnvValue -EnvPath $stagedEnv -Key "BEDROCK_API_BASE" -Value "https://bedrock-mantle.us-east-1.api.aws/v1"
    Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_BASE_URL" -Value "https://bedrock-mantle.us-east-1.api.aws/v1"
    Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_COMPLETION_PROVIDER" -Value "bedrock"
    Set-EnvValue -EnvPath $stagedEnv -Key "BEDROCK_COMPLETION_MODEL" -Value "deepseek.v3.2"
    Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_COMPLETION_MODEL" -Value "deepseek.v3.2"
    Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_API_KEY_REQUIRED" -Value "false"

    if ($env:CHIPVERIFY_SECRET_KEY) {
        Append-EnvIfMissing -EnvPath $stagedEnv -Key "CHIPVERIFY_SECRET_KEY" -Value $env:CHIPVERIFY_SECRET_KEY
    } else {
        $generatedSecret = ([guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
        Append-EnvIfMissing -EnvPath $stagedEnv -Key "CHIPVERIFY_SECRET_KEY" -Value $generatedSecret
    }

    Append-EnvIfMissing -EnvPath $stagedEnv -Key "CHIPVERIFY_REQUIRE_ACTIVATION" -Value "true"
    Append-EnvIfMissing -EnvPath $stagedEnv -Key "CHIPVERIFY_BACKEND_HOST" -Value "127.0.0.1"
    Append-EnvIfMissing -EnvPath $stagedEnv -Key "CHIPVERIFY_BACKEND_PORT" -Value "7348"
    $demoProvider = $env:CHIPVERIFY_LLM_PROVIDER
    if ([string]::IsNullOrWhiteSpace($demoProvider)) {
        $demoProvider = $env:MODEL_PROVIDER
    }
    if ([string]::IsNullOrWhiteSpace($demoProvider)) {
        $demoProvider = "bedrock"
    }
    $demoProvider = $demoProvider.Trim().ToLowerInvariant().Replace("-", "_")
    if ($demoProvider -eq "openai_sdk" -or $demoProvider -eq "chatgpt") {
        $demoProvider = "openai"
    }

    Set-EnvValue -EnvPath $stagedEnv -Key "MODEL_PROVIDER" -Value $demoProvider
    Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_PROVIDER" -Value $demoProvider

    if ($demoProvider -eq "bedrock") {
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_DEMO_FORCE_GEMINI" -Value "false"
        $bedrockRegion = $env:BEDROCK_REGION
        if ([string]::IsNullOrWhiteSpace($bedrockRegion)) {
            $bedrockRegion = "us-east-1"
        }
        $bedrockModel = $env:BEDROCK_MODEL
        if ([string]::IsNullOrWhiteSpace($bedrockModel)) {
            $bedrockModel = "deepseek.v3.2"
        }
        $bedrockBase = $env:BEDROCK_API_BASE
        if ([string]::IsNullOrWhiteSpace($bedrockBase)) {
            $bedrockBase = "https://bedrock-mantle.$bedrockRegion.api.aws/v1"
        }
        $bedrockCompletionModel = $env:BEDROCK_COMPLETION_MODEL
        if ([string]::IsNullOrWhiteSpace($bedrockCompletionModel)) {
            $bedrockCompletionModel = $bedrockModel
        }

        Set-EnvValue -EnvPath $stagedEnv -Key "BEDROCK_REGION" -Value $bedrockRegion
        Set-EnvValue -EnvPath $stagedEnv -Key "BEDROCK_MODEL" -Value $bedrockModel
        Set-EnvValue -EnvPath $stagedEnv -Key "MODEL_NAME" -Value $bedrockModel
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_MODEL_ALIAS" -Value $bedrockModel
        Set-EnvValue -EnvPath $stagedEnv -Key "BEDROCK_API_BASE" -Value $bedrockBase
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_BASE_URL" -Value $bedrockBase
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_COMPLETION_PROVIDER" -Value "bedrock"
        Set-EnvValue -EnvPath $stagedEnv -Key "BEDROCK_COMPLETION_MODEL" -Value $bedrockCompletionModel
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_COMPLETION_MODEL" -Value $bedrockCompletionModel

        $demoBedrockApiKey = $env:CHIPVERIFY_DEMO_BEDROCK_API_KEY
        if ([string]::IsNullOrWhiteSpace($demoBedrockApiKey)) {
            $demoBedrockApiKey = $env:BEDROCK_API_KEY
        }
        if ([string]::IsNullOrWhiteSpace($demoBedrockApiKey)) {
            $demoBedrockApiKey = $env:AWS_BEARER_TOKEN_BEDROCK
        }
        if (-not [string]::IsNullOrWhiteSpace($demoBedrockApiKey)) {
            Set-EnvValue -EnvPath $stagedEnv -Key "BEDROCK_API_KEY" -Value $demoBedrockApiKey
        }
    } elseif ($demoProvider -eq "openai") {
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_DEMO_FORCE_GEMINI" -Value "false"
        $openAiModel = $env:OPENAI_MODEL
        if ([string]::IsNullOrWhiteSpace($openAiModel)) {
            $openAiModel = $env:MODEL_NAME
        }
        if ([string]::IsNullOrWhiteSpace($openAiModel) -or $openAiModel.ToLowerInvariant().Contains("gemini")) {
            $openAiModel = "gpt-5.4"
        }
        Set-EnvValue -EnvPath $stagedEnv -Key "MODEL_NAME" -Value $openAiModel
        Set-EnvValue -EnvPath $stagedEnv -Key "OPENAI_MODEL" -Value $openAiModel
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_MODEL_ALIAS" -Value $openAiModel

        $openAiBase = $env:OPENAI_API_BASE
        if ([string]::IsNullOrWhiteSpace($openAiBase)) {
            $openAiBase = $env:OPENAI_BASE_URL
        }
        if ([string]::IsNullOrWhiteSpace($openAiBase)) {
            $openAiBase = "https://api.openai.com/v1"
        }
        Set-EnvValue -EnvPath $stagedEnv -Key "OPENAI_API_BASE" -Value $openAiBase
        Set-EnvValue -EnvPath $stagedEnv -Key "OPENAI_BASE_URL" -Value $openAiBase
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_BASE_URL" -Value $openAiBase

        $demoOpenAiApiKey = $env:CHIPVERIFY_DEMO_OPENAI_API_KEY
        if ([string]::IsNullOrWhiteSpace($demoOpenAiApiKey)) {
            $demoOpenAiApiKey = $env:OPENAI_API_KEY
        }
        if ([string]::IsNullOrWhiteSpace($demoOpenAiApiKey)) {
            $demoOpenAiApiKey = $env:CHIPVERIFY_LLM_API_KEY
        }
        if (-not [string]::IsNullOrWhiteSpace($demoOpenAiApiKey)) {
            Set-EnvValue -EnvPath $stagedEnv -Key "OPENAI_API_KEY" -Value $demoOpenAiApiKey
            Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_OPENAI_API_KEY" -Value $demoOpenAiApiKey
            Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_API_KEY" -Value $demoOpenAiApiKey
        }
    } elseif ($demoProvider -eq "azure_openai") {
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_DEMO_FORCE_GEMINI" -Value "false"
        $azureModel = $env:AZURE_OPENAI_DEPLOYMENT
        if ([string]::IsNullOrWhiteSpace($azureModel)) {
            $azureModel = $env:AZURE_OPENAI_MODEL
        }
        if ([string]::IsNullOrWhiteSpace($azureModel)) {
            $azureModel = "gpt-5.4"
        }
        Set-EnvValue -EnvPath $stagedEnv -Key "MODEL_NAME" -Value $azureModel
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_MODEL_ALIAS" -Value $azureModel
        Set-EnvValue -EnvPath $stagedEnv -Key "AZURE_OPENAI_MODEL" -Value $azureModel
        Set-EnvValue -EnvPath $stagedEnv -Key "AZURE_OPENAI_DEPLOYMENT" -Value $azureModel

        $azureEndpoint = $env:AZURE_OPENAI_ENDPOINT
        if ([string]::IsNullOrWhiteSpace($azureEndpoint)) {
            $azureEndpoint = $env:AZURE_OPENAI_BASE_URL
        }
        if ([string]::IsNullOrWhiteSpace($azureEndpoint)) {
            $azureEndpoint = "https://chipix-resource.services.ai.azure.com/openai/v1"
        }
        if (-not [string]::IsNullOrWhiteSpace($azureEndpoint)) {
            Set-EnvValue -EnvPath $stagedEnv -Key "AZURE_OPENAI_ENDPOINT" -Value $azureEndpoint
            Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_BASE_URL" -Value $azureEndpoint
            Set-EnvValue -EnvPath $stagedEnv -Key "OPENAI_API_BASE" -Value $azureEndpoint
            Set-EnvValue -EnvPath $stagedEnv -Key "OPENAI_BASE_URL" -Value $azureEndpoint
        }

        $demoAzureApiKey = $env:CHIPVERIFY_DEMO_AZURE_OPENAI_API_KEY
        if ([string]::IsNullOrWhiteSpace($demoAzureApiKey)) {
            $demoAzureApiKey = $env:AZURE_OPENAI_API_KEY
        }
        if ([string]::IsNullOrWhiteSpace($demoAzureApiKey)) {
            $demoAzureApiKey = $env:CHIPVERIFY_LLM_API_KEY
        }
        if (-not [string]::IsNullOrWhiteSpace($demoAzureApiKey)) {
            Set-EnvValue -EnvPath $stagedEnv -Key "AZURE_OPENAI_API_KEY" -Value $demoAzureApiKey
            Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_API_KEY" -Value $demoAzureApiKey
        }
        Set-EnvValue -EnvPath $stagedEnv -Key "AZURE_OPENAI_API_STYLE" -Value "openai_v1"
        Set-EnvValue -EnvPath $stagedEnv -Key "AZURE_OPENAI_AUTH_HEADER" -Value "bearer"
        Set-EnvValue -EnvPath $stagedEnv -Key "AZURE_OPENAI_USE_AAD" -Value "false"
    } elseif ($demoProvider -eq "nim") {
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_DEMO_FORCE_GEMINI" -Value "false"
        $nimModel = $env:NIM_MODEL
        if ([string]::IsNullOrWhiteSpace($nimModel)) {
            $nimModel = $env:MODEL_NAME
        }
        if ([string]::IsNullOrWhiteSpace($nimModel)) {
            $nimModel = "meta/llama-3.1-70b-instruct"
        }
        Set-EnvValue -EnvPath $stagedEnv -Key "MODEL_NAME" -Value $nimModel
        Set-EnvValue -EnvPath $stagedEnv -Key "NIM_MODEL" -Value $nimModel
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_MODEL_ALIAS" -Value $nimModel

        $nimBase = $env:NIM_API_BASE
        if ([string]::IsNullOrWhiteSpace($nimBase)) {
            $nimBase = "https://integrate.api.nvidia.com/v1"
        }
        Set-EnvValue -EnvPath $stagedEnv -Key "NIM_API_BASE" -Value $nimBase
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_BASE_URL" -Value $nimBase

        $demoNimApiKey = $env:NIM_API_KEY
        if ([string]::IsNullOrWhiteSpace($demoNimApiKey)) {
            $demoNimApiKey = $env:CHIPVERIFY_LLM_API_KEY
        }
        if (-not [string]::IsNullOrWhiteSpace($demoNimApiKey)) {
            Set-EnvValue -EnvPath $stagedEnv -Key "NIM_API_KEY" -Value $demoNimApiKey
            Set-EnvValue -EnvPath $stagedEnv -Key "OPENAI_API_KEY" -Value $demoNimApiKey
            Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_API_KEY" -Value $demoNimApiKey
        }
    } else {
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_DEMO_FORCE_GEMINI" -Value "true"
        Set-EnvValue -EnvPath $stagedEnv -Key "MODEL_PROVIDER" -Value "gemini"
        Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_PROVIDER" -Value "gemini"
        Set-EnvValue -EnvPath $stagedEnv -Key "MODEL_NAME" -Value "gemini-2.5-pro"
        Set-EnvValue -EnvPath $stagedEnv -Key "GEMINI_MODEL" -Value "gemini-2.5-pro"
        $demoGeminiApiKey = $env:CHIPVERIFY_DEMO_GEMINI_API_KEY
        if ([string]::IsNullOrWhiteSpace($demoGeminiApiKey)) {
            $demoGeminiApiKey = $env:GEMINI_API_KEY
        }
        if ([string]::IsNullOrWhiteSpace($demoGeminiApiKey)) {
            $demoGeminiApiKey = $env:GOOGLE_API_KEY
        }
        if (-not [string]::IsNullOrWhiteSpace($demoGeminiApiKey)) {
            Set-EnvValue -EnvPath $stagedEnv -Key "GEMINI_API_KEY" -Value $demoGeminiApiKey
            Set-EnvValue -EnvPath $stagedEnv -Key "GOOGLE_API_KEY" -Value $demoGeminiApiKey
            Set-EnvValue -EnvPath $stagedEnv -Key "GOOGLE_GENERATIVE_AI_API_KEY" -Value $demoGeminiApiKey
        }
    }
    Set-EnvValue -EnvPath $stagedEnv -Key "CHIPVERIFY_LLM_API_KEY_REQUIRED" -Value "false"

    $sourceLicense = Join-Path $repoRoot "backend\license.key"
    if (Test-Path $sourceLicense) {
        Copy-Item -Path $sourceLicense -Destination (Join-Path $stageBackend "license.key") -Force
    }

    $runtimeBin = Join-Path $repoRoot "backend\runtime\bin"
    if (Test-Path $runtimeBin) {
        Copy-Item -Path (Join-Path $runtimeBin "*") -Destination $stageBin -Force
    }

    $slangSource = $null
    if ($env:CHIPVERIFY_SLANG_BIN -and (Test-Path $env:CHIPVERIFY_SLANG_BIN)) {
        $slangSource = (Resolve-Path $env:CHIPVERIFY_SLANG_BIN).Path
    } else {
        $slangCommand = Get-Command "slang.exe" -ErrorAction SilentlyContinue
        if (-not $slangCommand) {
            $slangCommand = Get-Command "slang" -ErrorAction SilentlyContinue
        }
        if ($slangCommand) {
            $slangSource = $slangCommand.Source
        }
    }
    if ($slangSource) {
        Copy-Item -Path $slangSource -Destination (Join-Path $stageBin "slang.exe") -Force
        Write-Host "Bundled Slang parser: $slangSource" -ForegroundColor Green
    } else {
        Write-Warning "Slang parser was not found. TruthCore will require CHIPVERIFY_SLANG_BIN or runtime/bin/slang.exe on the target machine."
    }

    $svlsSource = $null
    if ($env:CHIPVERIFY_SVLS_BIN -and (Test-Path $env:CHIPVERIFY_SVLS_BIN)) {
        $svlsSource = (Resolve-Path $env:CHIPVERIFY_SVLS_BIN).Path
    } else {
        $svlsCommand = Get-Command "svls.exe" -ErrorAction SilentlyContinue
        if (-not $svlsCommand) {
            $svlsCommand = Get-Command "svls" -ErrorAction SilentlyContinue
        }
        if ($svlsCommand) {
            $svlsSource = $svlsCommand.Source
        }
    }
    if ($svlsSource) {
        Copy-Item -Path $svlsSource -Destination (Join-Path $stageBin "svls.exe") -Force
        Write-Host "Bundled svls linter: $svlsSource" -ForegroundColor Green
    } else {
        Write-Warning "svls was not found. RTL lint will be skipped unless CHIPVERIFY_SVLS_BIN or runtime/bin/svls.exe is available."
    }

    $manifest = [ordered]@{
        product = "chipverify-desktop"
        created_at = (Get-Date).ToUniversalTime().ToString("o")
        backend_runtime = "runtime/backend/chipverify-backend.exe"
        backend_source_fallback = "runtime/backend_source/main.py"
        slang_runtime = "runtime/bin/slang.exe"
        slang_bundled = [bool]$slangSource
        svls_runtime = "runtime/bin/svls.exe"
        svls_bundled = [bool]$svlsSource
        embedded_env = (Test-Path $stagedEnv)
        activation_required = $true
        electron_target = $ElectronTarget
    } | ConvertTo-Json -Depth 4
    Set-Content -Path (Join-Path $stageRoot "runtime-manifest.json") -Value $manifest -Encoding UTF8

    Write-Host "[4/5] Building frontend..." -ForegroundColor Cyan
    Write-Host "Installing frontend dependencies..."
    Push-Location (Join-Path $repoRoot "frontend")
    try {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install frontend dependencies."
        }
    } finally {
        Pop-Location
    }
    $version = (Get-Content -Path (Join-Path $repoRoot "package.json") -Raw | ConvertFrom-Json).version
    $env:CHIPVERIFY_PACKAGE_VERSION = $version
    $env:CHIPVERIFY_SENTRY_RELEASE = "chipverify-desktop@$version"
    if ($env:CHIPVERIFY_SENTRY_DSN) {
        $env:VITE_SENTRY_DSN = $env:CHIPVERIFY_SENTRY_DSN
    }
    $env:VITE_SENTRY_ENVIRONMENT = if ($env:CHIPVERIFY_SENTRY_ENVIRONMENT) { $env:CHIPVERIFY_SENTRY_ENVIRONMENT } else { "production" }
    $env:VITE_SENTRY_RELEASE = "chipverify-desktop@$version"
    $env:VITE_APP_VERSION = $version
    & npm.cmd run build:frontend
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed."
    }

    Write-Host "[5/5] Building Electron Windows package with embedded backend..." -ForegroundColor Cyan
    $electronBuilderArgs = @("--win", $ElectronTarget, "--x64", "--publish", "never")
    $localElectronDist = Join-Path $repoRoot "node_modules\electron\dist"
    if (Test-Path $localElectronDist) {
        $electronBuilderArgs += "--config.electronDist=$localElectronDist"
    }
    & npx.cmd electron-builder @electronBuilderArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Electron Builder failed."
    }

    $unpackedBackendExe = Join-Path $repoRoot "dist-electron\win-unpacked\resources\runtime\backend\chipverify-backend.exe"
    if (Test-Path $unpackedBackendExe) {
        Write-Host "EMBEDDED_BACKEND=$unpackedBackendExe" -ForegroundColor Green
    } else {
        Write-Warning "Could not verify win-unpacked embedded backend at '$unpackedBackendExe'. If only an installer was emitted, install it and check resources/runtime/backend."
    }

    $installer = Get-ChildItem -Path (Join-Path $repoRoot "dist-electron") -Filter "*win-x64*.exe" -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($installer) {
        Write-Host "INSTALLER=$($installer.FullName)" -ForegroundColor Green
    }

    Clear-StageRoot $stageRoot
}
finally {
    Pop-Location
}
