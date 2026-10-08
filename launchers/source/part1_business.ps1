$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$port = 8101
$address = "http://127.0.0.1:$port"
$pythonCandidates = @(
    (Join-Path $projectRoot '.venv\Scripts\python.exe'),
    'D:\ChatGPT项目\金融AI智能体\.venv\Scripts\python.exe'
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue

if (-not $python) {
    Write-Host '找不到 Python 运行环境。请检查本机 Python 环境是否已安装。' -ForegroundColor Red
    exit 1
}
if (-not $nodeCommand) {
    Write-Host '找不到 Node.js。请先安装 Node.js，再重新启动工作台。' -ForegroundColor Red
    exit 1
}

$existingListener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existingListener) {
    try {
        $existingHealth = Invoke-RestMethod -Uri "$address/api/health" -TimeoutSec 3
        if ($existingHealth.status -eq 'ok' -and $null -ne $existingHealth.api_configured) {
            Write-Host "模块一工作台已经运行：$address" -ForegroundColor Green
            Start-Process $address
            exit 0
        }
    } catch { }
    Write-Host "端口 $port 已被其他程序占用。为避免连接错服务，工作台没有启动。" -ForegroundColor Red
    exit 1
}

$logDirectory = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$stdoutLog = Join-Path $logDirectory '模块一工作台-输出.log'
$stderrLog = Join-Path $logDirectory '模块一工作台-错误.log'
$env:FINLAB_CONFIG_PATH = Join-Path $projectRoot '.env'
$env:FINLAB_API_TARGET = $address
Remove-Item Env:\DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:\DEEPSEEK_MODEL -ErrorAction SilentlyContinue

Write-Host '正在构建模块一页面…'
Push-Location (Join-Path $projectRoot 'frontend')
try {
    & $nodeCommand.Source (Join-Path $projectRoot '工具\构建模块一前端.mjs')
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败。' }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    Pop-Location
}

Write-Host '正在接入本任务已有的模块一历史结果…'
& $python (Join-Path $projectRoot '工具\导入模块一历史结果.py')
if ($LASTEXITCODE -ne 0) {
    Write-Host '历史结果导入脚本未能正常结束。详情请检查模块一工作台日志。' -ForegroundColor Red
    exit 1
}

Write-Host '正在启动独立服务…'
$serverArguments = @('-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', "$port", '--log-level', 'info')
Start-Process `
    -FilePath $python `
    -ArgumentList $serverArguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog | Out-Null

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri "$address/api/health" -TimeoutSec 2
        if ($health.status -eq 'ok') {
            $ready = $true
            break
        }
    } catch { }
}

if (-not $ready) {
    Write-Host "服务暂未就绪。日志保存在：$logDirectory" -ForegroundColor Red
    exit 1
}

Write-Host "模块一工作台已启动：$address" -ForegroundColor Green
Write-Host "运行数据与日志均保存在本任务目录：$projectRoot"
Start-Process $address
