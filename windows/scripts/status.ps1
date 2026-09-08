# ==============================================================================
# Check Windows Monitoring Services Status and Logs
# ==============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  WINDOWS MONITORING SERVICES STATUS                     " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$Services = @("WindowsThermalAgent", "WindowsPrometheus", "WindowsGrafana")
foreach ($s in $Services) {
    $svc = Get-Service -Name $s -ErrorAction SilentlyContinue
    if ($svc) {
        $color = if ($svc.Status -eq "Running") { "Green" } else { "Red" }
        Write-Host "Service $s : " -NoNewline
        Write-Host "$($svc.Status)" -ForegroundColor $color
    } else {
        Write-Host "Service $s : " -NoNewline
        Write-Host "NOT INSTALLED" -ForegroundColor Yellow
    }
}

Write-Host "`n----------------------------------------------------------" -ForegroundColor Gray
Write-Host "CHECKING LISTENING PORTS:" -ForegroundColor Cyan

$Ports = @(
    @{ Name = "Agent Metrics"; Port = 9100; Url = "http://127.0.0.1:9100/metrics" },
    @{ Name = "Prometheus UI"; Port = 9090; Url = "http://127.0.0.1:9090" },
    @{ Name = "Grafana Dashboard"; Port = 3000; Url = "http://127.0.0.1:3000" }
)

foreach ($p in $Ports) {
    $conn = Test-NetConnection -ComputerName 127.0.0.1 -Port $p.Port -WarningAction SilentlyContinue
    if ($conn.TcpTestSucceeded) {
        Write-Host "  [ONLINE] Port $($p.Port) ($($p.Name)) -> $($p.Url)" -ForegroundColor Green
    } else {
        Write-Host "  [OFFLINE] Port $($p.Port) ($($p.Name)) not reachable yet" -ForegroundColor Red
    }
}

Write-Host "`n----------------------------------------------------------" -ForegroundColor Gray
Write-Host "LATEST LOG FROM AGENT (logs\agent.log):" -ForegroundColor Cyan
$AgentLog = Join-Path $BaseDir "logs\agent.log"
if (Test-Path $AgentLog) {
    Get-Content $AgentLog -Tail 15 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
} else {
    Write-Host "  (Log file not created yet)" -ForegroundColor Gray
}
Write-Host "==========================================================" -ForegroundColor Cyan
