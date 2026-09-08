# ==============================================================================
# Start / Restart Windows Monitoring Services
# ==============================================================================

$Services = @("WindowsThermalAgent", "WindowsPrometheus", "WindowsGrafana")

Write-Host "Starting / Restarting monitoring services..." -ForegroundColor Cyan
foreach ($s in $Services) {
    try {
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

Write-Host "`nWaiting for services to finish starting..." -ForegroundColor Cyan
for ($i = 0; $i -lt 8; $i++) {
    Start-Sleep -Seconds 1
    $c = Test-NetConnection -ComputerName 127.0.0.1 -Port 3000 -WarningAction SilentlyContinue
    if ($c.TcpTestSucceeded) { break }
}
& "$PSScriptRoot\status.ps1"
