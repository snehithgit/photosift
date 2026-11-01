$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv-laptop/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run Setup-Laptop.ps1 first' }
& $python -m laptop.services status
# 0 means everything is running and ready; 3 means something is not.
exit $LASTEXITCODE
