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
Write-Host "DATA PIPELINE HEALTH CHECK:" -ForegroundColor Cyan

# 1. Test Agent
try {
    $agentResp = Invoke-WebRequest -Uri "http://127.0.0.1:9100/metrics" -UseBasicParsing -TimeoutSec 3
    if ($agentResp.StatusCode -eq 200) {
        $lines = ($agentResp.Content -split "`n").Count
        Write-Host "  [AGENT] Metrics HTTP 200 OK ($lines metric lines streaming)" -ForegroundColor Green
    }
} catch {
    Write-Host "  [AGENT] Could not query http://127.0.0.1:9100/metrics : $_" -ForegroundColor Red
}

# 2. Test Prometheus Target
try {
    $promTargets = Invoke-RestMethod -Uri "http://127.0.0.1:9090/api/v1/targets" -TimeoutSec 3
    $t = $promTargets.data.activeTargets | Where-Object { $_.scrapeUrl -like "*9100*" } | Select-Object -First 1
    if ($t) {
        $hColor = if ($t.health -eq "up") { "Green" } else { "Red" }
        Write-Host "  [PROMETHEUS] Target $($t.scrapeUrl) Health: $($t.health)" -ForegroundColor $hColor
    } else {
        Write-Host "  [PROMETHEUS] No target matching port 9100 found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  [PROMETHEUS] Could not query Prometheus targets API" -ForegroundColor Yellow
}

# 3. Test Grafana Datasource
try {
    $auth = "Basic " + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("admin:admin"))
    $grafDs = Invoke-RestMethod -Uri "http://127.0.0.1:3000/api/datasources" -Headers @{ Authorization = $auth } -TimeoutSec 3
    $pDs = $grafDs | Where-Object { $_.type -eq "prometheus" } | Select-Object -First 1
    if ($pDs) {
        Write-Host "  [GRAFANA] Datasource: $($pDs.name) | UID: $($pDs.uid) (Target: $($pDs.url))" -ForegroundColor Green
    } else {
        Write-Host "  [GRAFANA] No Prometheus datasource provisioned!" -ForegroundColor Red
    }
} catch {
    Write-Host "  [GRAFANA] Could not query Grafana datasources API (Grafana might be starting)" -ForegroundColor Gray
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
