<#
.SYNOPSIS
    Dừng đồng thời 3 dịch vụ giám sát Windows.
#>

$Services = @("WindowsGrafana", "WindowsPrometheus", "WindowsThermalAgent")

Write-Host "Đang dừng các dịch vụ giám sát..." -ForegroundColor Cyan
foreach ($s in $Services) {
    try {
        Stop-Service -Name $s -Force -ErrorAction SilentlyContinue
        Write-Host "  [STOPPED] Đã dừng: $s" -ForegroundColor Yellow
    } catch {
        Write-Host "  [INFO] $s đã dừng hoặc chưa chạy." -ForegroundColor Gray
    }
}

