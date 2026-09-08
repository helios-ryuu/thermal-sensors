# ==============================================================================
# Start Windows Monitoring Services
# ==============================================================================

$Services = @("WindowsThermalAgent", "WindowsPrometheus", "WindowsGrafana")

Write-Host "Starting monitoring services..." -ForegroundColor Cyan
foreach ($s in $Services) {
    try {
        Start-Service -Name $s -ErrorAction Stop
        Write-Host "  [OK] Started: $s" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] Could not start $s : $_" -ForegroundColor Red
    }
}

Write-Host "`nVerifying network port connectivity..." -ForegroundColor Cyan
Start-Sleep -Seconds 2
& "$PSScriptRoot\status.ps1"
