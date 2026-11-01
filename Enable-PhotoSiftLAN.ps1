# Run explicitly from an Administrator PowerShell if Windows Firewall blocks Zima.
# Only Private-profile local-subnet traffic to the authenticated gateway is allowed.
# The agent reaches the gateway on this laptop, so embedding jobs do not need this rule.
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$name = 'PhotoSift-Laptop-Models'
if (-not (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name $name -DisplayName 'PhotoSift laptop model gateway' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8787 -Profile Private -RemoteAddress LocalSubnet
}
$public = Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' }
foreach ($network in $public) {
    Write-Warning "$($network.InterfaceAlias) is set to Public, so this Private-only rule does not apply there. Change it in Windows Settings only if it is your trusted home network."
}
