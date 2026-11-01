$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv-laptop/Scripts/python.exe')) {
    & py -3.12 -m venv .venv-laptop
    if ($LASTEXITCODE) { throw 'Python 3.12 is required' }
}
$python = Join-Path $PSScriptRoot '.venv-laptop/Scripts/python.exe'
& $python -m pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cu130
if ($LASTEXITCODE) { throw 'CUDA PyTorch installation failed' }
& $python -m pip install -r requirements-laptop.lock
if ($LASTEXITCODE) { throw 'Laptop dependencies failed' }
& $python -m pip install --no-deps -e .
if ($LASTEXITCODE) { throw 'PhotoSift installation failed' }
& $python -m laptop.check_gpu
if ($LASTEXITCODE) { throw 'GPU execution check failed' }
$env:HF_HUB_DISABLE_XET = '1'
& $python -m laptop.setup
if ($LASTEXITCODE) { throw 'Pinned model setup failed' }
Write-Host 'Ready to run .\Start-PhotoSift.ps1'
