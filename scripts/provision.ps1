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
Push-Location $CaldovaRoot
try {
    if (-not $SubscriptionId) { $SubscriptionId = (Get-CaldovaEnv).AZURE_SUBSCRIPTION_ID }
    if (-not $SubscriptionId) {
        $SubscriptionId = Invoke-CaldovaAz -Arguments @('account', 'show', '--query', 'id', '--output', 'tsv')
        Write-Host "Using the active Azure CLI subscription. Pass -SubscriptionId to choose another one."
    }
    $null = Invoke-CaldovaAz -Arguments @('account', 'set', '--subscription', $SubscriptionId)
    $subscription = Assert-CaldovaSubscriptionEnabled -SubscriptionId $SubscriptionId
    foreach ($provider in @(
        'Microsoft.CognitiveServices', 'Microsoft.ContainerRegistry', 'Microsoft.Insights',
        'Microsoft.OperationalInsights', 'Microsoft.SaaS', 'Microsoft.MarketplaceOrdering'
    )) {
        $state = Invoke-CaldovaAz -Arguments @(
            'provider', 'show', '--namespace', $provider, '--query', 'registrationState', '--output', 'tsv'
        )
        if ($state -ne 'Registered') {
            Write-Host "Registering $provider..."
            $null = Invoke-CaldovaAz -Arguments @('provider', 'register', '--namespace', $provider, '--wait', '--output', 'none')
        }
    }
    $principalId = Invoke-CaldovaAz -Arguments @('ad', 'signed-in-user', 'show', '--query', 'id', '--output', 'tsv')
    if ([string]::IsNullOrWhiteSpace($principalId)) { throw 'A signed-in user object id is required.' }
    Assert-CaldovaModelQuota -SubscriptionId $SubscriptionId -Location $Location -ResourceGroup $ResourceGroup -Required @{
        'OpenAI.GlobalStandard.gpt-5.5' = @{ Capacity = $CoordinatorCapacity; Deployment = 'gpt-55'; Label = 'GPT-5.5 (Global Standard)'; Parameter = '-CoordinatorCapacity' }
        'OpenAI.GlobalStandard.gpt-5.4-mini' = @{ Capacity = $AnalystCapacity; Deployment = 'gpt-54-mini'; Label = 'GPT-5.4-mini (Global Standard)'; Parameter = '-AnalystCapacity' }
    }
    $tags = Get-CaldovaTags
    $tagArguments = @($tags.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })
    $null = Invoke-CaldovaAz -Arguments (@(
        'group', 'create', '--name', $ResourceGroup, '--location', $Location, '--tags'
    ) + $tagArguments + @('--output', 'none'))

    $claudeRequested = [bool]$DeployClaude -and -not $NoClaude
    $deploymentName = 'caldova-foundation'
    $deployDir = Join-Path $CaldovaRoot '.deploy'
    $null = New-Item -ItemType Directory -Path $deployDir -Force
    $parameterPath = Join-Path $deployDir 'foundation.parameters.local.json'
    $parameters = @{
        '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
        contentVersion = '1.0.0.0'
        parameters = @{
            principalId = @{ value = $principalId }
            location = @{ value = $Location }
            deployClaude = @{ value = $claudeRequested }
            coordinatorCapacity = @{ value = $CoordinatorCapacity }
            miniCapacity = @{ value = $AnalystCapacity }
            tags = @{ value = $tags }
        }
    }
    try {
        $parameters | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $parameterPath -Encoding utf8
        Write-Host "Deploying foundation in $ResourceGroup ($Location)..."
        $deploymentArguments = @(
            'deployment', 'group', 'create', '--name', $deploymentName, '--resource-group', $ResourceGroup,
            '--template-file', (Join-Path $CaldovaRoot 'infra\main.bicep'), '--parameters', "@$parameterPath",
            '--query', 'properties.outputs', '--output', 'json'
        )
        $deploymentHint = 'Foundation deployment failed; see the Azure error above. Common causes: not enough model quota ' +
            '(re-run with lower -CoordinatorCapacity / -AnalystCapacity, or another -Location), or no permission to create ' +
            'role assignments (you need Owner, or Contributor plus User Access Administrator).'
        try {
            $outputJson = Invoke-CaldovaAz -Arguments $deploymentArguments
        } catch {
            if (-not $claudeRequested) { throw $deploymentHint }
            Write-Warning 'Foundation deployment failed with Claude requested (see the Azure error above). Retrying once without Claude.'
            $claudeRequested = $false
            $parameters.parameters.deployClaude.value = $false
            $parameters | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $parameterPath -Encoding utf8
            try { $outputJson = Invoke-CaldovaAz -Arguments $deploymentArguments } catch { throw $deploymentHint }
        }
    } finally {
        Remove-Item -LiteralPath $parameterPath -Force -ErrorAction SilentlyContinue
    }
    $outputs = $outputJson | ConvertFrom-Json -AsHashtable
    $provider = if ($claudeRequested) { 'anthropic' } else { 'openai' }
    $model = if ($claudeRequested) { 'claude-opus-55' } else { 'gpt-54-mini' }
    Set-CaldovaEnv -Values @{
        AZURE_SUBSCRIPTION_ID = $SubscriptionId
        AZURE_TENANT_ID = $subscription.tenantId
        AZURE_RESOURCE_GROUP = $ResourceGroup
        AZURE_LOCATION = $Location
        FOUNDRY_ACCOUNT_NAME = $outputs.foundryAccountName.value
        FOUNDRY_ACCOUNT_ARM_ID = $outputs.foundryAccountId.value
        FOUNDRY_PROJECT_NAME = $outputs.foundryProjectName.value
        FOUNDRY_PROJECT_ARM_ID = $outputs.foundryProjectId.value
        FOUNDRY_PROJECT_ENDPOINT = $outputs.foundryProjectEndpoint.value
        FOUNDRY_PROJECT_PRINCIPAL_ID = $outputs.foundryProjectPrincipalId.value
        ACR_NAME = $outputs.acrName.value
        ACR_LOGIN_SERVER = $outputs.acrLoginServer.value
        ACR_RESOURCE_ID = $outputs.acrId.value
        APPINSIGHTS_NAME = $outputs.appInsightsName.value
        APPINSIGHTS_RESOURCE_ID = $outputs.appInsightsId.value
        LOG_ANALYTICS_WORKSPACE_ID = $outputs.logAnalyticsWorkspaceId.value
        COORDINATOR_MODEL_DEPLOYMENT = 'gpt-55'
        ANALYST_MODEL_PROVIDER = $provider
        ANALYST_MODEL_DEPLOYMENT = $model
    }
    Write-Host "Foundation ready: $($outputs.foundryAccountName.value) / caldova-control-tower"
    Write-Host "Registry: $($outputs.acrLoginServer.value); analyst: $provider / $model"
    Write-Host '.env updated. New RBAC assignments may take several minutes to propagate.'
} finally {
    Pop-Location
}
