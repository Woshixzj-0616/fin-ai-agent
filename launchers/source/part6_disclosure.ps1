param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

$Address = 'http://127.0.0.1:8106'
$Port = 8106
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$SharedPythonPath = Join-Path $ProjectRoot '..\..\金融AI智能体\.venv\Scripts\python.exe'
$LocalEnvPath = Join-Path $ProjectRoot '.env'

if (-not (Test-Path -LiteralPath $LocalEnvPath)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot '.env.example') -Destination $LocalEnvPath
    Write-Host '已创建本工作台自己的 .env。可在页面配置 DeepSeek Key。' -ForegroundColor Yellow
}
$env:FINLAB_CONFIG_PATH = $LocalEnvPath

if (-not (Test-Path -LiteralPath $PythonPath)) {
    if (Test-Path -LiteralPath $SharedPythonPath) {
        $PythonPath = $SharedPythonPath
        Write-Host '复用现有 Python 依赖环境；模块六的数据仍保存在本任务目录。' -ForegroundColor Yellow
    } else {
        $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($PyLauncher) {
            & $PyLauncher.Source -3.12 -m venv '.venv'
        } else {
            $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
            if (-not $PythonCommand) { throw '未找到 Python。请安装 Python 3.12 或更新版本后重试。' }
            & $PythonCommand.Source -m venv '.venv'
        }
        if ($LASTEXITCODE -ne 0) { throw '创建本地 Python 环境失败。' }
        $PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
        & $PythonPath -m pip install -r (Join-Path $ProjectRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw '安装 Python 依赖失败，请检查网络和 pip 输出。' }
    }
}

$PythonVersion = & $PythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$VersionParts = $PythonVersion.Split('.')
if ([int]$VersionParts[0] -lt 3 -or ([int]$VersionParts[0] -eq 3 -and [int]$VersionParts[1] -lt 12)) {
    throw "当前 Python 为 $PythonVersion；工作台需要 Python 3.12 或更新版本。"
}
& $PythonPath -c 'import fastapi, uvicorn, pymupdf, openai, dotenv, multipart'
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖不完整。请运行 pip install -r requirements.txt。' }

$FrontendDirectory = Join-Path $ProjectRoot 'frontend'
if (-not (Test-Path -LiteralPath (Join-Path $FrontendDirectory 'node_modules'))) {
    Push-Location -LiteralPath $FrontendDirectory
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw '安装网页依赖失败，请检查 Node.js 和网络。' }
    } finally { Pop-Location }
}
Push-Location -LiteralPath $FrontendDirectory
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw '模块六页面构建失败。' }
} finally { Pop-Location }

function Test-DisclosureService {
    try {
        $health = Invoke-RestMethod -Uri "$Address/api/health" -TimeoutSec 2
        $matches = ($health.module_id -eq 'disclosure' `
            -and $health.module_version -eq '模块六_披露可信度与特殊事项_v3.6.2' `
            -and $health.workbench_id -eq 'task06-disclosure-v3.6.2')
        if (-not $matches) {
            Write-Host ("健康检查未匹配：module_id={0}; module_version={1}; workbench_id={2}" -f $health.module_id, $health.module_version, $health.workbench_id) -ForegroundColor Yellow
        }
        return [bool]$matches
    } catch {
        Write-Host ("健康检查请求失败：{0}" -f $_.Exception.Message) -ForegroundColor Yellow
        return $false
    }
}

if (Test-DisclosureService) {
    Write-Host "模块六工作台已在运行：$Address" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $Address }
    exit 0
}

$PortListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($PortListener) {
    $ProcessIds = ($PortListener | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
    throw "端口 $Port 已被其他服务占用（PID $ProcessIds），没有停止或覆盖该服务。"
}

$LogDirectory = Join-Path $ProjectRoot 'data\app\logs'
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$StdoutLog = Join-Path $LogDirectory 'module-six-stdout.log'
$StderrLog = Join-Path $LogDirectory 'module-six-stderr.log'
# Clear inherited DeepSeek settings in this launcher process before spawning
# the backend. The child then loads credentials only from this task's .env.
foreach ($Name in @('DEEPSEEK_API_KEY', 'DEEPSEEK_MODEL', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_TIMEOUT_SECONDS', 'DEEPSEEK_MAX_RETRIES', 'FINLAB_DISCLOSURE_MAX_MODEL_TURNS')) {
    Remove-Item -Path "Env:$Name" -ErrorAction SilentlyContinue
}
$BackendProcess = Start-Process -FilePath $PythonPath `
    -ArgumentList @('-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', "$Port") `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog

for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-DisclosureService) {
        Write-Host "模块六独立工作台已启动：$Address" -ForegroundColor Green
        Write-Host "日志目录：$LogDirectory"
        if (-not $NoBrowser) { Start-Process $Address }
        exit 0
    }
    $BackendProcess.Refresh()
    if ($BackendProcess.HasExited) {
        throw "启动失败。请查看日志：$StderrLog"
    }
}
throw "8106 服务在 20 秒内未就绪。请查看日志：$StderrLog"
