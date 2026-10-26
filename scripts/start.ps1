param([switch]$SmokeTest)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$bundledRoot = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies'
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$basePython = if ($pythonCommand -and $pythonCommand.Source -notlike '*WindowsApps*') { $pythonCommand.Source } else { $null }
if (-not $basePython) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { $basePython = (& $launcher.Source -3 -c 'import sys; print(sys.executable)') }
}
if (-not $basePython -and (Test-Path (Join-Path $bundledRoot 'python\python.exe'))) { $basePython = Join-Path $bundledRoot 'python\python.exe' }
if (-not $basePython) { throw 'Install Python 3.11+ (python.org), then run start.bat again.' }
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $pythonExe)) {
    & $basePython -m venv .venv
    if ($LASTEXITCODE -ne 0 -and -not (Test-Path $pythonExe)) { throw 'Python virtual environment creation failed.' }
}
$ErrorActionPreference = 'Continue'
& $pythonExe -c 'import fastapi, uvicorn, sqlalchemy, httpx, dotenv, openpyxl, PIL' 2>&1 | Out-Null
$needsPythonPackages = $LASTEXITCODE -ne 0
$ErrorActionPreference = 'Stop'
if ($needsPythonPackages) {
    & $basePython -m pip --python $pythonExe install -r backend/requirements.txt
    if ($LASTEXITCODE -ne 0) {
        & $pythonExe -m pip install -r backend/requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Python package installation failed. Check your network and Python installation.' }
    }
}
if (-not (Test-Path '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodeExe = if ($nodeCommand) { $nodeCommand.Source } else { Join-Path $bundledRoot 'node\bin\node.exe' }
if (-not (Test-Path $nodeExe)) { throw 'Install Node.js 22 LTS or later (nodejs.org).' }
$nodeMajor = & $nodeExe -p 'Number(process.versions.node.split(".")[0])'
if ([int]$nodeMajor -lt 22) { throw 'Node.js 22 or later is required.' }
$env:PATH = "$(Split-Path -Parent $nodeExe);$env:PATH"
if (-not (Test-Path 'frontend\node_modules\next\dist\bin\next')) {
    Push-Location frontend
    try {
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if ($npm) { & $npm.Source install }
        else {
            $pnpm = Join-Path $bundledRoot 'bin\fallback\pnpm.cmd'
            if (-not (Test-Path $pnpm)) { throw 'npm was not found. Reinstall Node.js with npm.' }
            & $pnpm install --store-dir ../.pnpm-store --node-linker=hoisted --config.enable-global-virtual-store=false
        }
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    } finally { Pop-Location }
}
if (-not (Test-Path 'frontend\.next\BUILD_ID')) {
    Push-Location frontend
    try { & $nodeExe node_modules/next/dist/bin/next build --webpack; if ($LASTEXITCODE -ne 0) { throw 'Next.js build failed.' } }
    finally { Pop-Location }
}
New-Item -ItemType Directory -Force -Path data/logs | Out-Null
$backendProcess = $null
$frontendProcess = $null
try {
    foreach ($port in @(8000, 3000)) {
        $tcp = New-Object System.Net.Sockets.TcpClient
        try { $tcp.Connect('127.0.0.1', $port); throw "Port $port is already in use. Close the previous collector or conflicting application." }
        catch [System.Net.Sockets.SocketException] { }
        finally { $tcp.Dispose() }
    }
    $backendProcess = Start-Process -FilePath $pythonExe -ArgumentList '-m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $projectRoot 'data/logs/backend.log') -RedirectStandardError (Join-Path $projectRoot 'data/logs/backend-error.log')
    $frontendProcess = Start-Process -FilePath $nodeExe -ArgumentList 'node_modules/next/dist/bin/next start --hostname 127.0.0.1 -p 3000' -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $projectRoot 'data/logs/frontend.log') -RedirectStandardError (Join-Path $projectRoot 'data/logs/frontend-error.log')
    $ready = $false
    for ($i=0; $i -lt 60; $i++) {
        if ($backendProcess.HasExited -or $frontendProcess.HasExited) { throw 'Server stopped. See data/logs for details.' }
        try {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:3000/api/health' -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'Server startup timeout. See data/logs.' }
    if ($SmokeTest) { Write-Host 'SMOKE TEST PASSED'; return }
    Start-Process 'http://localhost:3000'
    Write-Host 'Collector ready: http://localhost:3000'
    Write-Host 'Keep this window open. Press Ctrl+C to stop both servers.'
    while (-not $backendProcess.HasExited -and -not $frontendProcess.HasExited) { Start-Sleep -Seconds 1 }
} finally {
    if ($frontendProcess -and -not $frontendProcess.HasExited) { & taskkill.exe /PID $frontendProcess.Id /T /F 2>$null | Out-Null }
    if ($backendProcess -and -not $backendProcess.HasExited) { & taskkill.exe /PID $backendProcess.Id /T /F 2>$null | Out-Null }
}
