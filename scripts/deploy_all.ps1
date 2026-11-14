[CmdletBinding()]
param(
    # Defaults to AZURE_SUBSCRIPTION_ID in .env, then to the active Azure CLI subscription.
    [string]$SubscriptionId = '',
    [string]$ResourceGroup = 'rg-caldova-foundry-demo',
    [string]$Location = 'eastus2',
    [switch]$DeployClaude = $true,
    [switch]$NoClaude,
    # Model capacity in thousands of tokens per minute. Lower these if your subscription has less quota.
    [ValidateRange(1, 100000)][int]$CoordinatorCapacity = 900,
    [ValidateRange(1, 100000)][int]$AnalystCapacity = 200
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
& (Join-Path $PSScriptRoot 'provision.ps1') -SubscriptionId $SubscriptionId -ResourceGroup $ResourceGroup -Location $Location -DeployClaude:$DeployClaude -NoClaude:$NoClaude -CoordinatorCapacity $CoordinatorCapacity -AnalystCapacity $AnalystCapacity
& (Join-Path $PSScriptRoot 'deploy_analyst.ps1')
Invoke-CaldovaPython -Arguments @((Join-Path $PSScriptRoot 'setup_coordinator.py'))
Invoke-CaldovaPython -Arguments @((Join-Path $PSScriptRoot 'smoke_test.py'))
Invoke-CaldovaPython -Arguments @((Join-Path $PSScriptRoot 'setup_evaluations.py'))
Write-Host 'Caldova Control Tower deployment and smoke test completed.'
