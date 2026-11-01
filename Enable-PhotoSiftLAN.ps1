# Run explicitly from an Administrator PowerShell if Windows Firewall blocks Zima.
# Only Private-profile local-subnet traffic to the authenticated gateway is allowed.
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$name = 'PhotoSift-Laptop-Models'
if (-not (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name $name -DisplayName 'PhotoSift laptop model gateway' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8787 -Profile Private -RemoteAddress LocalSubnet
}
