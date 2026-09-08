# ==============================================================================
# Uninstall Windows Monitoring Services via NSSM or sc.exe
# ==============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir
$NssmExe = Join-Path $BaseDir "bin\nssm\win64\nssm.exe"

$Services = @("WindowsGrafana", "WindowsPrometheus", "WindowsThermalAgent", "WindowsLHM")

Write-Host "Stopping and removing monitoring services..." -ForegroundColor Yellow

foreach ($s in $Services) {
    try {
        Stop-Service -Name $s -Force -ErrorAction SilentlyContinue
    } catch {}

    if (Test-Path $NssmExe) {
        & $NssmExe remove $s confirm 2>$null
        Write-Host "  [REMOVED] Removed: $s" -ForegroundColor Green
    } else {
        sc.exe delete $s | Out-Null
        Write-Host "  [REMOVED via sc] Removed: $s" -ForegroundColor Green
    }
}

try {
    Get-NetFirewallRule -DisplayName "Monitoring Stack*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    netsh interface portproxy reset | Out-Null
    Write-Host "  [CLEANUP] Removed firewall rules and portproxy entries." -ForegroundColor Green
} catch {}

Write-Host "`nSuccessfully uninstalled all monitoring services from Windows." -ForegroundColor Green
