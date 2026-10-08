$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = 8105
$Url = "http://127.0.0.1:$Port"
$Logs = Join-Path $ProjectRoot 'logs'
$Dist = Join-Path $ProjectRoot 'frontend\dist'
$TaskModules = Join-Path $ProjectRoot 'frontend\node_modules'
$Runtime = Join-Path $ProjectRoot 'data\开发运行'
$PidPath = Join-Path $Runtime '模块五服务.pid'

New-Item -ItemType Directory -Force -Path $Logs, $Runtime | Out-Null
$Listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Listener) {
    try {
        $Existing = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 2
        if ($Existing.module_id -eq 'solvency') {
            Write-Host "模块五工作台已经运行：$Url" -ForegroundColor Green
            Start-Process $Url
            exit 0
        }
    } catch { }
    throw "端口 $Port 已被其他程序占用（PID $($Listener.OwningProcess)）。请先关闭占用程序，再启动模块五工作台。"
}

if (-not (Test-Path (Join-Path $TaskModules 'vite\bin\vite.js'))) {
    $ProjectParent = Split-Path -Path (Split-Path -Path $ProjectRoot -Parent) -Parent
    $SharedModules = Join-Path $ProjectParent '金融AI智能体_V3.0.1\frontend\node_modules'
    if (Test-Path $SharedModules) {
        Write-Host '正在把前端构建依赖复制到本任务目录…' -ForegroundColor Cyan
        New-Item -ItemType Directory -Force -Path $TaskModules | Out-Null
        Copy-Item -Path (Join-Path $SharedModules '*') -Destination $TaskModules -Recurse -Force
    } else {
        Write-Host '正在为本工作台安装网页依赖…' -ForegroundColor Cyan
        Push-Location (Join-Path $ProjectRoot 'frontend')
        try { & npm.cmd ci } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败。请检查网络后重新运行此文件。' }
    }
}

Write-Host '正在构建模块五独立页面…' -ForegroundColor Cyan
Push-Location (Join-Path $ProjectRoot 'frontend')
try { & npm.cmd run build } finally { Pop-Location }
if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $Dist 'index.html'))) {
    throw '网页构建失败。请查看上方信息并修复后重试。'
}

$Python = 'D:\ChatGPT项目\金融AI智能体\.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $Python -or -not (Test-Path $Python)) { throw '未找到 Python。请先安装项目 requirements.txt 中的依赖。' }

$env:FINLAB_CONFIG_PATH = Join-Path $ProjectRoot '.env'
if ($env:PYTHONPATH) { $TaskPythonPath = "$ProjectRoot;$env:PYTHONPATH" } else { $TaskPythonPath = $ProjectRoot }
$env:PYTHONPATH = $TaskPythonPath
$env:PYTHONUTF8 = '1'
$StdoutLog = Join-Path $Logs '模块五-8105-stdout.log'
$StderrLog = Join-Path $Logs '模块五-8105-stderr.log'
$Arguments = "-m uvicorn backend.app:app --host 127.0.0.1 --port $Port --app-dir `"$ProjectRoot`""
$Server = Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog
$Server.Id | Set-Content -LiteralPath $PidPath -Encoding ASCII

$Ready = $false
for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
    Start-Sleep -Milliseconds 500
    if ($Server.HasExited) { break }
    try {
        $Health = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 2
        if ($Health.module_id -eq 'solvency') { $Ready = $true; break }
    } catch { }
}
if (-not $Ready) {
    if (-not $Server.HasExited) { Stop-Process -Id $Server.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
    $ErrorLines = if (Test-Path $StderrLog) { (Get-Content -LiteralPath $StderrLog -Tail 16) -join "`n" } else { '' }
    throw "模块五服务未能启动。请查看日志：$StderrLog`n$ErrorLines"
}

Write-Host "模块五工作台已启动：$Url" -ForegroundColor Green
Write-Host "本机配置：$ProjectRoot\.env（密钥不写入日志或代码）" -ForegroundColor DarkGray
Write-Host "服务日志：$Logs" -ForegroundColor DarkGray
Start-Process $Url
