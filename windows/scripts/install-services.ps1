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

# 1. Service: WindowsThermalAgent
$AgentService = "WindowsThermalAgent"
Write-Host "[INSTALL] Registering Service: $AgentService..." -ForegroundColor Yellow
& $NssmExe install $AgentService $PythonCmd (Join-Path $BaseDir "agent.py")
& $NssmExe set $AgentService AppDirectory $BaseDir
& $NssmExe set $AgentService AppStdout (Join-Path $BaseDir "logs\agent.log")
& $NssmExe set $AgentService AppStderr (Join-Path $BaseDir "logs\agent.log")
& $NssmExe set $AgentService AppRotateFiles 1
& $NssmExe set $AgentService AppRotateBytes 10485760 # 10MB
& $NssmExe set $AgentService AppEnvironmentExtra "PYTHONIOENCODING=utf-8"
& $NssmExe set $AgentService Start SERVICE_AUTO_START

# 2. Service: WindowsPrometheus
$PromService = "WindowsPrometheus"
$PromExe = Join-Path $BaseDir "bin\prometheus\prometheus.exe"
$PromCfg = Join-Path $BaseDir "config\prometheus.yml"
$PromData = Join-Path $BaseDir "data\prometheus"
$PromArgs = "--config.file=`"$PromCfg`" --storage.tsdb.path=`"$PromData`" --storage.tsdb.retention.time=30d --web.listen-address=`"0.0.0.0:9090`""

Write-Host "[INSTALL] Registering Service: $PromService..." -ForegroundColor Yellow
& $NssmExe install $PromService $PromExe $PromArgs
& $NssmExe set $PromService AppDirectory (Join-Path $BaseDir "bin\prometheus")
& $NssmExe set $PromService AppStdout (Join-Path $BaseDir "logs\prometheus.log")
& $NssmExe set $PromService AppStderr (Join-Path $BaseDir "logs\prometheus.log")
& $NssmExe set $PromService Start SERVICE_AUTO_START

# 3. Service: WindowsGrafana
$GrafService = "WindowsGrafana"
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

Write-Host "[INSTALL] Registering Service: $GrafService using $GrafExe..." -ForegroundColor Yellow
& $NssmExe install $GrafService $GrafExe $GrafArgs
& $NssmExe set $GrafService AppParameters $GrafArgs
& $NssmExe set $GrafService AppDirectory $GrafHome
& $NssmExe set $GrafService AppStdout (Join-Path $BaseDir "logs\grafana.log")
& $NssmExe set $GrafService AppStderr (Join-Path $BaseDir "logs\grafana.log")
& $NssmExe set $GrafService Start SERVICE_AUTO_START

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
