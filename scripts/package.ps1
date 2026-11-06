#Requires -Version 5.1
<#
.SYNOPSIS
  Builds the Azure Functions source package (outputs/function.zip).

.DESCRIPTION
  The zip holds only what the Function needs: function_app.py, host.json, requirements.txt,
  pyproject.toml and src/. Dependencies are installed remotely at publish time, so no .venv,
  tests, examples, docs or local configuration ever leaves the machine. Entry names use "/"
  so the archive also works on the Linux host.
#>
param(
    [string]$Output = (Join-Path $PSScriptRoot '..\outputs\function.zip')
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$output = [System.IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Force -Path (Split-Path $output) | Out-Null
if (Test-Path $output) { Remove-Item $output -Force }

$files = @('function_app.py', 'host.json', 'requirements.txt', 'pyproject.toml') | ForEach-Object { Join-Path $root $_ }
$files += Get-ChildItem -Path (Join-Path $root 'src') -Recurse -File |
    Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -ne '.pyc' } |
    ForEach-Object { $_.FullName }

$zip = [System.IO.Compression.ZipFile]::Open($output, 'Create')
try {
    foreach ($file in $files) {
        if (-not (Test-Path $file)) { throw "Missing file for the package: $file" }
        $entry = $file.Substring($root.Length).TrimStart('\', '/').Replace('\', '/')
        [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file, $entry, 'Optimal')
    }
}
finally { $zip.Dispose() }

Write-Host "Package: $output ($([math]::Round((Get-Item $output).Length / 1KB)) KB, $($files.Count) files)"
$output
