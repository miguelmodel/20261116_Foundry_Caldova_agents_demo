$CaldovaRoot = Split-Path $PSScriptRoot -Parent
$CaldovaPython = Join-Path $CaldovaRoot '.venv\Scripts\python.exe'

# Per-project Azure CLI profile (AZURE_CONFIG_DIR in .env), isolated from other projects' az context.
$caldovaEnvFile = Join-Path $CaldovaRoot '.env'
if (Test-Path -LiteralPath $caldovaEnvFile) {
    $profileLine = Select-String -LiteralPath $caldovaEnvFile -Pattern '^\s*AZURE_CONFIG_DIR\s*=\s*(.+)$' | Select-Object -First 1
    if ($profileLine) {
        $env:AZURE_CONFIG_DIR = [Environment]::ExpandEnvironmentVariables($profileLine.Matches[0].Groups[1].Value.Trim().Trim("'", '"'))
    }
}

function Invoke-CaldovaAz {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $result = & az @Arguments --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed ($($Arguments[0])). See the error above." }
    return ($result -join "`n")
}

function Invoke-CaldovaPython {
    param([Parameter(Mandatory)][string[]]$Arguments)
    & $CaldovaPython @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python failed: $($Arguments[0])" }
}

function Get-CaldovaEnv {
    $json = & $CaldovaPython (Join-Path $PSScriptRoot 'common.py') read
    if ($LASTEXITCODE -ne 0) { throw 'Could not load .env.' }
    return (($json -join "`n") | ConvertFrom-Json -AsHashtable)
}

function Set-CaldovaEnv {
    param([Parameter(Mandatory)][hashtable]$Values)
    $Values | ConvertTo-Json -Depth 10 | & $CaldovaPython (Join-Path $PSScriptRoot 'common.py') update
    if ($LASTEXITCODE -ne 0) { throw 'Could not update .env.' }
}

function Assert-CaldovaConfig {
    param([hashtable]$Values, [string[]]$Keys)
    foreach ($key in $Keys) {
        if ([string]::IsNullOrWhiteSpace($Values[$key]) -or $Values[$key].Contains('<')) {
            throw "Missing $key in .env. Run scripts\provision.ps1 first."
        }
    }
}

function Assert-CaldovaSubscriptionEnabled {
    param([Parameter(Mandatory)][string]$SubscriptionId)
    $subscription = Invoke-CaldovaAz -Arguments @(
        'rest', '--method', 'get', '--url',
        "https://management.azure.com/subscriptions/$($SubscriptionId)?api-version=2022-12-01",
        '--output', 'json'
    ) | ConvertFrom-Json
    if ($subscription.state -ne 'Enabled') {
        throw "Subscription $SubscriptionId is '$($subscription.state)', not Enabled. No writes attempted. Re-enable the subscription before deploying."
    }
    return $subscription
}

function Get-CaldovaTags {
    param([string]$Path = (Join-Path $CaldovaRoot 'infra\tags.local.json'))
    $tags = @{ project = 'caldova-control-tower'; environment = 'demo' }
    if (Test-Path -LiteralPath $Path) {
        $extra = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -AsHashtable
        if ($extra -isnot [System.Collections.IDictionary]) {
            throw 'The optional extra tags from infra/tags.local.json must be a flat JSON object.'
        }
        foreach ($entry in $extra.GetEnumerator()) {
            if ($entry.Value -isnot [string] -or [string]::IsNullOrWhiteSpace($entry.Key)) {
                throw 'The optional extra tags from infra/tags.local.json must have nonempty names and string values.'
            }
            $tags[$entry.Key] = $entry.Value
        }
    }
    return $tags
}

function Assert-CaldovaModelQuota {
    # Fails fast, before anything is created, when the region lacks model quota for the requested capacity.
    param(
        [Parameter(Mandatory)][string]$SubscriptionId,
        [Parameter(Mandatory)][string]$Location,
        [Parameter(Mandatory)][string]$ResourceGroup,
        [Parameter(Mandatory)][hashtable]$Required
    )
    try {
        $usages = (Invoke-CaldovaAz -Arguments @(
            'rest', '--method', 'get', '--url',
            "https://management.azure.com/subscriptions/$SubscriptionId/providers/Microsoft.CognitiveServices/locations/$Location/usages?api-version=2024-10-01",
            '--output', 'json'
        ) | ConvertFrom-Json).value
    } catch {
        Write-Warning "Could not read model quota for $Location. Azure will still check quota during deployment."
        return
    }
    # Capacity already held by this demo's deployments in the target resource group is reused, not new.
    $existing = @{}
    if ((Invoke-CaldovaAz -Arguments @('group', 'exists', '--name', $ResourceGroup)) -eq 'true') {
        $accounts = Invoke-CaldovaAz -Arguments @(
            'cognitiveservices', 'account', 'list', '--resource-group', $ResourceGroup, '--query', '[].name', '--output', 'tsv'
        )
        foreach ($account in @($accounts -split "`r?`n" | Where-Object { $_.Trim() })) {
            $deployments = Invoke-CaldovaAz -Arguments @(
                'cognitiveservices', 'account', 'deployment', 'list', '--resource-group', $ResourceGroup,
                '--name', $account.Trim(), '--query', '[].{name:name,capacity:sku.capacity}', '--output', 'json'
            ) | ConvertFrom-Json
            foreach ($deployment in @($deployments)) {
                $existing[$deployment.name] = [int]$existing[$deployment.name] + [int]$deployment.capacity
            }
        }
    }
    $problems = @()
    $fixes = @()
    foreach ($usageName in $Required.Keys) {
        $need = $Required[$usageName]
        $usage = @($usages | Where-Object { $_.name.value -eq $usageName }) | Select-Object -First 1
        if (-not $usage) {
            Write-Warning "No quota information for $($need.Label) in $Location. If deployment fails, choose another -Location."
            continue
        }
        $available = [int]$usage.limit - [int]$usage.currentValue + [int]$existing[$need.Deployment]
        if ($need.Capacity -gt $available) {
            $problems += "  - $($need.Label): needs $($need.Capacity)K tokens per minute, but only $([Math]::Max(0, $available))K are available (quota $([int]$usage.limit)K, already in use $([int]$usage.currentValue)K)."
            if ($available -gt 0) { $fixes += "$($need.Parameter) $available" }
        }
    }
    if ($problems) {
        $fix = if ($fixes) { "Re-run with lower capacity, for example: .\scripts\deploy_all.ps1 $($fixes -join ' ')`n  Or choose" } else { 'Choose' }
        throw ("Not enough model quota in $Location for subscription $SubscriptionId. Nothing was deployed.`n" +
            ($problems -join "`n") + "`n  $fix another region with -Location, or request more quota for the model in the Azure portal.")
    }
}
