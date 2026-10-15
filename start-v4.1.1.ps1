$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = 'http://127.0.0.1:8769/app/finscope.html'
$port = 8769

try {
    $pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
} catch {
    Write-Host '没有找到 Python。请先安装 Python 3 并确保 python.exe 已加入 PATH。' -ForegroundColor Red
    Read-Host '按 Enter 关闭'
    exit 1
}

$listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $listener) {
    Start-Process -FilePath $pythonPath -ArgumentList @('-B', 'agent/webui.py', '--host', '127.0.0.1', '--port', "$port") -WorkingDirectory $projectRoot -WindowStyle Hidden
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-WebRequest -Uri $url -TimeoutSec 2 -UseBasicParsing
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch {}
    }
    if (-not $ready) {
        Write-Host "FinScope v4.1.1 本地服务没有启动。请检查 Python 环境，或确认 $port 端口未被其他程序占用。" -ForegroundColor Red
        Read-Host '按 Enter 关闭'
        exit 1
    }
}

try {
    $page = Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing
    if ($page.StatusCode -ne 200 -or -not $page.Content.Contains('data-build="v4.1.1"') -or -not $page.Content.Contains('workspace-navigation') -or -not $page.Content.Contains('pane-history')) {
        throw "$port 端口当前没有提供 v4.1.1 页面。为避免打开错误版本，启动器已停止。"
    }
} catch {
    Write-Host "无法打开 FinScope v4.1.1：$($_.Exception.Message)" -ForegroundColor Red
    Read-Host '按 Enter 关闭'
    exit 1
}

Start-Process $url
