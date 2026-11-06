#Requires -Version 5.1
<#
.SYNOPSIS
  Deploys the evaluator Function in the right order: core, private network, core with VNet, code.

.DESCRIPTION
  1. infra/main.bicep           -> Function, plan, private storage, identity, role assignments
  2. infra/private-network.bicep -> VNet, private endpoint and private DNS for the storage account
  3. infra/main.bicep (again)   -> Function VNet integration, using the subnet from step 2
  4. scripts/package.ps1        -> outputs/function.zip
  5. az functionapp deployment source config-zip --build-remote true

  Reuses an existing resource group and Foundry account. Never creates keys: the evaluator API key
  comes from $env:EVALUATOR_API_KEY and travels to Azure in a temporary parameters file that is
  deleted at the end. Nothing is written to Git.

.PARAMETER CheckOnly
  No Azure call: compiles both Bicep templates, validates the parameters and builds the package.

.PARAMETER WhatIf
  Runs "what-if" for step 1 only and stops. Nothing is created.

.PARAMETER SkipPublish
  Deploys the infrastructure but does not push the code.

.EXAMPLE
  $env:EVALUATOR_API_KEY = '<generate a long random value>'
  .\scripts\deploy.ps1 -CheckOnly
  .\scripts\deploy.ps1 -WhatIf
  .\scripts\deploy.ps1
#>
[CmdletBinding()]
param(
    [string]$Parameters = 'infra/main.parameters.json',
    [string]$NetworkParameters = 'infra/private-network.parameters.json',
    [string]$Rubrics = 'examples/rubrics.json',
    [switch]$CheckOnly,
    [switch]$WhatIf,
    [switch]$SkipPublish
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
function Resolve-InRepo([string]$path) { if ([System.IO.Path]::IsPathRooted($path)) { $path } else { Join-Path $root $path } }
$mainBicep = Join-Path $root 'infra/main.bicep'
$networkBicep = Join-Path $root 'infra/private-network.bicep'
$mainParams = Resolve-InRepo $Parameters
$netParams = Resolve-InRepo $NetworkParameters
$rubricsFile = Resolve-InRepo $Rubrics
$tempFiles = @()

function Invoke-Az {
    $output = & az @args --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw "az $($args[0]) $($args[1]) failed (exit code $LASTEXITCODE)." }
    $output
}

function Write-SecretParameters([string]$subnetId) {
    $values = [ordered]@{
        evaluatorApiKey     = @{ value = $env:EVALUATOR_API_KEY }
        evaluatorConfigJson = @{ value = (Get-Content -Raw -Path $rubricsFile) }
    }
    if ($subnetId) { $values.integrationSubnetId = @{ value = $subnetId } }
    $document = [ordered]@{
        '$schema'      = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'
        contentVersion = '1.0.0.0'
        parameters     = $values
    }
    $path = Join-Path ([System.IO.Path]::GetTempPath()) ("decision-eval-" + [guid]::NewGuid().ToString('N') + '.json')
    [System.IO.File]::WriteAllText($path, ($document | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding($false)))
    $script:tempFiles += $path
    $path
}

try {
    foreach ($file in @($mainParams, $netParams, $rubricsFile)) {
        if (-not (Test-Path $file)) { throw "Missing file: $file. Copy the matching *.example.json and fill it in (it stays out of Git)." }
    }
    $p = (Get-Content -Raw $mainParams | ConvertFrom-Json).parameters
    $placeholders = $p.PSObject.Properties | Where-Object { "$($_.Value.value)" -match 'YOUR-|YOURNEW' }
    if ($placeholders) { throw "Replace the placeholders in $Parameters : $($placeholders.Name -join ', ')" }
    $rg = $p.resourceGroupName.value
    $location = $p.location.value
    $functionName = $p.functionName.value

    Write-Host '== Compiling Bicep templates'
    Invoke-Az bicep build --file $mainBicep --stdout | Out-Null
    Invoke-Az bicep build --file $networkBicep --stdout | Out-Null

    if ($CheckOnly) {
        Write-Host '== Building the package'
        & (Join-Path $PSScriptRoot 'package.ps1') | Out-Null
        Write-Host 'Check passed: templates compile, parameters filled, package built. No Azure call was made.'
        return
    }

    if (-not $env:EVALUATOR_API_KEY -or $env:EVALUATOR_API_KEY.Length -lt 32) {
        throw 'Set $env:EVALUATOR_API_KEY to a random value of at least 32 characters (it is never written to Git).'
    }
    $account = Invoke-Az account show --query '{name:name, user:user.name}' -o tsv
    Write-Host "== Azure context: $account"

    if ($WhatIf) {
        Write-Host '== What-if: core deployment (nothing is created)'
        $secret = Write-SecretParameters ''
        Invoke-Az deployment sub what-if --name decision-eval-core --location $location --template-file $mainBicep --parameters "@$mainParams" "@$secret"
        Write-Host 'The network what-if needs the storage account created by step 1, so it is skipped.'
        return
    }

    Write-Host '== 1/5 Core: Function, plan, private storage, identity, roles'
    $secret = Write-SecretParameters ''
    Invoke-Az deployment sub create --name decision-eval-core --location $location --template-file $mainBicep --parameters "@$mainParams" "@$secret" --query properties.outputs.endpoint.value -o tsv | Out-Null

    Write-Host '== 2/5 Private network for the storage account'
    $subnetId = (Invoke-Az deployment group create --resource-group $rg --name decision-eval-network --template-file $networkBicep --parameters "@$netParams" --query properties.outputs.integrationSubnetId.value -o tsv | Select-Object -First 1).Trim()
    if (-not $subnetId) { throw 'The network deployment returned no integration subnet.' }

    Write-Host '== 3/5 Core again: Function VNet integration'
    $secret = Write-SecretParameters $subnetId
    $endpoint = (Invoke-Az deployment sub create --name decision-eval-core --location $location --template-file $mainBicep --parameters "@$mainParams" "@$secret" --query properties.outputs.endpoint.value -o tsv | Select-Object -First 1).Trim()

    if ($SkipPublish) { Write-Host "Infrastructure ready. Endpoint: $endpoint. Code not published (-SkipPublish)."; return }

    Write-Host '== 4/5 Package'
    $zip = & (Join-Path $PSScriptRoot 'package.ps1') | Select-Object -Last 1

    Write-Host '== 5/5 Publish with remote dependency installation'
    Invoke-Az functionapp deployment source config-zip --resource-group $rg --name $functionName --src $zip --build-remote true | Out-Null

    Write-Host ''
    Write-Host "Deployed. Endpoint: $endpoint"
    Write-Host "Check:     curl $endpoint/health"
    Write-Host 'Next:      register the evaluators (dry-run first), see "Use in Foundry" in the README.'
    Write-Host 'Note:      sending the package is not proof that the Function is live; check /health and run one evaluation.'
}
finally {
    foreach ($file in $tempFiles) { if (Test-Path $file) { Remove-Item $file -Force } }
}
