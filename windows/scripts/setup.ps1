<#
.SYNOPSIS
    Tự động tải và thiết lập các gói Portable MỚI NHẤT (Python, Prometheus, Grafana, NSSM) cho Windows.
    Hoàn toàn không yêu cầu quyền cài đặt hệ thống phức tạp và KHÔNG CẦN KHỞI ĐỘNG LẠI MÁY.
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  THIẾT LẬP HỆ THỐNG GIÁM SÁT PORTABLE CHO WINDOWS       " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

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

# ==============================================================================
# 2. KIỂM TRA & TỰ ĐỘNG CÀI ĐẶT BẢN PYTHON 64-BIT MỚI NHẤT (SILENT, 0 REBOOT)
# ==============================================================================
$HasPython = Get-Command python.exe -ErrorAction SilentlyContinue
if (!$HasPython) {
    Write-Host "[CHECK] Đang tìm kiếm phiên bản Python 64-bit mới nhất..." -ForegroundColor Yellow
    $PyUrl = "https://www.python.org/ftp/python/3.12.5/python-3.12.5-amd64.exe"
    $LatestPyVer = "3.12.5"
    try {
        $pyWeb = (Invoke-WebRequest -Uri "https://www.python.org/downloads/windows/" -UseBasicParsing -TimeoutSec 6).Content
        if ($pyWeb -match 'Latest Python 3 Release - Python ([0-9]+\.[0-9]+\.[0-9]+)') {
            $LatestPyVer = $matches[1]
            $PyUrl = "https://www.python.org/ftp/python/$LatestPyVer/python-$LatestPyVer-amd64.exe"
        }
    } catch {
        Write-Host "[INFO] Dùng bản Python 3.12.5 ổn định." -ForegroundColor Gray
    }

    Write-Host "[DOWNLOAD] Tải Python $LatestPyVer installer từ python.org..." -ForegroundColor Yellow
    $PyInstaller = Join-Path $env:TEMP "python-installer.exe"
    Invoke-WebRequest -Uri $PyUrl -OutFile $PyInstaller -UseBasicParsing
    
    Write-Host "[INSTALL] Đang cài đặt Python $LatestPyVer ngầm (0 lần reboot)..." -ForegroundColor Yellow
    Start-Process -FilePath $PyInstaller -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1" -Wait
    Remove-Item $PyInstaller -Force
    
    # Cập nhật PATH cho phiên PowerShell hiện tại
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    Write-Host "[OK] Đã cài đặt xong Python $LatestPyVer!" -ForegroundColor Green
} else {
    Write-Host "[OK] Đã phát hiện Python có sẵn trên máy: $(python --version 2>&1)" -ForegroundColor Green
}

# ==============================================================================
# 3. TỰ ĐỘNG TÌM KIẾM BẢN MỚI NHẤT CỦA PROMETHEUS, GRAFANA & NSSM
# ==============================================================================

Write-Host "`nĐang truy vấn các phiên bản mới nhất từ kho chính thức..." -ForegroundColor Yellow

# A. Prometheus Latest
$PromFinalDir = Join-Path $BinDir "prometheus"
$PromExe = Join-Path $PromFinalDir "prometheus.exe"
if (!(Test-Path $PromExe)) {
    $PromUrl = "https://github.com/prometheus/prometheus/releases/download/v2.54.1/prometheus-2.54.1.windows-amd64.zip"
    try {
        $promRel = Invoke-RestMethod -Uri "https://api.github.com/repos/prometheus/prometheus/releases/latest" -Headers @{"User-Agent"="PowerShell"} -TimeoutSec 6
        $promAsset = $promRel.assets | Where-Object { $_.name -match "windows-amd64\.zip$" } | Select-Object -First 1
        if ($promAsset) {
            $PromUrl = $promAsset.browser_download_url
            Write-Host "[LATEST] Phát hiện Prometheus mới nhất: $($promRel.tag_name)" -ForegroundColor Cyan
        }
    } catch {
        Write-Host "[INFO] Dùng URL Prometheus ổn định." -ForegroundColor Gray
    }

    $ZipPath = Join-Path $BinDir "prometheus.zip"
    Write-Host "[DOWNLOAD] Tải Prometheus từ $PromUrl..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $PromUrl -OutFile $ZipPath -UseBasicParsing
    Write-Host "[EXTRACT] Đang giải nén Prometheus..." -ForegroundColor Yellow
    Expand-Archive -Path $ZipPath -DestinationPath $BinDir -Force
    Remove-Item $ZipPath -Force

    # Tìm thư mục giải nén và đổi tên thành 'prometheus'
    $extractedFolder = Get-ChildItem -Path $BinDir -Directory | Where-Object { $_.Name -like "prometheus-*.windows-amd64" } | Select-Object -First 1
    if ($extractedFolder) {
        if (Test-Path $PromFinalDir) { Remove-Item $PromFinalDir -Recurse -Force }
        Move-Item -Path $extractedFolder.FullName -Destination $PromFinalDir -Force
    }
    Write-Host "[OK] Đã thiết lập xong Prometheus!" -ForegroundColor Green
} else {
    Write-Host "[OK] Prometheus đã có sẵn trong bin\prometheus\." -ForegroundColor Green
}

# B. Grafana Latest
$GrafFinalDir = Join-Path $BinDir "grafana"
$GrafExe = Join-Path $GrafFinalDir "bin\grafana-server.exe"
if (!(Test-Path $GrafExe)) {
    $GrafUrl = "https://dl.grafana.com/oss/release/grafana-11.2.0.windows-amd64.zip"
    try {
        $grafRel = Invoke-RestMethod -Uri "https://api.github.com/repos/grafana/grafana/releases/latest" -Headers @{"User-Agent"="PowerShell"} -TimeoutSec 6
        $grafVer = $grafRel.tag_name.TrimStart('v')
        $GrafUrl = "https://dl.grafana.com/oss/release/grafana-$grafVer.windows-amd64.zip"
        Write-Host "[LATEST] Phát hiện Grafana mới nhất: v$grafVer" -ForegroundColor Cyan
    } catch {
        Write-Host "[INFO] Dùng URL Grafana ổn định." -ForegroundColor Gray
    }

    $ZipPath = Join-Path $BinDir "grafana.zip"
    Write-Host "[DOWNLOAD] Tải Grafana từ $GrafUrl..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $GrafUrl -OutFile $ZipPath -UseBasicParsing
    Write-Host "[EXTRACT] Đang giải nén Grafana..." -ForegroundColor Yellow
    Expand-Archive -Path $ZipPath -DestinationPath $BinDir -Force
    Remove-Item $ZipPath -Force

    # Tìm thư mục giải nén và đổi tên thành 'grafana'
    $extractedFolder = Get-ChildItem -Path $BinDir -Directory | Where-Object { $_.Name -like "grafana-*" -and $_.Name -ne "grafana" } | Select-Object -First 1
    if ($extractedFolder) {
        if (Test-Path $GrafFinalDir) { Remove-Item $GrafFinalDir -Recurse -Force }
        Move-Item -Path $extractedFolder.FullName -Destination $GrafFinalDir -Force
    }
    Write-Host "[OK] Đã thiết lập xong Grafana!" -ForegroundColor Green
} else {
    Write-Host "[OK] Grafana đã có sẵn trong bin\grafana\." -ForegroundColor Green
}

# C. NSSM (Non-Sucking Service Manager)
$NssmFinalDir = Join-Path $BinDir "nssm"
$NssmExe = Join-Path $NssmFinalDir "win64\nssm.exe"
if (!(Test-Path $NssmExe)) {
    $NssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
    $ZipPath = Join-Path $BinDir "nssm.zip"
    Write-Host "[DOWNLOAD] Tải NSSM 2.24..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $NssmUrl -OutFile $ZipPath -UseBasicParsing
    Write-Host "[EXTRACT] Đang giải nén NSSM..." -ForegroundColor Yellow
    Expand-Archive -Path $ZipPath -DestinationPath $BinDir -Force
    Remove-Item $ZipPath -Force

    $extractedFolder = Get-ChildItem -Path $BinDir -Directory | Where-Object { $_.Name -like "nssm-*" -and $_.Name -ne "nssm" } | Select-Object -First 1
    if ($extractedFolder) {
        if (Test-Path $NssmFinalDir) { Remove-Item $NssmFinalDir -Recurse -Force }
        Move-Item -Path $extractedFolder.FullName -Destination $NssmFinalDir -Force
    }
    Write-Host "[OK] Đã thiết lập xong NSSM!" -ForegroundColor Green
} else {
    Write-Host "[OK] NSSM đã có sẵn trong bin\nssm\." -ForegroundColor Green
}

# ==============================================================================
# 4. ĐỒNG BỘ CẤU HÌNH VÀ DASHBOARDS CHO GRAFANA
# ==============================================================================
$GrafProvDir = Join-Path $GrafFinalDir "conf\provisioning"
if (Test-Path $GrafProvDir) {
    $LocalProvDir = Join-Path $BaseDir "config\grafana\provisioning"
    Copy-Item -Path "$LocalProvDir\*" -Destination $GrafProvDir -Recurse -Force
    Write-Host "[CONFIG] Đã đồng bộ Datasource & Dashboard provisioning cho Grafana." -ForegroundColor Green
}

$GrafDashDir = Join-Path $GrafFinalDir "dashboards"
New-Item -ItemType Directory -Force -Path $GrafDashDir | Out-Null
Copy-Item -Path "$BaseDir\dashboards\*.json" -Destination $GrafDashDir -Force
Write-Host "[CONFIG] Đã đồng bộ 4 Dashboards (Blackbox Crash, Thermals, System, Network) vào Grafana." -ForegroundColor Green

# ==============================================================================
# 5. CÀI ĐẶT THƯ VIỆN PYTHON TỪ REQUIREMENTS.TXT
# ==============================================================================
$ReqFile = Join-Path $BaseDir "requirements.txt"
if (Test-Path $ReqFile) {
    Write-Host "`n[PIP] Đang cài đặt thư viện Python từ requirements.txt..." -ForegroundColor Yellow
    $PyExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (!$PyExe) {
        $CommonPaths = @(
            "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
            "C:\Program Files\Python313\python.exe",
            "C:\Program Files\Python312\python.exe",
            "C:\Program Files\Python311\python.exe"
        )
        foreach ($p in $CommonPaths) {
            if (Test-Path $p) { $PyExe = $p; break }
        }
    }
    if ($PyExe) {
        & $PyExe -m pip install --upgrade pip --quiet
        & $PyExe -m pip install -r $ReqFile --quiet
        Write-Host "[OK] Đã cài đặt xong các thư viện Python (psutil, pynvml, prometheus-client, requests)." -ForegroundColor Green
    } else {
        Write-Host "[WARN] Chưa định vị được python.exe để chạy pip install." -ForegroundColor Yellow
    }
}

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host "  THIẾT LẬP HOÀN TẤT VỚI CÁC BẢN MỚI NHẤT (LATEST)!       " -ForegroundColor Green
Write-Host "  Bước tiếp theo: Chạy .\scripts\install-services.ps1    " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Green

