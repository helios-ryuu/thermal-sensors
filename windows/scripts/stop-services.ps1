# ==============================================================================
# Stop Windows Monitoring Services
# ==============================================================================

$Services = @("WindowsGrafana", "WindowsPrometheus", "WindowsThermalAgent")

Write-Host "Stopping monitoring services..." -ForegroundColor Cyan
foreach ($s in $Services) {
    try {
        Stop-Service -Name $s -Force -ErrorAction SilentlyContinue
        Write-Host "  [STOPPED] Stopped: $s" -ForegroundColor Yellow
    } catch {
        Write-Host "  [INFO] $s is already stopped or not installed." -ForegroundColor Gray
    }
}
