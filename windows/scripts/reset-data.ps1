# ==============================================================================
# Reset Collected Monitoring Data (Prometheus TSDB and Logs)
# Starts fresh time-series metrics collection from time 0
# ==============================================================================

param (
    [switch]$Force,
    [switch]$KeepLogs
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir
$PromDataDir = Join-Path $BaseDir "data\prometheus"
$LogsDir = Join-Path $BaseDir "logs"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  RESET MONITORING TIME-SERIES DATA (START FRESH)         " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

if (!$Force) {
    Write-Host "`n[WARNING] This action will wipe all recorded historical metrics in:" -ForegroundColor Yellow
    Write-Host "  $PromDataDir" -ForegroundColor Gray
    Write-Host "All Grafana metric charts will reset and start measuring from now.`n" -ForegroundColor Yellow
    
    $confirm = Read-Host "Are you sure you want to reset all collected data? (Y/N)"
    if ($confirm -notmatch "^[Yy]") {
        Write-Host "[ABORTED] Reset cancelled by user." -ForegroundColor Yellow
        exit 0
    }
}

# 1. Stop WindowsPrometheus Service
Write-Host "`n[1/4] Stopping WindowsPrometheus service..." -ForegroundColor Cyan
$promSvc = Get-Service -Name "WindowsPrometheus" -ErrorAction SilentlyContinue
if ($promSvc -and $promSvc.Status -eq "Running") {
    try {
        Stop-Service -Name "WindowsPrometheus" -Force -ErrorAction Stop
        Write-Host "  [OK] Service WindowsPrometheus stopped." -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] Could not stop WindowsPrometheus: $_" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  [INFO] WindowsPrometheus was not running." -ForegroundColor Gray
}

# 2. Wipe TSDB Data Directory
Write-Host "`n[2/4] Wiping Prometheus TSDB storage..." -ForegroundColor Cyan
if (Test-Path $PromDataDir) {
    try {
        Get-ChildItem -Path $PromDataDir -Force | Remove-Item -Recurse -Force -ErrorAction Stop
        Write-Host "  [OK] Cleared all historical data in data\prometheus\" -ForegroundColor Green
    } catch {
        Write-Host "  [WARN] Some files could not be removed immediately: $_" -ForegroundColor Yellow
    }
} else {
    New-Item -ItemType Directory -Force -Path $PromDataDir | Out-Null
    Write-Host "  [OK] Created fresh data\prometheus\ directory." -ForegroundColor Green
}

# 3. Truncate Log Files (unless -KeepLogs)
if (!$KeepLogs -and (Test-Path $LogsDir)) {
    Write-Host "`n[3/4] Truncating application log files..." -ForegroundColor Cyan
    Get-ChildItem -Path $LogsDir -Filter "*.log" -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Clear-Content -Path $_.FullName -ErrorAction SilentlyContinue
            Write-Host "  [OK] Cleared log: $($_.Name)" -ForegroundColor Green
        } catch {}
    }
} else {
    Write-Host "`n[3/4] Skipping logs cleanup (KeepLogs selected)." -ForegroundColor Gray
}

# 4. Restart WindowsPrometheus Service
Write-Host "`n[4/4] Restarting WindowsPrometheus service..." -ForegroundColor Cyan
try {
    Start-Service -Name "WindowsPrometheus" -ErrorAction Stop
    Write-Host "  [OK] Service WindowsPrometheus restarted successfully." -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] Could not restart WindowsPrometheus: $_" -ForegroundColor Red
}

# Check readiness
Write-Host "`nWaiting for Prometheus TSDB engine to initialize..." -ForegroundColor Cyan
for ($i = 0; $i -lt 5; $i++) {
    Start-Sleep -Seconds 1
    $c = Test-NetConnection -ComputerName 127.0.0.1 -Port 9090 -WarningAction SilentlyContinue
    if ($c.TcpTestSucceeded) { break }
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  SUCCESS! Historical data wiped. Measuring fresh metrics." -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
