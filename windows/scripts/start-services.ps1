<#
.SYNOPSIS
    Khởi động đồng thời 3 dịch vụ giám sát Windows.
#>

$Services = @("WindowsThermalAgent", "WindowsPrometheus", "WindowsGrafana")

Write-Host "Đang khởi động các dịch vụ giám sát..." -ForegroundColor Cyan
foreach ($s in $Services) {
    try {
        Start-Service -Name $s -ErrorAction Stop
        Write-Host "  [OK] Đã khởi động: $s" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] Không thể khởi động $s : $_" -ForegroundColor Red
    }
}

Write-Host "`nĐang kiểm tra kết nối các cổng dịch vụ..." -ForegroundColor Cyan
Start-Sleep -Seconds 2
& "$PSScriptRoot\status.ps1"

