$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = 'http://127.0.0.1:8768/app/finscope.html'
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source

try {
    $listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8768 -State Listen -ErrorAction Stop
} catch {
    $listener = $null
}

if (-not $listener) {
    Start-Process -FilePath $pythonPath -ArgumentList @('-B', 'agent/webui.py', '--host', '127.0.0.1', '--port', '8768') -WorkingDirectory $projectRoot -WindowStyle Hidden
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-WebRequest -Uri $url -TimeoutSec 2 -UseBasicParsing
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch {}
    }
    if (-not $ready) {
        Write-Host 'Local service failed to start. Check the Python environment.' -ForegroundColor Red
        Read-Host 'Press Enter to close'
        exit 1
    }
}

try {
    $page = Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing
    if ($page.StatusCode -ne 200 -or -not $page.Content.Contains('workspace-navigation') -or -not $page.Content.Contains('pane-history')) {
        throw 'Port 8768 is not serving the v4.0.2 workspace.'
    }
} catch {
    Write-Host "Unable to open v4.0.2: $($_.Exception.Message)" -ForegroundColor Red
    Read-Host 'Press Enter to close'
    exit 1
}

Start-Process $url