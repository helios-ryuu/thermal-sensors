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

# Tailscale Remote Access Detection
$tsIp = $null
try {
    $tsCmd = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($tsCmd) {
        $tsIp = (& $tsCmd.Source ip -4 2>$null).Trim()
    }
} catch {}
if (!$tsIp) {
    $tsAdapter = Get-NetIPAddress -InterfaceAlias "*tailscale*" -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($tsAdapter) {
        $tsIp = $tsAdapter.IPAddress
    }
}

if ($tsIp) {
    Write-Host "`n----------------------------------------------------------" -ForegroundColor Gray
    Write-Host "TAILSCALE REMOTE ACCESS (IP: $tsIp):" -ForegroundColor Cyan
    Write-Host "  Grafana Dashboard : http://$($tsIp):3000" -ForegroundColor Green
    Write-Host "  Prometheus UI     : http://$($tsIp):9090" -ForegroundColor Green
    Write-Host "  Agent Metrics     : http://$($tsIp):9100/metrics" -ForegroundColor Green
}

Write-Host "`n----------------------------------------------------------" -ForegroundColor Gray
Write-Host "LATEST LOG FROM AGENT (logs\agent.log):" -ForegroundColor Cyan
$AgentLog = Join-Path $BaseDir "logs\agent.log"
if (Test-Path $AgentLog) {
    Get-Content $AgentLog -Tail 15 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
} else {
    Write-Host "  (Log file not created yet)" -ForegroundColor Gray
}

$grafSvc = Get-Service -Name "WindowsGrafana" -ErrorAction SilentlyContinue
$GrafLog = Join-Path $BaseDir "logs\grafana.log"
if ($grafSvc -and $grafSvc.Status -ne "Running" -and (Test-Path $GrafLog)) {
    Write-Host "`n----------------------------------------------------------" -ForegroundColor Gray
    Write-Host "LATEST LOG FROM GRAFANA (logs\grafana.log):" -ForegroundColor Yellow
    Get-Content $GrafLog -Tail 15 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
}
Write-Host "==========================================================" -ForegroundColor Cyan
