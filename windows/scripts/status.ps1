<#
.SYNOPSIS
    Kiểm tra trạng thái hoạt động, cổng lắng nghe và log gần nhất của hệ thống giám sát.
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  TRẠNG THÁI DỊCH VỤ GIÁM SÁT WINDOWS                     " -ForegroundColor Cyan
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
        Write-Host "CHƯA CÀI ĐẶT" -ForegroundColor Yellow
    }
}

Write-Host "`n----------------------------------------------------------" -ForegroundColor Gray
Write-Host "KIỂM TRA CỔNG LẮNG NGHE:" -ForegroundColor Cyan

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
        Write-Host "  [OFFLINE] Port $($p.Port) ($($p.Name)) chưa sẵn sàng" -ForegroundColor Red
    }
}

Write-Host "`n----------------------------------------------------------" -ForegroundColor Gray
Write-Host "LOG MỚI NHẤT TỪ AGENT (logs\agent.log):" -ForegroundColor Cyan
$AgentLog = Join-Path $BaseDir "logs\agent.log"
if (Test-Path $AgentLog) {
    Get-Content $AgentLog -Tail 15 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
} else {
    Write-Host "  (Chưa có file log)" -ForegroundColor Gray
}
Write-Host "==========================================================" -ForegroundColor Cyan

