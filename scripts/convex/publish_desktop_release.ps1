param(
  [string]$Deployment = "next-swordfish-62",
  [string]$Platform = "win32",
  [string]$Version = "0.1.0",
  [string]$MinSupportedVersion = "0.1.0",
  [string]$ReleaseNotes = "Chipix desktop release",
  [string]$InstallerPath = "",
  [string]$UpdateFeedPath = "",
  [switch]$ForceUpdate
)

$ErrorActionPreference = "Stop"

# WARNING: Convex Free tier = 1 GB total file storage + 1 GB/month egress.
# Do NOT upload AppImages here on Free tier (~380 MB each). Use GitHub Releases instead:
#   ./scripts/convex/set-release-version.sh --version 0.2.1 --platform linux --github-tag v0.2.1

function Read-JsonResult {
  param([string]$Text)
  $lines = @($Text -split "`r?`n" | Where-Object { $_.Trim() })
  for ($i = 0; $i -lt $lines.Count; $i++) {
    $candidate = $lines[$i].Trim()
    if ($candidate.StartsWith("{") -or $candidate.StartsWith("[") -or $candidate.StartsWith('"')) {
      $json = ($lines[$i..($lines.Count - 1)] -join "`n")
      try {
        return $json | ConvertFrom-Json
      } catch {
        continue
      }
    }
  }
  throw "Could not find a JSON result in Convex CLI output: $Text"
}

function ConvertTo-ConvexCliArg {
  param([object]$Value)
  if ($null -eq $Value) {
    return "null"
  }
  if ($Value -is [bool]) {
    return $(if ($Value) { "true" } else { "false" })
  }
  if ($Value -is [int] -or $Value -is [long] -or $Value -is [double] -or $Value -is [decimal]) {
    return ([string]::Format([Globalization.CultureInfo]::InvariantCulture, "{0}", $Value))
  }
  if ($Value -is [string]) {
    $escaped = $Value.Replace("\", "\\").Replace("'", "\'")
    return "'$escaped'"
  }
  if ($Value -is [hashtable]) {
    $parts = foreach ($key in $Value.Keys) {
      "${key}:$(ConvertTo-ConvexCliArg -Value $Value[$key])"
    }
    return "{" + ($parts -join ",") + "}"
  }
  if ($Value -is [System.Collections.IEnumerable]) {
    $parts = foreach ($item in $Value) {
      ConvertTo-ConvexCliArg -Value $item
    }
    return "[" + ($parts -join ",") + "]"
  }
  return ConvertTo-ConvexCliArg -Value ([string]$Value)
}

function Invoke-ConvexJson {
  param(
    [string]$FunctionName,
    [hashtable]$Payload,
    [switch]$Push
  )
  $argsJson = ConvertTo-ConvexCliArg -Value $Payload
  $convexCli = Resolve-Path -LiteralPath "node_modules/convex/bin/main.js"
  $cmd = @("run", $FunctionName, $argsJson)
  if (-not $env:CONVEX_DEPLOY_KEY) {
    $cmd += @("--deployment", $Deployment)
  }
  if ($Push) {
    $cmd += "--push"
  }
  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $output = & node $convexCli.Path @cmd 2>&1
  $exitCode = $LASTEXITCODE
  $ErrorActionPreference = $previousErrorActionPreference
  if ($exitCode -ne 0) {
    throw ($output -join "`n")
  }
  return Read-JsonResult -Text ($output -join "`n")
}

function Upload-ToConvexStorage {
  param(
    [string]$Kind,
    [string]$Path,
    [string]$ContentType
  )
  $resolved = Resolve-Path -LiteralPath $Path
  $upload = Invoke-ConvexJson `
    -FunctionName "controlPlane:createDesktopReleaseUploadUrl" `
    -Payload @{ kind = $Kind }
  $response = Invoke-RestMethod `
    -Method Post `
    -Uri $upload.uploadUrl `
    -ContentType $ContentType `
    -InFile $resolved.Path
  if (-not $response.storageId) {
    throw "Convex upload did not return a storageId for $Path"
  }
  return [string]$response.storageId
}

function Find-FirstExistingPath {
  param([string[]]$Candidates)
  foreach ($candidate in $Candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
      return $candidate
    }
  }
  return ""
}

if (-not $InstallerPath) {
  switch ($Platform.ToLowerInvariant()) {
    "linux" {
      $InstallerPath = Find-FirstExistingPath @(
        "dist-redhat/ChipVerify_Desktop-$Version-redhat-x86_64.AppImage",
        "dist-redhat/ChipVerify Desktop-$Version-redhat-x86_64.AppImage",
        "dist-linux/ChipVerify_Desktop-$Version-linux-x86_64.AppImage",
        "dist-linux/ChipVerify Desktop-$Version-linux-x86_64.AppImage",
        "dist-electron/ChipVerify_Desktop-$Version-linux-x86_64.AppImage",
        "dist-electron/ChipVerify Desktop-$Version-linux-x86_64.AppImage",
        "dist-linux/ChipVerify_Desktop-$Version-linux-amd64.deb",
        "dist-electron/ChipVerify_Desktop-$Version-linux-amd64.deb"
      )
    }
    "darwin" {
      $InstallerPath = Find-FirstExistingPath @(
        "dist-electron/ChipVerify Desktop-$Version-mac-x64.dmg",
        "dist-electron/ChipVerify Desktop-$Version-mac-arm64.dmg"
      )
    }
    default {
      $InstallerPath = Find-FirstExistingPath @(
        "dist-electron/ChipVerify Desktop-$Version-win-x64.exe",
        "dist-electron/ChipVerify Desktop-$Version-win-ia32.exe"
      )
    }
  }
}

if (-not $UpdateFeedPath) {
  switch ($Platform.ToLowerInvariant()) {
    "linux" {
      $UpdateFeedPath = Find-FirstExistingPath @(
        "dist-redhat/latest-linux.yml",
        "dist-linux/latest-linux.yml",
        "dist-electron/latest-linux.yml"
      )
    }
    "darwin" {
      $UpdateFeedPath = "dist-electron/latest-mac.yml"
    }
    default {
      $UpdateFeedPath = "dist-electron/latest.yml"
    }
  }
}

if (-not (Test-Path -LiteralPath $InstallerPath)) {
  throw "Installer not found: $InstallerPath"
}
if (-not (Test-Path -LiteralPath $UpdateFeedPath)) {
  throw "Update feed not found: $UpdateFeedPath"
}

Write-Host "Deploying Convex functions/schema to $Deployment..."
Invoke-ConvexJson -FunctionName "controlPlane:seedCloudDefaults" -Payload @{} -Push | Out-Null

Write-Host "Uploading update feed to Convex Storage..."
$feedStorageId = Upload-ToConvexStorage -Kind "update_feed" -Path $UpdateFeedPath -ContentType "text/yaml"

Write-Host "Uploading installer to Convex Storage..."
$installerStorageId = Upload-ToConvexStorage -Kind "installer" -Path $InstallerPath -ContentType "application/octet-stream"

Write-Host "Publishing desktop release row..."
$release = Invoke-ConvexJson `
  -FunctionName "controlPlane:publishDesktopRelease" `
  -Payload @{
    platform = $Platform
    version = $Version
    minSupportedVersion = $MinSupportedVersion
    releaseNotes = $ReleaseNotes
    updateFeedStorageId = $feedStorageId
    installerStorageId = $installerStorageId
    forceUpdate = [bool]$ForceUpdate
  }

Write-Host "Published release $Version for $Platform"
Write-Host "desktopReleases id: $release"
Write-Host "updateFeedStorageId: $feedStorageId"
Write-Host "installerStorageId: $installerStorageId"
