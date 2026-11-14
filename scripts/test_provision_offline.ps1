# Exercise provisioning in an isolated copy with an in-process fake az command.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$fixture = Join-Path $root ('.deploy\provision-test-' + [guid]::NewGuid().ToString('N'))
$fixtureScripts = Join-Path $fixture 'scripts'
$null = New-Item -ItemType Directory -Path $fixtureScripts -Force
$global:CaldovaProvisionTestState = @{
    Disabled = $false
    FailClaude = $false
    Deployments = 0
    Writes = 0
}

function az {
    param([Parameter(ValueFromRemainingArguments)][string[]]$Arguments)
    $global:LASTEXITCODE = 0
    $state = $global:CaldovaProvisionTestState
    switch ("$($Arguments[0]) $($Arguments[1])") {
        'account set' { return }
        'account show' { return 'offline-subscription' }
        'rest --method' {
            $subscriptionState = if ($state.Disabled) { 'Disabled' } else { 'Enabled' }
            return (@{ state = $subscriptionState; tenantId = 'offline-tenant' } | ConvertTo-Json -Compress)
        }
        'provider show' { return 'Registered' }
        'ad signed-in-user' { return 'offline-user' }
        'group create' {
            $state.Writes++
            if ($Arguments -notcontains '--tags') { throw 'Resource group did not receive tags.' }
            return
        }
        'deployment group' {
            $state.Writes++
            $state.Deployments++
            $parameterIndex = [Array]::IndexOf($Arguments, '--parameters') + 1
            $parameterPath = $Arguments[$parameterIndex].TrimStart('@')
            $parameters = Get-Content -LiteralPath $parameterPath -Raw | ConvertFrom-Json
            if ($parameters.parameters.tags.value.project -ne 'caldova-control-tower') {
                throw 'Deployment did not receive merged tags.'
            }
            if ($state.FailClaude -and $parameters.parameters.deployClaude.value) {
                $global:LASTEXITCODE = 1
                return
            }
            $outputs = @{}
            foreach ($name in @(
                'foundryAccountName', 'foundryAccountId', 'foundryProjectName', 'foundryProjectId',
                'foundryProjectEndpoint', 'foundryProjectPrincipalId', 'acrName', 'acrLoginServer',
                'acrId', 'appInsightsName', 'appInsightsId', 'logAnalyticsWorkspaceId'
            )) {
                $outputs[$name] = @{ value = "offline-$name" }
            }
            return ($outputs | ConvertTo-Json -Depth 10 -Compress)
        }
        default { throw "Unexpected Azure command in offline test: $($Arguments[0]) $($Arguments[1])" }
    }
}

try {
    Copy-Item -LiteralPath (Join-Path $root '.env.example') -Destination $fixture
    foreach ($name in @('common.py', 'common.ps1', 'provision.ps1')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $fixtureScripts
    }
    $commonPath = Join-Path $fixtureScripts 'common.ps1'
    $pythonPath = (Join-Path $root '.venv\Scripts\python.exe').Replace("'", "''")
    $commonText = Get-Content -LiteralPath $commonPath -Raw
    $commonText.Replace(
        '$CaldovaPython = Join-Path $CaldovaRoot ''.venv\Scripts\python.exe''',
        "`$CaldovaPython = '$pythonPath'"
    ) | Set-Content -LiteralPath $commonPath -Encoding utf8

    $provision = Join-Path $fixtureScripts 'provision.ps1'
    & $provision *> $null
    $envText = Get-Content -LiteralPath (Join-Path $fixture '.env') -Raw
    if ($global:CaldovaProvisionTestState.Deployments -ne 1 -or $envText -notmatch "ANALYST_MODEL_PROVIDER='anthropic'") {
        throw 'Default Claude provisioning test failed.'
    }
    $global:CaldovaProvisionTestState.Deployments = 0
    $global:CaldovaProvisionTestState.FailClaude = $true
    & $provision *> $null
    $envText = Get-Content -LiteralPath (Join-Path $fixture '.env') -Raw
    if ($global:CaldovaProvisionTestState.Deployments -ne 2 -or $envText -notmatch "ANALYST_MODEL_PROVIDER='openai'") {
        throw 'Claude fallback provisioning test failed.'
    }
    $global:CaldovaProvisionTestState.Deployments = 0
    & $provision -NoClaude *> $null
    if ($global:CaldovaProvisionTestState.Deployments -ne 1) { throw 'Explicit -NoClaude test failed.' }

    $global:CaldovaProvisionTestState.Disabled = $true
    $global:CaldovaProvisionTestState.Writes = 0
    $rejected = $false
    try { & $provision *> $null } catch { $rejected = $_.Exception.Message -like '*No writes attempted*' }
    if (-not $rejected -or $global:CaldovaProvisionTestState.Writes -ne 0) {
        throw 'Disabled subscription did not stop all provisioning writes.'
    }
    Write-Host 'PASS: mocked provisioning (Claude, automatic fallback, -NoClaude, disabled guard); no Azure calls.'
} finally {
    Remove-Item -LiteralPath $fixture -Recurse -Force
    Remove-Variable -Name CaldovaProvisionTestState -Scope Global
    Remove-Item Function:\az
}
