param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$V1Python = Join-Path $ProjectRoot '..\金融AI智能体\.venv\Scripts\python.exe'
$NeedInstallPythonDependencies = $false

$V2EnvironmentReady = $false
if (Test-Path -LiteralPath $VenvPython) {
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $VenvPython -c 'import fastapi, uvicorn, pymupdf, openai, dotenv, multipart' 2>$null | Out-Null
        $V2EnvironmentReady = ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
}

if (-not $V2EnvironmentReady) {
    if (Test-Path -LiteralPath $V1Python) {
        $VenvPython = $V1Python
        Write-Host 'V2 将复用本机已有的 Python 依赖环境；代码、数据库和 PDF 仍分别保存在 V2.0 目录。' -ForegroundColor Yellow
    } else {
        $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($PyLauncher) {
            & $PyLauncher.Source -3.12 -m venv '.venv'
        } elseif ($PythonCommand) {
            & $PythonCommand.Source -m venv '.venv'
        } else {
            Write-Host '没有找到 Python。请先安装 Python 3.12 或更新版本，并勾选 Add Python to PATH。' -ForegroundColor Yellow
            Write-Host '安装后重新运行“启动阶段二.ps1”。'
            exit 1
        }
        if ($LASTEXITCODE -ne 0) { throw '创建项目 Python 环境失败。' }
        $NeedInstallPythonDependencies = $true
    }
}

$PythonVersion = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$VersionParts = $PythonVersion.Split('.')
if ([int]$VersionParts[0] -lt 3 -or ([int]$VersionParts[0] -eq 3 -and [int]$VersionParts[1] -lt 12)) {
    throw "当前 Python 为 $PythonVersion；此项目需要 Python 3.12 或更新版本。"
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env'))) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot '.env.example') -Destination (Join-Path $ProjectRoot '.env')
    Write-Host '已创建本机配置文件 .env。首次打开网页时可在弹窗中配置 DeepSeek API Key。' -ForegroundColor Yellow
}

if ($NeedInstallPythonDependencies) {
    Write-Host '正在准备 Python 依赖…'
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw '安装 Python 依赖失败，请检查网络后重试。' }
}

$FrontendDir = Join-Path $ProjectRoot 'frontend'
Push-Location -LiteralPath $FrontendDir
try {
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir 'node_modules'))) {
        Write-Host '正在准备网页依赖…'
        npm ci
        if ($LASTEXITCODE -ne 0) { throw '安装网页依赖失败，请检查 Node.js 和网络。' }
    }
    Write-Host '正在构建 V2.0 网页…'
    npm run build
    if ($LASTEXITCODE -ne 0) { throw '网页构建失败。' }
} finally {
    Pop-Location
}

$Port = 8001
$Address = "http://127.0.0.1:$Port"

function Test-V2Service {
    try {
        $schema = Invoke-RestMethod -Uri "$Address/openapi.json" -TimeoutSec 2
        return $schema.info.version -eq '2.0.0'
    } catch {
        return $false
    }
}

if (Test-V2Service) {
    Write-Host "V2.0 已在运行：$Address" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $Address }
    exit 0
}

$LogDir = Join-Path $ProjectRoot 'logs'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$StdoutLog = Join-Path $LogDir 'v2-stdout.log'
$StderrLog = Join-Path $LogDir 'v2-stderr.log'
$BackendProcess = Start-Process -FilePath $VenvPython `
    -ArgumentList @('-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', "$Port") `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog

for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-V2Service) {
        Write-Host "V2.0 已启动：$Address" -ForegroundColor Green
        if (-not $NoBrowser) { Start-Process $Address }
        exit 0
    }
    $BackendProcess.Refresh()
    if ($BackendProcess.HasExited) {
        throw "V2.0 服务启动失败。请查看 $StderrLog；如果 8001 端口已被占用，请先关闭占用它的程序。"
    }
}

throw "V2.0 服务未能在 20 秒内就绪。请查看 $StderrLog。"
