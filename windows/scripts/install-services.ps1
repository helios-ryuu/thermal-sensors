<#
.SYNOPSIS
    Đăng ký 3 tiến trình thành Windows Services tự chạy ngầm cùng máy bằng NSSM.
    Không cần đăng nhập màn hình GUI, an toàn tuyệt đối cho máy vận hành từ xa.
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir
$NssmExe = Join-Path $BaseDir "bin\nssm\win64\nssm.exe"

if (!(Test-Path $NssmExe)) {
    Write-Host "[ERROR] Không tìm thấy nssm.exe. Vui lòng chạy .\scripts\setup.ps1 trước!" -ForegroundColor Red
    exit 1
}

# Tìm Python executable
$PythonCmd = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (!$PythonCmd) {
    $CommonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe",
        "C:\Program Files\Python310\python.exe"
    )
    foreach ($p in $CommonPaths) {
        if (Test-Path $p) {
            $PythonCmd = $p
            break
        }
    }
}
if (!$PythonCmd) {
    $PythonCmd = "python.exe"
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  ĐĂNG KÝ WINDOWS SERVICES BẰNG NSSM                     " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Service: WindowsThermalAgent
$AgentService = "WindowsThermalAgent"
Write-Host "[INSTALL] Đăng ký Service: $AgentService..." -ForegroundColor Yellow
& $NssmExe install $AgentService $PythonCmd (Join-Path $BaseDir "agent.py")
& $NssmExe set $AgentService AppDirectory $BaseDir
& $NssmExe set $AgentService AppStdout (Join-Path $BaseDir "logs\agent.log")
& $NssmExe set $AgentService AppStderr (Join-Path $BaseDir "logs\agent.log")
& $NssmExe set $AgentService AppRotateFiles 1
& $NssmExe set $AgentService AppRotateBytes 10485760 # 10MB
& $NssmExe set $AgentService Start SERVICE_AUTO_START

# 2. Service: WindowsPrometheus
$PromService = "WindowsPrometheus"
$PromExe = Join-Path $BaseDir "bin\prometheus\prometheus.exe"
$PromCfg = Join-Path $BaseDir "config\prometheus.yml"
$PromData = Join-Path $BaseDir "data\prometheus"
$PromArgs = "--config.file=`"$PromCfg`" --storage.tsdb.path=`"$PromData`" --storage.tsdb.retention.time=30d --web.listen-address=`"0.0.0.0:9090`""

Write-Host "[INSTALL] Đăng ký Service: $PromService..." -ForegroundColor Yellow
& $NssmExe install $PromService $PromExe $PromArgs
& $NssmExe set $PromService AppDirectory (Join-Path $BaseDir "bin\prometheus")
& $NssmExe set $PromService AppStdout (Join-Path $BaseDir "logs\prometheus.log")
& $NssmExe set $PromService AppStderr (Join-Path $BaseDir "logs\prometheus.log")
& $NssmExe set $PromService Start SERVICE_AUTO_START

# 3. Service: WindowsGrafana
$GrafService = "WindowsGrafana"
$GrafExe = Join-Path $BaseDir "bin\grafana\bin\grafana-server.exe"
$GrafHome = Join-Path $BaseDir "bin\grafana"
$GrafArgs = "--homepath=`"$GrafHome`""

Write-Host "[INSTALL] Đăng ký Service: $GrafService..." -ForegroundColor Yellow
& $NssmExe install $GrafService $GrafExe $GrafArgs
& $NssmExe set $GrafService AppDirectory $GrafHome
& $NssmExe set $GrafService AppStdout (Join-Path $BaseDir "logs\grafana.log")
& $NssmExe set $GrafService AppStderr (Join-Path $BaseDir "logs\grafana.log")
& $NssmExe set $GrafService Start SERVICE_AUTO_START

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  ĐĂNG KÝ THÀNH CÔNG 3 WINDOWS SERVICES!                 " -ForegroundColor Green
Write-Host "  Để khởi động, hãy chạy: .\scripts\start-services.ps1   " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Green

