# ==============================================================================
# Register Windows Services via NSSM (Zero-Reboot)
# ==============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir
$NssmExe = Join-Path $BaseDir "bin\nssm\win64\nssm.exe"

if (!(Test-Path $NssmExe)) {
    Write-Host "[ERROR] nssm.exe not found. Please run .\scripts\setup.ps1 first!" -ForegroundColor Red
    exit 1
}

# Locate Python executable (ignoring WindowsApps redirector stub)
$pyCmd = Get-Command python.exe -ErrorAction SilentlyContinue
$PythonCmd = $null
if ($pyCmd -and $pyCmd.Source -notmatch "WindowsApps") {
    $PythonCmd = $pyCmd.Source
}

if (!$PythonCmd) {
    $CommonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "C:\Program Files\Python313\python.exe",
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

if (!$PythonCmd -and (Test-Path "$env:LOCALAPPDATA\Programs\Python")) {
    $foundPy = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($foundPy) {
        $PythonCmd = $foundPy.FullName
    }
}

if (!$PythonCmd) {
    Write-Host "[ERROR] Real python.exe not found! Please run .\scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}
Write-Host "[AGENT] Using Python for service: $PythonCmd" -ForegroundColor Cyan

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  REGISTERING WINDOWS SERVICES VIA NSSM                  " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

function Register-Or-Update-Service {
    param(
        [string]$ServiceName,
        [string]$AppPath,
        [string]$AppParams,
        [string]$AppDir,
        [string]$StdoutPath,
        [string]$StderrPath,
        [string]$EnvExtra = $null
    )

    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (!$existing) {
        Write-Host "[INSTALL] Registering Service: $ServiceName..." -ForegroundColor Yellow
        & $NssmExe install $ServiceName $AppPath $AppParams | Out-Null
    } else {
        Write-Host "[UPDATE] Updating existing Service: $ServiceName..." -ForegroundColor Yellow
    }

    & $NssmExe set $ServiceName Application $AppPath | Out-Null
    & $NssmExe set $ServiceName AppParameters $AppParams | Out-Null
    & $NssmExe set $ServiceName AppDirectory $AppDir | Out-Null
    & $NssmExe set $ServiceName AppStdout $StdoutPath | Out-Null
    & $NssmExe set $ServiceName AppStderr $StderrPath | Out-Null
    & $NssmExe set $ServiceName AppRotateFiles 1 | Out-Null
    & $NssmExe set $ServiceName AppRotateBytes 10485760 | Out-Null
    if ($EnvExtra) {
        & $NssmExe set $ServiceName AppEnvironmentExtra $EnvExtra | Out-Null
    }
    & $NssmExe set $ServiceName Start SERVICE_AUTO_START | Out-Null
    Write-Host "  [OK] Service $ServiceName configured -> $AppPath" -ForegroundColor Green
}

# 1. Service: WindowsThermalAgent
Register-Or-Update-Service `
    -ServiceName "WindowsThermalAgent" `
    -AppPath $PythonCmd `
    -AppParams (Join-Path $BaseDir "agent.py") `
    -AppDir $BaseDir `
    -StdoutPath (Join-Path $BaseDir "logs\agent.log") `
    -StderrPath (Join-Path $BaseDir "logs\agent.log") `
    -EnvExtra "PYTHONIOENCODING=utf-8"

# 2. Service: WindowsPrometheus
$PromExe = Join-Path $BaseDir "bin\prometheus\prometheus.exe"
$PromCfg = Join-Path $BaseDir "config\prometheus.yml"
$PromData = Join-Path $BaseDir "data\prometheus"
$PromArgs = "--config.file=`"$PromCfg`" --storage.tsdb.path=`"$PromData`" --storage.tsdb.retention.time=30d --web.listen-address=`"0.0.0.0:9090`" --web.enable-admin-api --web.enable-lifecycle"

Register-Or-Update-Service `
    -ServiceName "WindowsPrometheus" `
    -AppPath $PromExe `
    -AppParams $PromArgs `
    -AppDir (Join-Path $BaseDir "bin\prometheus") `
    -StdoutPath (Join-Path $BaseDir "logs\prometheus.log") `
    -StderrPath (Join-Path $BaseDir "logs\prometheus.log")

# 3. Synchronize Grafana Configuration & Dashboards
$GrafHome = Join-Path $BaseDir "bin\grafana"
$GrafProvDir = Join-Path $GrafHome "conf\provisioning"
if (Test-Path $GrafProvDir) {
    $LocalProvDir = Join-Path $BaseDir "config\grafana\provisioning"
    Copy-Item -Path "$LocalProvDir\*" -Destination $GrafProvDir -Recurse -Force
    Write-Host "[CONFIG] Synchronized Grafana Provisioning (Datasource UID 'Prometheus')." -ForegroundColor Green
}

$GrafDashDir = Join-Path $GrafHome "dashboards"
New-Item -ItemType Directory -Force -Path $GrafDashDir | Out-Null
Copy-Item -Path "$BaseDir\dashboards\*.json" -Destination $GrafDashDir -Force
Write-Host "[CONFIG] Synchronized 4 Dashboards into bin\grafana\dashboards." -ForegroundColor Green

$LocalCustomIni = Join-Path $BaseDir "config\grafana\conf\custom.ini"
$GrafConfDir = Join-Path $GrafHome "conf"
if (Test-Path $LocalCustomIni) {
    Copy-Item -Path $LocalCustomIni -Destination "$GrafConfDir\custom.ini" -Force
    Write-Host "[CONFIG] Applied custom.ini (disabled slow plugin downloads, bound 0.0.0.0:3000)." -ForegroundColor Green
}

# Clean stale SQLite database so Grafana recreates fresh with UID 'Prometheus'
$GrafDbs = @(
    (Join-Path $BaseDir "data\grafana\grafana.db"),
    (Join-Path $GrafHome "data\grafana.db")
)
foreach ($db in $GrafDbs) {
    if (Test-Path $db) {
        try {
            Stop-Service -Name "WindowsGrafana" -Force -ErrorAction SilentlyContinue
            Remove-Item $db -Force -ErrorAction SilentlyContinue
            Write-Host "[CONFIG] Re-initialized $db to bind UID 'Prometheus' cleanly." -ForegroundColor Green
        } catch {}
    }
}

# 4. Service: WindowsGrafana
$GrafExe = Join-Path $GrafHome "bin\grafana.exe"
$CustomIniPath = Join-Path $GrafConfDir "custom.ini"
$GrafArgs = "server --homepath `"$GrafHome`" --config `"$CustomIniPath`""

if (!(Test-Path $GrafExe)) {
    # Fallback for older Grafana versions (< v13)
    $LegacyGrafExe = Join-Path $GrafHome "bin\grafana-server.exe"
    if (Test-Path $LegacyGrafExe) {
        $GrafExe = $LegacyGrafExe
        $GrafArgs = "--homepath `"$GrafHome`" --config `"$CustomIniPath`""
    } else {
        Write-Host "[ERROR] Neither grafana.exe nor grafana-server.exe found in bin\grafana\bin!" -ForegroundColor Red
        exit 1
    }
}

Register-Or-Update-Service `
    -ServiceName "WindowsGrafana" `
    -AppPath $GrafExe `
    -AppParams $GrafArgs `
    -AppDir $GrafHome `
    -StdoutPath (Join-Path $BaseDir "logs\grafana.log") `
    -StderrPath (Join-Path $BaseDir "logs\grafana.log")

# 5. Service: WindowsLHM (Optional - for Motherboard 12V/5V/3.3V voltages and CPU Package Power)
$LhmExe = Join-Path $BaseDir "bin\lhm\LibreHardwareMonitor.exe"
if (Test-Path $LhmExe) {
    Register-Or-Update-Service `
        -ServiceName "WindowsLHM" `
        -AppPath $LhmExe `
        -AppParams "" `
        -AppDir (Join-Path $BaseDir "bin\lhm") `
        -StdoutPath (Join-Path $BaseDir "logs\lhm.log") `
        -StderrPath (Join-Path $BaseDir "logs\lhm.log")
}

# 4. Firewall Inbound Rule for Tailscale / Remote access
try {
    $RuleName = "Monitoring Stack (Grafana 3000, Prometheus 9090, Agent 9100)"
    $existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    if (!$existing) {
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -LocalPort 3000,9090,9100 -Protocol TCP -Action Allow -Profile Any | Out-Null
        Write-Host "[FIREWALL] Opened Inbound TCP Ports 3000, 9090, 9100 for Tailscale/LAN." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] Firewall rule skipped (requires Admin rights, Tailscale can still route directly)." -ForegroundColor Gray
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "  SUCCESSFULLY REGISTERED 3 WINDOWS SERVICES!            " -ForegroundColor Green
Write-Host "  To start services, run: .\scripts\start-services.ps1   " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Green
