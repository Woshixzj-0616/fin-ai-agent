$ErrorActionPreference = 'Stop'
$Port = 8103
$Url = "http://127.0.0.1:$Port"
$LogDir = Join-Path $PSScriptRoot 'logs'
$StdoutLog = Join-Path $LogDir '模块三工作台_启动输出.log'
$StderrLog = Join-Path $LogDir '模块三工作台_错误输出.log'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Listener) {
    try {
        $Existing = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 2
        if ($Existing.module_id -eq 'assets' -and $Existing.port -eq $Port) {
            Start-Process $Url
            Write-Host "模块三工作台已在运行：$Url"
            exit 0
        }
    } catch { }
    Write-Host "端口 $Port 已被其他程序占用。为避免连错服务，本次没有启动。"
    Write-Host "请先确认该端口的程序，或关闭它后再运行本启动文件。"
    exit 2
}

$Candidates = @(
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    'D:\ChatGPT项目\金融AI智能体\.venv\Scripts\python.exe',
    'D:\ChatGPT项目\金融AI智能体_V3.0.1\.venv\Scripts\python.exe'
)
$Python = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Python) {
    Write-Host '没有找到本工作台可用的 Python 环境。'
    exit 3
}

& $Python -c "import fastapi, uvicorn, multipart, pymupdf, openai, dotenv"
if ($LASTEXITCODE -ne 0) {
    Write-Host '当前 Python 环境缺少工作台依赖，未启动服务。'
    exit 4
}

$env:FINLAB_CONFIG_PATH = Join-Path $PSScriptRoot '.env'
$env:ASSETS_WORKBENCH_PORT = "$Port"
Start-Process -FilePath $Python `
    -ArgumentList @('-m', 'backend.modules.assets.workbench') `
    -WorkingDirectory $PSScriptRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog | Out-Null

$Ready = $false
for ($Attempt = 0; $Attempt -lt 35; $Attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $Health = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 2
        if ($Health.module_id -eq 'assets' -and $Health.port -eq $Port) {
            $Ready = $true
            break
        }
    } catch { }
}

if (-not $Ready) {
    Write-Host "模块三工作台未能在 18 秒内启动。日志位置：$LogDir"
    exit 5
}

Start-Process $Url
Write-Host "模块三工作台已启动：$Url"
Write-Host "后台运行记录：$(Join-Path $PSScriptRoot 'data\模块三工作台')"
Write-Host "日志目录：$LogDir"
