param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$Port = 8104
$Address = "http://127.0.0.1:$Port"
$SharedPython = 'D:\ChatGPT项目\金融AI智能体\.venv\Scripts\python.exe'
$FallbackConfig = 'D:\ChatGPT项目\金融AI智能体_V3.0.1\.env'
$ProjectConfig = Join-Path $ProjectRoot '.env'
$LogDir = Join-Path $ProjectRoot 'data\现金流工作台\logs'
$StdoutLog = Join-Path $LogDir '模块四工作台-stdout.log'
$StderrLog = Join-Path $LogDir '模块四工作台-stderr.log'

Set-Location -LiteralPath $ProjectRoot
$env:FINLAB_CONFIG_PATH = $ProjectConfig
$env:FINLAB_FALLBACK_CONFIG_PATH = $FallbackConfig
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Test-CashflowService {
    try {
        $health = Invoke-RestMethod -Uri "$Address/api/health" -TimeoutSec 2
        return ($health.ok -eq $true -and $health.module_id -eq 'cashflow' -and $health.port -eq $Port)
    } catch {
        return $false
    }
}

if (Test-CashflowService) {
    Write-Host "模块四工作台已在运行：$Address" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $Address }
    exit 0
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) {
    throw "端口 $Port 已被其他程序占用。请先关闭占用程序，再启动模块四工作台；没有终止任何进程。"
}

$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = $SharedPython
}
if (-not (Test-Path -LiteralPath $Python)) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonCommand) { $Python = $PythonCommand.Source } else {
        throw '没有找到 Python。请安装 Python 3.12 或更新版本后重新启动。'
    }
}

$DependencyCheck = & $Python -c 'import fastapi, uvicorn, pymupdf, openai, dotenv, multipart' 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "当前 Python 环境缺少工作台依赖。请运行：python -m pip install -r requirements.txt。详情：$DependencyCheck"
}

$Process = Start-Process -FilePath $Python -ArgumentList @('-m', 'uvicorn', 'backend.cashflow_workbench:app', '--host', '127.0.0.1', '--port', "$Port") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog

for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-CashflowService) {
        Write-Host "模块四现金流工作台已启动：$Address" -ForegroundColor Green
        Write-Host "独立数据目录：$(Join-Path $ProjectRoot 'data\现金流工作台')"
        if (-not $NoBrowser) {
            try { Start-Process $Address } catch { Write-Host "请在浏览器中打开：$Address" -ForegroundColor Yellow }
        }
        exit 0
    }
    $Process.Refresh()
    if ($Process.HasExited) {
        throw "模块四工作台启动失败。请查看日志：$StderrLog"
    }
}

throw "模块四工作台未能在 20 秒内就绪。请查看日志：$StderrLog"
