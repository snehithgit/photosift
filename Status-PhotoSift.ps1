$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv-laptop/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run Setup-Laptop.ps1 first' }
& $python -m laptop.services status
if ($LASTEXITCODE) { throw 'Status failed' }
