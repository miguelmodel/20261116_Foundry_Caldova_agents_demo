[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Push-Location $CaldovaRoot
try {
    $null = New-Item -ItemType Directory -Path (Join-Path $CaldovaRoot '.deploy') -Force
    $compiled = Join-Path $CaldovaRoot '.deploy\main.check.json'
    try {
        $null = Invoke-CaldovaAz -Arguments @(
            'bicep', 'build', '--file', (Join-Path $CaldovaRoot 'infra\main.bicep'), '--outfile', $compiled
        )
    } finally {
        Remove-Item -LiteralPath $compiled -Force -ErrorAction SilentlyContinue
    }
    foreach ($file in Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1') {
        $tokens = $null
        $parseErrors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors.Count) { throw "$($file.Name): $($parseErrors -join '; ')" }
        Write-Host "Parsed $($file.Name)"
    }
    $pythonScripts = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.py' | ForEach-Object { $_.FullName })
    Invoke-CaldovaPython -Arguments (@('-m', 'py_compile') + $pythonScripts)
    Invoke-CaldovaPython -Arguments @((Join-Path $PSScriptRoot 'verify_sdk.py'))
    Invoke-CaldovaPython -Arguments @('-m', 'unittest', 'discover', '-s', 'scripts', '-p', 'test_setup_offline.py', '-v')
    & (Join-Path $PSScriptRoot 'test_provision_offline.ps1')

    $fixture = Join-Path $CaldovaRoot ('.deploy\tags-test-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $fixture
    try {
        $path = Join-Path $fixture 'tags.local.json'
        $base = Get-CaldovaTags -Path $path
        if ($base.Count -ne 2) { throw 'Base tag test failed.' }
        @{ fixture = 'offline'; project = 'override' } | ConvertTo-Json | Set-Content -LiteralPath $path
        $merged = Get-CaldovaTags -Path $path
        if ($merged.Count -ne 3 -or $merged.project -ne 'override') { throw 'Tag merge test failed.' }
        '{"fixture":{"not":"flat"}}' | Set-Content -LiteralPath $path
        $rejected = $false
        try { $null = Get-CaldovaTags -Path $path } catch { $rejected = $true }
        if (-not $rejected) { throw 'Nested tag values must be rejected.' }
        Write-Host 'PASS: optional tag merge (missing, present, invalid); no tag values displayed.'
    } finally {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
    Write-Host 'PASS: offline verification completed. No Azure resource writes were attempted.'
} finally {
    Pop-Location
}
