param(
  [Parameter(Mandatory = $true)]
  [string]$Version,

  [string]$GitHubTag = "",
  [string]$GitHubRepository = "AffanShaikhsurab/chipix_release",
  [string]$ReleaseNotes = "",
  [switch]$SkipConvex,
  [switch]$SkipGitHub
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

$tag = if ($GitHubTag) { $GitHubTag } else { "v$Version" }
$appImage = "dist-redhat/ChipVerify_Desktop-${Version}-redhat-x86_64.AppImage"
$feed = "dist-redhat/latest-linux.yml"

if (-not (Test-Path $appImage)) {
  throw "AppImage not found: $appImage"
}
if (-not (Test-Path $feed)) {
  throw "Update feed not found: $feed"
}

if (-not $ReleaseNotes) {
  $ReleaseNotes = "ChipVerify Desktop $Version"
}

if (-not $SkipGitHub) {
  Write-Host "Publishing GitHub release $tag to $GitHubRepository"
  gh release create $tag $appImage $feed `
    --repo $GitHubRepository `
    --title "ChipVerify Desktop $Version" `
    --notes $ReleaseNotes
}

if (-not $SkipConvex) {
  Write-Host "Registering Convex release metadata"
  $env:GITHUB_REPOSITORY = $GitHubRepository
  bash ./scripts/convex/set-release-version.sh `
    --version $Version `
    --platform linux `
    --github-tag $tag `
    --release-notes $ReleaseNotes
  bash ./scripts/convex/set-release-version.sh `
    --version $Version `
    --platform all `
    --github-tag $tag `
    --release-notes $ReleaseNotes
}

Write-Host "Done. Public release: https://github.com/$GitHubRepository/releases/tag/$tag"
