<#
.SYNOPSIS
    Gỡ bỏ hoàn toàn 3 Windows Services khỏi hệ thống bằng NSSM.
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir
$NssmExe = Join-Path $BaseDir "bin\nssm\win64\nssm.exe"

$Services = @("WindowsGrafana", "WindowsPrometheus", "WindowsThermalAgent")

Write-Host "Đang dừng và gỡ bỏ các dịch vụ giám sát..." -ForegroundColor Yellow

foreach ($s in $Services) {
    try {
        Stop-Service -Name $s -Force -ErrorAction SilentlyContinue
    } catch {}

    if (Test-Path $NssmExe) {
        & $NssmExe remove $s confirm 2>$null
        Write-Host "  [REMOVED] Đã gỡ bỏ: $s" -ForegroundColor Green
    } else {
        # Fallback sc.exe
        sc.exe delete $s | Out-Null
        Write-Host "  [REMOVED via sc] Đã gỡ bỏ: $s" -ForegroundColor Green
    }
}

Write-Host "`nĐã gỡ bỏ sạch sẽ các dịch vụ giám sát khỏi hệ thống." -ForegroundColor Green

