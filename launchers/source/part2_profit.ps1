param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$Port = 8102
$Url = "http://127.0.0.1:$Port"
$ModuleVersion = '模块二_盈利来源与变化_v2'
$Python = 'D:\ChatGPT项目\金融AI智能体\.venv\Scripts\python.exe'

Set-Location -LiteralPath $ProjectRoot

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    try {
        $health = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 3
        if ($health.analysis_module -eq 'profit' -and $health.module_version -eq $ModuleVersion) {
            Write-Host "模块二工作台已在运行：$Url" -ForegroundColor Green
            if (-not $NoBrowser) { Start-Process $Url }
            exit 0
        }
    } catch { }
    throw "端口 $Port 已被其他服务占用；为避免误连或停止别的工作台，本脚本没有继续启动。"
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "找不到本机已有 Python 环境：$Python。没有联网安装依赖。"
}

$Frontend = Join-Path $ProjectRoot 'frontend'
$LocalNodeModules = Join-Path $Frontend 'node_modules'
$SharedNodeModules = 'D:\ChatGPT项目\金融AI智能体\frontend\node_modules'
if (-not (Test-Path -LiteralPath (Join-Path $LocalNodeModules '.bin\vite.cmd'))) {
    if (-not (Test-Path -LiteralPath $SharedNodeModules)) {
        throw '本机没有可复用的前端依赖。请在本工作区运行 npm ci 后重试。'
    }
    New-Item -ItemType Directory -Path $LocalNodeModules -Force | Out-Null
    Copy-Item -Path (Join-Path $SharedNodeModules '*') -Destination $LocalNodeModules -Recurse -Force
    if (-not (Test-Path -LiteralPath (Join-Path $LocalNodeModules '.bin\vite.cmd'))) {
        throw '复用的前端依赖复制不完整；没有启动后端服务。'
    }
}

Push-Location -LiteralPath $Frontend
try {
    & npm run build:profit
    if ($LASTEXITCODE -ne 0) { throw '模块二前端构建失败；后端尚未启动。' }
} finally {
    Pop-Location
}

$ConfigFile = Join-Path $ProjectRoot '.env'
if (-not (Test-Path -LiteralPath $ConfigFile)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot '.env.example') -Destination $ConfigFile
}
$env:FINLAB_CONFIG_PATH = $ConfigFile
foreach ($name in @('DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_TIMEOUT_SECONDS', 'DEEPSEEK_MAX_RETRIES')) {
    Remove-Item -LiteralPath "Env:\$name" -ErrorAction SilentlyContinue
}

$Logs = Join-Path $ProjectRoot 'logs'
New-Item -ItemType Directory -Path $Logs -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdoutLog = Join-Path $Logs "module2-workbench-$stamp.out.log"
$stderrLog = Join-Path $Logs "module2-workbench-$stamp.err.log"
$arguments = @('-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', "$Port", '--log-level', 'info')
$server = Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 2
        if ($health.status -eq 'ok' -and $health.analysis_module -eq 'profit' -and $health.module_version -eq $ModuleVersion) {
            $ready = $true
            break
        }
    } catch { }
    if ($server.HasExited) { break }
}

if (-not $ready) {
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -ErrorAction SilentlyContinue }
    Write-Host "服务未能启动。错误日志：$stderrLog" -ForegroundColor Red
    throw '模块二工作台没有在 8102 正常就绪。'
}

Write-Host "模块二独立工作台已启动：$Url" -ForegroundColor Green
Write-Host "模块版本：$($health.module_version)"
Write-Host "API Key：$(if ($health.api_configured) { '本机已配置' } else { '请在网页设置' })"
Write-Host "运行日志：$stdoutLog"
Write-Host "错误日志：$stderrLog"
if (-not $NoBrowser) { Start-Process $Url }
