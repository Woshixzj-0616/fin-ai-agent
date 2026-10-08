param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$V1Python = Join-Path $ProjectRoot '..\金融AI智能体\.venv\Scripts\python.exe'
$NeedInstallPythonDependencies = $false

$LocalEnvironmentReady = $false
if (Test-Path -LiteralPath $VenvPython) {
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $VenvPython -c 'import fastapi, uvicorn, pymupdf, openai, dotenv, multipart' 2>$null | Out-Null
        $LocalEnvironmentReady = ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
}

if (-not $LocalEnvironmentReady) {
    if (Test-Path -LiteralPath $V1Python) {
        $VenvPython = $V1Python
        Write-Host 'V3.2.1 将复用本机已有的 Python 依赖环境；代码和运行数据仍保存在本目录。' -ForegroundColor Yellow
    } else {
        $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($PyLauncher) {
            & $PyLauncher.Source -3.12 -m venv '.venv'
        } elseif ($PythonCommand) {
            & $PythonCommand.Source -m venv '.venv'
        } else {
            Write-Host '没有找到 Python。请先安装 Python 3.12 或更新版本，并勾选 Add Python to PATH。' -ForegroundColor Yellow
            Write-Host '安装后重新运行“启动V3.2.1.ps1”。'
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
    Write-Host '正在构建 V3.2.1 网页…'
    npm run build
    if ($LASTEXITCODE -ne 0) { throw '网页构建失败。' }
} finally {
    Pop-Location
}

$Port = 8006
$Address = "http://127.0.0.1:$Port"

function Test-V3Service {
    try {
        $schema = Invoke-RestMethod -Uri "$Address/openapi.json" -TimeoutSec 2
        return $schema.info.version -eq '3.2.1'
    } catch {
        return $false
    }
}

function Open-V3Browser {
    try {
        Start-Process $Address -ErrorAction Stop
    } catch {
        Write-Host "服务已启动，但当前环境没有打开浏览器的权限。请在浏览器输入：$Address" -ForegroundColor Yellow
    }
}

if (Test-V3Service) {
    Write-Host "V3.2.1 已在运行：$Address" -ForegroundColor Green
    if (-not $NoBrowser) { Open-V3Browser }
    exit 0
}

$LogDir = Join-Path $ProjectRoot 'logs'
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$StdoutLog = Join-Path $LogDir 'v3.2.1-stdout.log'
$StderrLog = Join-Path $LogDir 'v3.2.1-stderr.log'
$BackendProcess = Start-Process -FilePath $VenvPython `
    -ArgumentList @('-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', "$Port") `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog

for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-V3Service) {
        Write-Host "V3.2.1 已启动：$Address" -ForegroundColor Green
        if (-not $NoBrowser) { Open-V3Browser }
        exit 0
    }
    $BackendProcess.Refresh()
    if ($BackendProcess.HasExited) {
        throw "V3.2.1 服务启动失败。请查看 $StderrLog；如果 8006 端口已被占用，请先关闭占用它的程序。"
    }
}

throw "V3.2.1 服务未能在 20 秒内就绪。请查看 $StderrLog。"
