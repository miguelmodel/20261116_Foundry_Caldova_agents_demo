[CmdletBinding()]
param([int]$TimeoutSeconds = 900)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Push-Location $CaldovaRoot
try {
    $values = Get-CaldovaEnv
    Assert-CaldovaConfig -Values $values -Keys @('AZURE_SUBSCRIPTION_ID', 'ACR_NAME', 'ACR_LOGIN_SERVER', 'FOUNDRY_PROJECT_ENDPOINT')
    $null = Assert-CaldovaSubscriptionEnabled -SubscriptionId $values.AZURE_SUBSCRIPTION_ID
    $imageTag = 'caldova-logistics-analyst:' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfffZ')
    Write-Host "Building $imageTag in Azure Container Registry (no local Docker)..."
    # Queue without log streaming (the Windows CLI can crash on Unicode build logs) and poll the run.
    $queued = & az acr build --subscription $values.AZURE_SUBSCRIPTION_ID `
        --registry $values.ACR_NAME --image $imageTag --platform linux/amd64 --no-wait `
        (Join-Path $CaldovaRoot 'agents\logistics_analyst') 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "ACR build could not be queued: $queued" }
    if ($queued -notmatch 'Queued a build with ID:\s*(\S+)') { throw "ACR did not return a build run id: $queued" }
    $runId = $Matches[1]
    $deadline = (Get-Date).AddMinutes(30)
    do {
        Start-Sleep -Seconds 15
        $status = Invoke-CaldovaAz -Arguments @(
            'acr', 'task', 'show-run', '--subscription', $values.AZURE_SUBSCRIPTION_ID,
            '--registry', $values.ACR_NAME, '--run-id', $runId, '--query', 'status', '--output', 'tsv'
        )
        Write-Host "ACR build $runId status: $status"
    } while ($status -in @('Queued', 'Started', 'Running') -and (Get-Date) -lt $deadline)
    if ($status -ne 'Succeeded') {
        throw "ACR build $runId ended with status '$status'. Inspect: az acr task logs --registry $($values.ACR_NAME) --run-id $runId"
    }
    $image = "$($values.ACR_LOGIN_SERVER)/$imageTag"
    Set-CaldovaEnv -Values @{ ANALYST_CONTAINER_IMAGE = $image }
    Invoke-CaldovaPython -Arguments @(
        (Join-Path $PSScriptRoot 'register_analyst.py'), '--image', $image,
        '--timeout-seconds', "$TimeoutSeconds"
    )
} catch {
    Write-Warning 'If hosted-agent provisioning reports an image-pull failure, azd ai agent is the fallback; check ACR access and the project AcrPull assignment first. No local Docker is required.'
    throw
} finally {
    Pop-Location
}
