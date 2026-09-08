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
$PromArgs = "--config.file=`"$PromCfg`" --storage.tsdb.path=`"$PromData`" --storage.tsdb.retention.time=30d --web.listen-address=`"0.0.0.0:9090`""

Register-Or-Update-Service `
    -ServiceName "WindowsPrometheus" `
    -AppPath $PromExe `
    -AppParams $PromArgs `
    -AppDir (Join-Path $BaseDir "bin\prometheus") `
    -StdoutPath (Join-Path $BaseDir "logs\prometheus.log") `
    -StderrPath (Join-Path $BaseDir "logs\prometheus.log")

# 3. Service: WindowsGrafana
$GrafHome = Join-Path $BaseDir "bin\grafana"
$GrafExe = Join-Path $GrafHome "bin\grafana.exe"
$GrafArgs = "server --homepath `"$GrafHome`""

if (!(Test-Path $GrafExe)) {
    # Fallback for older Grafana versions (< v13)
    $LegacyGrafExe = Join-Path $GrafHome "bin\grafana-server.exe"
    if (Test-Path $LegacyGrafExe) {
        $GrafExe = $LegacyGrafExe
        $GrafArgs = "--homepath `"$GrafHome`""
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
