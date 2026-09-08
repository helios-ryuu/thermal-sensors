# ==============================================================================
# Windows Portable Monitoring Stack Setup (Zero-Reboot)
# Downloads latest Python, Prometheus, Grafana, NSSM and configures dashboards
# ==============================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  WINDOWS PORTABLE MONITORING SETUP (ZERO-REBOOT)        " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# 1. Directory Structure
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
# 2. CHECK AND AUTO-INSTALL LATEST 64-BIT PYTHON (SILENT, ZERO-REBOOT)
# ==============================================================================
# Check if real Python exists (ignoring WindowsApps redirector stub)
$pyCmd = Get-Command python.exe -ErrorAction SilentlyContinue
$HasRealPython = $false
$RealPyExe = $null

if ($pyCmd -and $pyCmd.Source -notmatch "WindowsApps") {
    $testVer = & $pyCmd.Source --version 2>$null
    if ($testVer -match "Python\s+[0-9]+") {
        $HasRealPython = $true
        $RealPyExe = $pyCmd.Source
    }
}

if (!$HasRealPython) {
    # Check if installed in common directory but not in session PATH
    $commonSearch = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Program Files\Python313\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe"
    )
    foreach ($c in $commonSearch) {
        if (Test-Path $c) {
            $testVer = & $c --version 2>$null
            if ($testVer -match "Python\s+[0-9]+") {
                $HasRealPython = $true
                $RealPyExe = $c
                $cDir = Split-Path -Parent $c
                $env:Path = "$cDir;$cDir\Scripts;" + $env:Path
                break
            }
        }
    }
}

if (!$HasRealPython) {
    Write-Host "[CHECK] Real Python not found (ignoring WindowsApps stub). Finding latest Python release..." -ForegroundColor Yellow
    $PyUrl = "https://www.python.org/ftp/python/3.12.5/python-3.12.5-amd64.exe"
    $LatestPyVer = "3.12.5"
    try {
        $pyWeb = (Invoke-WebRequest -Uri "https://www.python.org/downloads/windows/" -UseBasicParsing -TimeoutSec 6).Content
        if ($pyWeb -match 'Latest Python 3 Release - Python ([0-9]+\.[0-9]+\.[0-9]+)') {
            $LatestPyVer = $matches[1]
            $PyUrl = "https://www.python.org/ftp/python/$LatestPyVer/python-$LatestPyVer-amd64.exe"
        }
    } catch {
        Write-Host "[INFO] Using stable fallback Python 3.12.5" -ForegroundColor Gray
    }

    Write-Host "[DOWNLOAD] Downloading Python $LatestPyVer installer from python.org..." -ForegroundColor Yellow
    $PyInstaller = Join-Path $env:TEMP "python-installer.exe"
    Invoke-WebRequest -Uri $PyUrl -OutFile $PyInstaller -UseBasicParsing
    
    Write-Host "[INSTALL] Installing Python $LatestPyVer silently (zero reboot)..." -ForegroundColor Yellow
    Start-Process -FilePath $PyInstaller -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1" -Wait
    Remove-Item $PyInstaller -Force
    
    # Locate the newly installed Python
    if (Test-Path "$env:LOCALAPPDATA\Programs\Python") {
        $newPy = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($newPy) {
            $RealPyExe = $newPy.FullName
        }
    }
    if (!$RealPyExe) {
        foreach ($c in $commonSearch) {
            if (Test-Path $c) {
                $RealPyExe = $c
                break
            }
        }
    }
    if ($RealPyExe) {
        $newDir = Split-Path -Parent $RealPyExe
        $env:Path = "$newDir;$newDir\Scripts;" + $env:Path
        Write-Host "[OK] Successfully installed Python $LatestPyVer - $RealPyExe" -ForegroundColor Green
    } else {
        Write-Host "[WARN] Python installed, but python.exe could not be located." -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK] Detected existing Python: $(& $RealPyExe --version 2>&1)" -ForegroundColor Green
}

# ==============================================================================
# 3. DOWNLOAD LATEST PROMETHEUS, GRAFANA AND NSSM PORTABLE
# ==============================================================================

Write-Host "`nQuerying latest releases from official repositories..." -ForegroundColor Yellow

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
            Write-Host "[LATEST] Found latest Prometheus: $($promRel.tag_name)" -ForegroundColor Cyan
        }
    } catch {
        Write-Host "[INFO] Using stable fallback Prometheus URL." -ForegroundColor Gray
    }

    $ZipPath = Join-Path $BinDir "prometheus.zip"
    Write-Host "[DOWNLOAD] Downloading Prometheus from $PromUrl..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $PromUrl -OutFile $ZipPath -UseBasicParsing
    Write-Host "[EXTRACT] Extracting Prometheus..." -ForegroundColor Yellow
    Expand-Archive -Path $ZipPath -DestinationPath $BinDir -Force
    Remove-Item $ZipPath -Force

    $extractedFolder = Get-ChildItem -Path $BinDir -Directory | Where-Object { $_.Name -like "prometheus-*.windows-amd64" } | Select-Object -First 1
    if ($extractedFolder) {
        if (Test-Path $PromFinalDir) { Remove-Item $PromFinalDir -Recurse -Force }
        Move-Item -Path $extractedFolder.FullName -Destination $PromFinalDir -Force
    }
    Write-Host "[OK] Prometheus setup complete!" -ForegroundColor Green
} else {
    Write-Host "[OK] Prometheus already exists in bin\prometheus\" -ForegroundColor Green
}

# B. Grafana Latest
$GrafFinalDir = Join-Path $BinDir "grafana"
$GrafExe = Join-Path $GrafFinalDir "bin\grafana.exe"
$LegacyGrafExe = Join-Path $GrafFinalDir "bin\grafana-server.exe"
if (!(Test-Path $GrafExe) -and !(Test-Path $LegacyGrafExe)) {
    $GrafUrl = "https://dl.grafana.com/oss/release/grafana-11.2.0.windows-amd64.zip"
    try {
        $grafRel = Invoke-RestMethod -Uri "https://api.github.com/repos/grafana/grafana/releases/latest" -Headers @{"User-Agent"="PowerShell"} -TimeoutSec 6
        $grafVer = $grafRel.tag_name.TrimStart('v')
        $GrafUrl = "https://dl.grafana.com/oss/release/grafana-$grafVer.windows-amd64.zip"
        Write-Host "[LATEST] Found latest Grafana: v$grafVer" -ForegroundColor Cyan
    } catch {
        Write-Host "[INFO] Using stable fallback Grafana URL." -ForegroundColor Gray
    }

    $ZipPath = Join-Path $BinDir "grafana.zip"
    Write-Host "[DOWNLOAD] Downloading Grafana from $GrafUrl..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $GrafUrl -OutFile $ZipPath -UseBasicParsing
    Write-Host "[EXTRACT] Extracting Grafana..." -ForegroundColor Yellow
    Expand-Archive -Path $ZipPath -DestinationPath $BinDir -Force
    Remove-Item $ZipPath -Force

    $extractedFolder = Get-ChildItem -Path $BinDir -Directory | Where-Object { $_.Name -like "grafana-*" -and $_.Name -ne "grafana" } | Select-Object -First 1
    if ($extractedFolder) {
        if (Test-Path $GrafFinalDir) { Remove-Item $GrafFinalDir -Recurse -Force }
        Move-Item -Path $extractedFolder.FullName -Destination $GrafFinalDir -Force
    }
    Write-Host "[OK] Grafana setup complete!" -ForegroundColor Green
} else {
    Write-Host "[OK] Grafana already exists in bin\grafana\" -ForegroundColor Green
}

# C. NSSM (Non-Sucking Service Manager)
$NssmFinalDir = Join-Path $BinDir "nssm"
$NssmExe = Join-Path $NssmFinalDir "win64\nssm.exe"
if (!(Test-Path $NssmExe)) {
    $NssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
    $ZipPath = Join-Path $BinDir "nssm.zip"
    Write-Host "[DOWNLOAD] Downloading NSSM 2.24..." -ForegroundColor Yellow
    Invoke-WebRequest -Uri $NssmUrl -OutFile $ZipPath -UseBasicParsing
    Write-Host "[EXTRACT] Extracting NSSM..." -ForegroundColor Yellow
    Expand-Archive -Path $ZipPath -DestinationPath $BinDir -Force
    Remove-Item $ZipPath -Force

    $extractedFolder = Get-ChildItem -Path $BinDir -Directory | Where-Object { $_.Name -like "nssm-*" -and $_.Name -ne "nssm" } | Select-Object -First 1
    if ($extractedFolder) {
        if (Test-Path $NssmFinalDir) { Remove-Item $NssmFinalDir -Recurse -Force }
        Move-Item -Path $extractedFolder.FullName -Destination $NssmFinalDir -Force
    }
    Write-Host "[OK] NSSM setup complete!" -ForegroundColor Green
} else {
    Write-Host "[OK] NSSM already exists in bin\nssm\" -ForegroundColor Green
}

# 3b. DOWNLOAD LIBRE HARDWARE MONITOR (PORTABLE FOR VOLTAGES 12V/5V/3.3V AND SENSORS)
$LhmFinalDir = Join-Path $BinDir "lhm"
$LhmExe = Join-Path $LhmFinalDir "LibreHardwareMonitor.exe"
if (!(Test-Path $LhmExe)) {
    Write-Host "`nQuerying latest LibreHardwareMonitor release..." -ForegroundColor Yellow
    $LhmUrl = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.3/LibreHardwareMonitor-net472.zip"
    try {
        $lhmRel = Invoke-RestMethod -Uri "https://api.github.com/repos/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest" -Headers @{"User-Agent"="PowerShell"} -TimeoutSec 6
        $zipAsset = $lhmRel.assets | Where-Object { $_.name -like "*.zip" } | Select-Object -First 1
        if ($zipAsset -and $zipAsset.browser_download_url) {
            $LhmUrl = $zipAsset.browser_download_url
            Write-Host "[LATEST] Found latest LibreHardwareMonitor: $($lhmRel.tag_name)" -ForegroundColor Green
        }
    } catch {
        Write-Host "[FALLBACK] Using standard release URL: $LhmUrl" -ForegroundColor Gray
    }

    $ZipPath = Join-Path $BinDir "lhm.zip"
    Write-Host "[DOWNLOAD] Downloading LibreHardwareMonitor portable..." -ForegroundColor Yellow
    try {
        Invoke-WebRequest -Uri $LhmUrl -OutFile $ZipPath -UseBasicParsing -TimeoutSec 60
        Write-Host "[EXTRACT] Extracting LibreHardwareMonitor..." -ForegroundColor Yellow
        New-Item -ItemType Directory -Force -Path $LhmFinalDir | Out-Null
        Expand-Archive -Path $ZipPath -DestinationPath $LhmFinalDir -Force
        Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] LibreHardwareMonitor portable setup complete!" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Could not download LibreHardwareMonitor ($_); agent will use native Windows sensors." -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK] LibreHardwareMonitor already exists in bin\lhm\" -ForegroundColor Green
}

# ==============================================================================
# 4. SYNC CONFIGURATIONS AND DASHBOARDS FOR GRAFANA
# ==============================================================================
$GrafProvDir = Join-Path $GrafFinalDir "conf\provisioning"
if (Test-Path $GrafProvDir) {
    $LocalProvDir = Join-Path $BaseDir "config\grafana\provisioning"
    Copy-Item -Path "$LocalProvDir\*" -Destination $GrafProvDir -Recurse -Force
    Write-Host "[CONFIG] Configured Datasource and Dashboard provisioning for Grafana." -ForegroundColor Green
}

$GrafDashDir = Join-Path $GrafFinalDir "dashboards"
New-Item -ItemType Directory -Force -Path $GrafDashDir | Out-Null
Copy-Item -Path "$BaseDir\dashboards\*.json" -Destination $GrafDashDir -Force
Write-Host "[CONFIG] Synchronized 4 Dashboards (Blackbox Crash, Thermals, System, Network) into Grafana." -ForegroundColor Green

$LocalCustomIni = Join-Path $BaseDir "config\grafana\conf\custom.ini"
$GrafConfDir = Join-Path $GrafFinalDir "conf"
if (Test-Path $LocalCustomIni) {
    Copy-Item -Path $LocalCustomIni -Destination "$GrafConfDir\custom.ini" -Force
    Write-Host "[CONFIG] Applied custom.ini (disabled slow plugin downloads, bound 0.0.0.0:3000)." -ForegroundColor Green
}

$GrafDbs = @(
    (Join-Path $BaseDir "data\grafana\grafana.db"),
    (Join-Path $GrafFinalDir "data\grafana.db")
)
foreach ($db in $GrafDbs) {
    if (Test-Path $db) {
        Remove-Item $db -Force -ErrorAction SilentlyContinue
    }
}

# ==============================================================================
# 5. INSTALL PYTHON REQUIREMENTS
# ==============================================================================
$ReqFile = Join-Path $BaseDir "requirements.txt"
if (Test-Path $ReqFile) {
    Write-Host "`n[PIP] Installing Python requirements from requirements.txt..." -ForegroundColor Yellow
    $PyExe = $RealPyExe
    if (!$PyExe) {
        $pyCmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pyCmd -and $pyCmd.Source -notmatch "WindowsApps") {
            $PyExe = $pyCmd.Source
        }
    }
    if (!$PyExe) {
        foreach ($c in $commonSearch) {
            if (Test-Path $c) { $PyExe = $c; break }
        }
    }
    if (!$PyExe -and (Test-Path "$env:LOCALAPPDATA\Programs\Python")) {
        $foundPy = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python" -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($foundPy) { $PyExe = $foundPy.FullName }
    }
    if ($PyExe) {
        Write-Host "[PIP] Using Python: $PyExe" -ForegroundColor Cyan
        & $PyExe -m pip install --upgrade pip --quiet
        & $PyExe -m pip install -r $ReqFile --quiet
        Write-Host "[OK] Installed Python packages (psutil, pynvml, prometheus-client, requests)." -ForegroundColor Green
    } else {
        Write-Host "[WARN] python.exe not located to execute pip install." -ForegroundColor Yellow
    }
}

Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host "  SETUP COMPLETED SUCCESSFULLY WITH LATEST RELEASES!     " -ForegroundColor Green
Write-Host "  Next step: Run .\scripts\install-services.ps1          " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Green
