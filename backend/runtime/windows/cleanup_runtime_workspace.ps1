param(
    [string]$RepoRoot = "",
    [switch]$RemoveLlamaSourceClone = $true
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $scriptDir "..\..\..")).Path
}

$removed = New-Object System.Collections.Generic.List[string]

function Remove-PathIfExists([string]$PathToRemove) {
    if (Test-Path $PathToRemove) {
        try {
            Remove-Item -Path $PathToRemove -Recurse -Force -ErrorAction Stop
            $removed.Add($PathToRemove) | Out-Null
        } catch {
            Write-Host "SKIPPED_LOCKED=$PathToRemove"
        }
    }
}

# Remove runtime and backend logs (generated artifacts only).
$logDir = Join-Path $RepoRoot "backend\\logs"
if (Test-Path $logDir) {
    Get-ChildItem -Path $logDir -Force -ErrorAction SilentlyContinue |
        ForEach-Object {
            Remove-PathIfExists $_.FullName
        }
}

# Remove Python cache artifacts under backend.
Get-ChildItem -Path (Join-Path $RepoRoot "backend") -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Remove-PathIfExists $_.FullName
    }

Get-ChildItem -Path (Join-Path $RepoRoot "backend") -Recurse -File -Include "*.pyc", "*.pyo" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Remove-PathIfExists $_.FullName
    }

# Optional cleanup of temporary llama.cpp source clone used only for local build.
if ($RemoveLlamaSourceClone) {
    Remove-PathIfExists (Join-Path $RepoRoot "vendor\llama.cpp")
}

Write-Host "CLEANUP_REMOVED_COUNT=$($removed.Count)"
if ($removed.Count -gt 0) {
    $removed | ForEach-Object { Write-Host "REMOVED=$_" }
} else {
    Write-Host "No cleanup needed."
}
