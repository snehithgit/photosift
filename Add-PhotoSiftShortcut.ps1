$ErrorActionPreference = 'Stop'
# A desktop shortcut only: it opens when you click it, and Windows never launches it by itself.
$python = Join-Path $PSScriptRoot '.venv-laptop/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run Setup-Laptop.ps1 first' }
$path = Join-Path ([Environment]::GetFolderPath('Desktop')) 'PhotoSift Agent.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($path)
$shortcut.TargetPath = $python
$shortcut.Arguments = '-m laptop.control'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = 'Start and stop PhotoSift on this laptop'
$shortcut.Save()
Write-Host "Created $path"
