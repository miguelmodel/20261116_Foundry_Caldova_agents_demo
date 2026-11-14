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
