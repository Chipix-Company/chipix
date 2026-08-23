param(
  [string]$Deployment = "next-swordfish-62",
  [string]$EnvFile = "backend/.env",
  [switch]$KeepOpenAi,
  [switch]$SkipBedrockGatewayConfig,
  [switch]$DryRun,
  [string[]]$Names = @(
    "BEDROCK_API_KEY",
    "AWS_BEARER_TOKEN_BEDROCK",
    "BEDROCK_REGION",
    "BEDROCK_API_BASE",
    "BEDROCK_MODEL"
  ),
  [string[]]$OpenAiNames = @(
    "OPENAI_API_KEY",
    "CHIPVERIFY_OPENAI_API_KEY",
    "CHIPVERIFY_LLM_API_KEY",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "GEMINI_MODEL",
    "NIM_API_KEY",
    "NIM_API_BASE",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION"
  )
)

$ErrorActionPreference = "Stop"

$fileEnv = @{}
if (-not [string]::IsNullOrWhiteSpace($EnvFile)) {
  $envPath = if ([System.IO.Path]::IsPathRooted($EnvFile)) {
    $EnvFile
  } else {
    Join-Path (Get-Location).Path $EnvFile
  }
  if (Test-Path -LiteralPath $envPath) {
    foreach ($line in Get-Content -LiteralPath $envPath) {
      if ($line -match '^\s*#' -or $line -match '^\s*$') {
        continue
      }
      if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
        $fileEnv[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
      }
    }
    Write-Host "Loaded provider values from $envPath"
  }
}

function Get-ProviderEnvValue {
  param([string]$Name)
  $value = [Environment]::GetEnvironmentVariable($Name, "Process")
  if ([string]::IsNullOrWhiteSpace($value) -and $fileEnv.ContainsKey($Name)) {
    $value = [string]$fileEnv[$Name]
  }
  if ([string]::IsNullOrWhiteSpace($value)) {
    $value = [Environment]::GetEnvironmentVariable($Name, "User")
  }
  return $value
}

function Invoke-ConvexCli {
  param([string[]]$Command)

  $cmd = @($Command)
  if (-not $env:CONVEX_DEPLOY_KEY) {
    $cmd += @("--deployment", $Deployment)
  }

  if ($DryRun) {
    $display = @($cmd)
    if ($display.Count -ge 5 -and $display[0] -eq "convex" -and $display[1] -eq "env" -and $display[2] -eq "set") {
      $display[4] = "<redacted>"
    }
    Write-Host "Dry run: npx $($display -join ' ')"
    return @()
  }

  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $output = & npx.cmd @cmd 2>&1
  $exitCode = $LASTEXITCODE
  $ErrorActionPreference = $previousErrorActionPreference

  if ($exitCode -ne 0) {
    throw ($output -join "`n")
  }

  return $output
}

function Invoke-ConvexRun {
  param(
    [string]$FunctionName,
    [hashtable]$Payload
  )

  $payloadJson = $Payload | ConvertTo-Json -Depth 20 -Compress
  return Invoke-ConvexCli -Command @("convex", "run", $FunctionName, $payloadJson)
}

if (-not $KeepOpenAi) {
  foreach ($name in $OpenAiNames) {
    Invoke-ConvexCli -Command @("convex", "env", "remove", $name) | Out-Null
    if ($DryRun) {
      Write-Host "Would remove $name from Convex if it existed"
    } else {
      Write-Host "Removed $name from Convex if it existed"
    }
  }
}

foreach ($name in $Names) {
  $value = Get-ProviderEnvValue -Name $name
  if ([string]::IsNullOrWhiteSpace($value)) {
    Write-Host "Skipping $name (not set)"
    continue
  }

  Invoke-ConvexCli -Command @("convex", "env", "set", $name, $value) | Out-Null
  if ($DryRun) {
    Write-Host "Would set $name in Convex"
  } else {
    Write-Host "Set $name in Convex"
  }
}

if (-not $SkipBedrockGatewayConfig) {
  $bedrockKey = Get-ProviderEnvValue -Name "BEDROCK_API_KEY"
  if ([string]::IsNullOrWhiteSpace($bedrockKey)) {
    $bedrockKey = Get-ProviderEnvValue -Name "AWS_BEARER_TOKEN_BEDROCK"
  }

  if ([string]::IsNullOrWhiteSpace($bedrockKey)) {
    Write-Host "Skipping Bedrock Convex gateway config (BEDROCK_API_KEY not set)"
  } else {
    $region = Get-ProviderEnvValue -Name "BEDROCK_REGION"
    if ([string]::IsNullOrWhiteSpace($region)) {
      $region = "us-east-1"
    }

    $baseUrl = Get-ProviderEnvValue -Name "BEDROCK_API_BASE"
    if ([string]::IsNullOrWhiteSpace($baseUrl)) {
      $baseUrl = "https://bedrock-mantle.$region.api.aws/v1"
    }

    $model = Get-ProviderEnvValue -Name "BEDROCK_MODEL"
    if ([string]::IsNullOrWhiteSpace($model)) {
      $model = Get-ProviderEnvValue -Name "MODEL_NAME"
    }
    if ([string]::IsNullOrWhiteSpace($model)) {
      $model = "deepseek.v3.2"
    }

    Invoke-ConvexRun `
      -FunctionName "controlPlane:upsertProviderSecret" `
      -Payload @{
        provider = "bedrock"
        envKey = "BEDROCK_API_KEY"
        baseUrl = $baseUrl
      } | Out-Null

    Invoke-ConvexRun `
      -FunctionName "controlPlane:upsertRemoteConfig" `
      -Payload @{
        name = "default"
        provider = "bedrock"
        model = $model
        maxTokens = 16384
        temperature = 0.3
        dailyTokenLimit = 250000
        monthlyTokenLimit = 2500000
        perMachineDailyLimit = 100000
        allowUnknownDevices = $false
        gatewayEnabled = $true
        featureFlags = @{}
      } | Out-Null

    if ($DryRun) {
      Write-Host "Would configure Convex LLM gateway for Bedrock ($region, $model)"
    } else {
      Write-Host "Configured Convex LLM gateway for Bedrock ($region, $model)"
    }
  }
}
