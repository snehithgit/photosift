$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv-laptop/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Laptop environment does not exist' }
& $python -m laptop.services stop
if ($LASTEXITCODE) { throw 'Stop failed' }
