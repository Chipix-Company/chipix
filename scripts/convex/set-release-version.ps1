param(
  [Parameter(Mandatory = $true)]
  [string]$Version,

  [string]$Platform = "all",
  [string]$MinSupportedVersion = "",
  [string]$GitHubTag = "",
  [string]$GitHubRepository = "AffanShaikhsurab/chipix_release",
  [string]$UpdateFeedUrl = "",
  [string]$DownloadUrl = "",
  [string]$ReleaseNotes = "",
  [switch]$ForceUpdate
)

$minVersion = if ($MinSupportedVersion) { $MinSupportedVersion } else { $Version }
$tag = if ($GitHubTag) { $GitHubTag } else { "v$Version" }

if (-not $UpdateFeedUrl) {
  $UpdateFeedUrl = "https://github.com/$GitHubRepository/releases/download/$tag/"
}

if (-not $DownloadUrl -and $Platform -eq "linux") {
  $DownloadUrl = "https://github.com/$GitHubRepository/releases/download/$tag/ChipVerify_Desktop-${Version}-redhat-x86_64.AppImage"
}

$payloadObj = @{
  platform = $Platform
  version = $Version
  minSupportedVersion = $minVersion
  updateFeedUrl = $UpdateFeedUrl
  downloadUrl = $DownloadUrl
  releaseNotes = $ReleaseNotes
  forceUpdate = [bool]$ForceUpdate
}

$payload = $payloadObj | ConvertTo-Json -Compress
Write-Host "Upserting Convex appInfo for platform=$Platform version=$Version"
npx convex run appInfo:upsert $payload

Write-Host "Upserting Convex desktopReleases for platform=$Platform"
node -e "const {execSync}=require('child_process'); const payload=process.argv[1]; console.log(execSync('npx convex run controlPlane:publishDesktopRelease '+JSON.stringify(payload),{stdio:'pipe',encoding:'utf8'}));" $payload
