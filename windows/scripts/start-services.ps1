# ==============================================================================
# Start Windows Monitoring Services
# Start / Restart Windows Monitoring Services
# ==============================================================================

$Services = @("WindowsThermalAgent", "WindowsPrometheus", "WindowsGrafana")

Write-Host "Starting monitoring services..." -ForegroundColor Cyan
Write-Host "Starting / Restarting monitoring services..." -ForegroundColor Cyan
foreach ($s in $Services) {
    try {
        Start-Service -Name $s -ErrorAction Stop
        Write-Host "  [OK] Started: $s" -ForegroundColor Green
        $svc = Get-Service -Name $s -ErrorAction SilentlyContinue
        if ($svc) {
            if ($svc.Status -eq "Running") {
                Restart-Service -Name $s -Force -ErrorAction Stop
                Write-Host "  [OK] Restarted: $s" -ForegroundColor Green
            } else {
                Start-Service -Name $s -ErrorAction Stop
                Write-Host "  [OK] Started: $s" -ForegroundColor Green
            }
        } else {
            Write-Host "  [WARN] Service $s not installed." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  [FAIL] Could not start $s : $_" -ForegroundColor Red
    }
}

Write-Host "`nVerifying network port connectivity..." -ForegroundColor Cyan
Start-Sleep -Seconds 2
Start-Sleep -Seconds 3
& "$PSScriptRoot\status.ps1"
