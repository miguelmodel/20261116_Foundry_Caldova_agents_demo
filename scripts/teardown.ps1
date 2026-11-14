[CmdletBinding()]
param(
    [string]$SubscriptionId,
    [string]$ResourceGroup,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$values = Get-CaldovaEnv
if (-not $SubscriptionId) { $SubscriptionId = $values.AZURE_SUBSCRIPTION_ID }
if (-not $ResourceGroup) { $ResourceGroup = $values.AZURE_RESOURCE_GROUP }
if (-not $SubscriptionId -or -not $ResourceGroup) {
    throw 'Provide -SubscriptionId and -ResourceGroup, or populate .env with provision.ps1.'
}
if (-not $Force) {
    $answer = Read-Host "Delete ALL resources in '$ResourceGroup' in subscription '$SubscriptionId'? Type DELETE"
    if ($answer -cne 'DELETE') {
        Write-Host 'Teardown cancelled.'
        return
    }
}
$null = Assert-CaldovaSubscriptionEnabled -SubscriptionId $SubscriptionId
# Only clean up agents from .env when it refers to the same group being deleted.
if ($values.AZURE_SUBSCRIPTION_ID -eq $SubscriptionId -and $values.AZURE_RESOURCE_GROUP -eq $ResourceGroup) {
    try {
        Invoke-CaldovaPython -Arguments @((Join-Path $PSScriptRoot 'common.py'), 'delete-agents')
    } catch {
        Write-Warning 'Best-effort agent cleanup failed; proceeding with resource-group deletion.'
    }
}
$null = Invoke-CaldovaAz -Arguments @(
    'group', 'delete', '--subscription', $SubscriptionId, '--name', $ResourceGroup, '--yes', '--no-wait'
)
Write-Host "Deletion requested for $ResourceGroup. Azure will complete it asynchronously."
Write-Host 'Local .env has been retained; provision.ps1 will refresh outputs if you redeploy.'
