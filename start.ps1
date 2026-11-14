# Run from any directory: .\start.ps1
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'Create the Python environment first:' -ForegroundColor Yellow
    Write-Host 'uv venv --python 3.14 .venv; uv pip install --python .venv\Scripts\python.exe -r requirements.txt --prerelease=allow'
    exit 1
}
if (-not (Test-Path '.env')) {
    Write-Warning 'No .env found. Run scripts/deploy_all.ps1 to configure real Foundry agents. The UI will still start.'
}
$port = & $python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(os.getenv('APP_PORT') or '8000')"
$parsedPort = 0
if (-not [int]::TryParse("$port", [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
    Write-Warning "Invalid APP_PORT '$port'; using 8000."
    $parsedPort = 8000
}
try {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'Azure CLI is not installed.' }
    $envValues = & $python -c "from dotenv import dotenv_values; import json; print(json.dumps(dotenv_values('.env')))" | ConvertFrom-Json
    if ($envValues.AZURE_CONFIG_DIR) { $env:AZURE_CONFIG_DIR = [Environment]::ExpandEnvironmentVariables($envValues.AZURE_CONFIG_DIR) }
    $active = & az account show --only-show-errors --output json 2>$null | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $active) { throw 'Azure CLI is not signed in.' }
    if ($envValues.AZURE_SUBSCRIPTION_ID -and $active.id -ne $envValues.AZURE_SUBSCRIPTION_ID) {
        throw "Azure CLI is on subscription $($active.id), not $($envValues.AZURE_SUBSCRIPTION_ID). Set AZURE_CONFIG_DIR in .env or run: az account set --subscription $($envValues.AZURE_SUBSCRIPTION_ID)"
    }
} catch {
    Write-Warning "Azure login check: $($_.Exception.Message)"
}
$url = "http://127.0.0.1:$parsedPort"
Write-Host "`nCALDOVA — Control Tower  $url" -ForegroundColor Green
Write-Host 'Press Ctrl+C to stop.'
$browserJob = Start-Job -ArgumentList $url -ScriptBlock {
    param($url)
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        try {
            $null = Invoke-WebRequest $url -TimeoutSec 1
            Start-Process $url
            break
        } catch { Start-Sleep -Milliseconds 500 }
    }
}
try {
    & $python -m uvicorn app.backend.main:app --host 127.0.0.1 --port $parsedPort
} finally {
    Stop-Job $browserJob -ErrorAction SilentlyContinue
    Remove-Job $browserJob -Force -ErrorAction SilentlyContinue
}
