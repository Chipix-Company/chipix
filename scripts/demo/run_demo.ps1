<#
.SYNOPSIS
  Launch Chipix backend + frontend in DEMO mode (scripted LLM, no remote API).

.NOTES
  Local / demo-only. Does not commit. Does not start a real LLM.
#>
param(
  [switch]$BackendOnly,
  [switch]$SkipSeed
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

$env:CHIPVERIFY_DEMO_MODE = "true"
$env:CHIPVERIFY_LLM_PROVIDER = "demo"
$env:MODEL_PROVIDER = "demo"
$env:CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES = "true"
$env:CHIPVERIFY_DEMO_SCENARIO = "sha256_fix_loop"
$env:CHIPVERIFY_ALLOW_DEV_AUTH = "true"
$env:CHIPVERIFY_DEMO_THINK_MS = "160"
$env:CHIPVERIFY_LLM_API_KEY_REQUIRED = "false"
$env:CHIPVERIFY_WORKSPACE_ROOT = "$repoRoot"
if (-not $env:CHIPVERIFY_SECRET_KEY) {
  $env:CHIPVERIFY_SECRET_KEY = "chipverify-local-secret-demo"
}

$demoEnv = Join-Path $repoRoot "backend\.env.demo"
# Prefer process env over rewriting backend/.env (keeps your real keys intact).
Get-Content $demoEnv | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
  $k, $v = $_.Split('=', 2)
  Set-Item -Path "Env:$k" -Value $v
}

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  $python = "python"
}

Write-Host "Starting demo backend on :7348 (provider=demo)..." -ForegroundColor Cyan
$backendCmd = @"
Set-Location '$repoRoot\backend'
`$env:CHIPVERIFY_DEMO_MODE='true'
`$env:CHIPVERIFY_LLM_PROVIDER='demo'
`$env:MODEL_PROVIDER='demo'
`$env:CHIPVERIFY_DEMO_RELAX_MENTAL_MODEL_GATES='true'
`$env:CHIPVERIFY_ALLOW_DEV_AUTH='true'
`$env:CHIPVERIFY_DEMO_THINK_MS='160'
`$env:CHIPVERIFY_LLM_API_KEY_REQUIRED='false'
`$env:CHIPVERIFY_WORKSPACE_ROOT='$repoRoot'
& '$python' -m uvicorn main:app --host 127.0.0.1 --port 7348
"@

Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", $backendCmd

if (-not $BackendOnly) {
  Write-Host "Starting frontend on :5173..." -ForegroundColor Cyan
  $feCmd = @"
Set-Location '$repoRoot\frontend'
npm run dev -- --host 127.0.0.1 --port 5173
"@
  Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", $feCmd
}

Write-Host "Waiting for health..." -ForegroundColor Yellow
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
  try {
    $h = Invoke-RestMethod "http://127.0.0.1:7348/api/v1/health" -TimeoutSec 2
    if ($h.status -eq "ok") { $ok = $true; break }
  } catch {}
  Start-Sleep -Seconds 1
}
if (-not $ok) { throw "Backend health check failed" }

if (-not $SkipSeed) {
  Write-Host "Seeding SHA-256 demo project..." -ForegroundColor Cyan
  & $python (Join-Path $repoRoot "scripts\demo\seed_sha256_project.py")
}

Write-Host ""
Write-Host "Demo ready:" -ForegroundColor Green
Write-Host "  Frontend  http://127.0.0.1:5173"
Write-Host "  Backend   http://127.0.0.1:7348"
Write-Host "  Prompt:   Verify the SHA-256 accelerator end to end"
Write-Host ""
Write-Host "Record with OpenScreen when the UI is visible."
