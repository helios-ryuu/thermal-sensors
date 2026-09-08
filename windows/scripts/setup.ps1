<#
.SYNOPSIS
    Tự động tải và thiết lập các gói Portable (Prometheus, Grafana, NSSM) cho Windows.
    Hoàn toàn không yêu cầu quyền cài đặt hệ thống phức tạp và KHÔNG CẦN KHỞI ĐỘNG LẠI MÁY.
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  THIẾT LẬP HỆ THỐNG GIÁM SÁT PORTABLE CHO WINDOWS       " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Tạo cấu trúc thư mục
$BinDir = Join-Path $BaseDir "bin"
$LogsDir = Join-Path $BaseDir "logs"
$DataDir = Join-Path $BaseDir "data"
$PromDataDir = Join-Path $DataDir "prometheus"
$GrafDataDir = Join-Path $DataDir "grafana"

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
New-Item -ItemType Directory -Force -Path $PromDataDir | Out-Null
New-Item -ItemType Directory -Force -Path $GrafDataDir | Out-Null

# 2. Cấu hình các gói cần tải
$PromVersion = "2.51.2"
$GrafVersion = "10.4.1"
$NssmVersion = "2.24"

# Link tải chính thức
$PromUrl = "https://github.com/prometheus/prometheus/releases/download/v$PromVersion/prometheus-$PromVersion.windows-amd64.zip"
$GrafUrl = "https://dl.grafana.com/oss/release/grafana-$GrafVersion.windows-amd64.zip"
$NssmUrl = "https://nssm.cc/release/nssm-$NssmVersion.zip"

# Hàm tải và giải nén
function Download-And-Extract ($Url, $ZipName, $ExtractDir, $CheckFile) {
    $TargetCheck = Join-Path $ExtractDir $CheckFile
    if (Test-Path $TargetCheck) {
        Write-Host "[OK] $CheckFile đã tồn tại sẵn, bỏ qua tải." -ForegroundColor Green
        return
    }

    $ZipPath = Join-Path $BinDir $ZipName
    Write-Host "[DOWNLOAD] Đang tải $ZipName từ $Url..." -ForegroundColor Yellow
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Url -OutFile $ZipPath -UseBasicParsing

    Write-Host "[EXTRACT] Đang giải nén $ZipName..." -ForegroundColor Yellow
    Expand-Archive -Path $ZipPath -DestinationPath $BinDir -Force
    Remove-Item $ZipPath -Force
}

# Tải Prometheus
$PromExtractDir = Join-Path $BinDir "prometheus-$PromVersion.windows-amd64"
Download-And-Extract -Url $PromUrl -ZipName "prometheus.zip" -ExtractDir $PromExtractDir -CheckFile "prometheus.exe"
# Tạo alias thư mục chuẩn
$PromFinalDir = Join-Path $BinDir "prometheus"
if (!(Test-Path $PromFinalDir) -and (Test-Path $PromExtractDir)) {
    Rename-Item -Path $PromExtractDir -NewName "prometheus"
}

# Tải Grafana
$GrafExtractDir = Join-Path $BinDir "grafana-v$GrafVersion"
Download-And-Extract -Url $GrafUrl -ZipName "grafana.zip" -ExtractDir $GrafExtractDir -CheckFile "bin\grafana-server.exe"
$GrafFinalDir = Join-Path $BinDir "grafana"
if (!(Test-Path $GrafFinalDir) -and (Test-Path $GrafExtractDir)) {
    Rename-Item -Path $GrafExtractDir -NewName "grafana"
}

# Tải NSSM
$NssmExtractDir = Join-Path $BinDir "nssm-$NssmVersion"
Download-And-Extract -Url $NssmUrl -ZipName "nssm.zip" -ExtractDir $NssmExtractDir -CheckFile "win64\nssm.exe"
$NssmFinalDir = Join-Path $BinDir "nssm"
if (!(Test-Path $NssmFinalDir) -and (Test-Path $NssmExtractDir)) {
    Rename-Item -Path $NssmExtractDir -NewName "nssm"
}

# 3. Đồng bộ cấu hình provisioning của Grafana
$GrafProvDir = Join-Path $GrafFinalDir "conf\provisioning"
if (Test-Path $GrafProvDir) {
    $LocalProvDir = Join-Path $BaseDir "config\grafana\provisioning"
    Copy-Item -Path "$LocalProvDir\*" -Destination $GrafProvDir -Recurse -Force
    Write-Host "[CONFIG] Đã cấu hình Datasource & Dashboard provisioning cho Grafana." -ForegroundColor Green
}

# Tạo symlink hoặc copy dashboards vào thư mục Grafana
$GrafDashDir = Join-Path $GrafFinalDir "dashboards"
New-Item -ItemType Directory -Force -Path $GrafDashDir | Out-Null
Copy-Item -Path "$BaseDir\dashboards\*.json" -Destination $GrafDashDir -Force
Write-Host "[CONFIG] Đã đồng bộ các Dashboards vào Grafana." -ForegroundColor Green

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  HOÀN TẤT THIẾT LẬP CÁC GÓI PORTABLE!                   " -ForegroundColor Green
Write-Host "  Bước kế tiếp: Chạy .\scripts\install-services.ps1      " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Green

