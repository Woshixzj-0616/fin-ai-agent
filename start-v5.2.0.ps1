$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = 'http://127.0.0.1:8769/app/finscope.html'
$port = 8769

$pythonPath = $null
$pythonArguments = @()
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if ($pythonCommand) { $pythonPath = $pythonCommand.Source }
if (-not $pythonPath) {
    $pythonCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
    )
    $pythonPath = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $pythonPath) {
    $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $pythonPath = $pythonCommand.Source; $pythonArguments = @('-3') }
}
if (-not $pythonPath) {
    Write-Host '没有找到 Python。请先安装 Python 3 并确保 python.exe 已加入 PATH。' -ForegroundColor Red
    Read-Host '按 Enter 关闭'
    exit 1
}

$requirementsPath = Join-Path $projectRoot 'requirements.txt'
$engineRequirementsPath = Join-Path $projectRoot 'six_module_engine\backend\requirements.txt'
$vendorPath = Join-Path $projectRoot 'vendor\python'
$dependencyStampPath = Join-Path $vendorPath '.requirements.sha256'
$pythonVersion = (& $pythonPath @pythonArguments -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($LASTEXITCODE -ne 0 -or -not $pythonVersion) {
    Write-Host '无法读取 Python 版本。请检查 Python 安装后重新启动。' -ForegroundColor Red
    Read-Host '按 Enter 关闭'
    exit 1
}
$dependencySignature = $pythonVersion + ':' + (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash + ':' + (Get-FileHash -LiteralPath $engineRequirementsPath -Algorithm SHA256).Hash
$installedSignature = if (Test-Path -LiteralPath $dependencyStampPath) { (Get-Content -LiteralPath $dependencyStampPath -Raw).Trim() } else { '' }
if ($installedSignature -ne $dependencySignature -or -not (Test-Path -LiteralPath (Join-Path $vendorPath 'openai\__init__.py'))) {
    New-Item -ItemType Directory -Force -Path $vendorPath | Out-Null
    Write-Host '正在为 V5.2.0 准备本地分析依赖，文件只安装在当前版本目录。' -ForegroundColor Cyan
    & $pythonPath @pythonArguments -m pip install --disable-pip-version-check --no-warn-script-location --target $vendorPath --upgrade -r $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'V5.2.0 本地依赖安装失败。请检查网络或 Python pip 环境后重新启动。' -ForegroundColor Red
        Read-Host '按 Enter 关闭'
        exit 1
    }
    Set-Content -LiteralPath $dependencyStampPath -Value $dependencySignature -NoNewline -Encoding ASCII
}

$listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $listener) {
    $launchArguments = @('-B', 'agent/webui.py', '--host', '127.0.0.1', '--port', "$port")
    if ($pythonArguments.Count -gt 0) { $launchArguments = $pythonArguments + $launchArguments }
    Start-Process -FilePath $pythonPath -ArgumentList $launchArguments -WorkingDirectory $projectRoot -WindowStyle Hidden
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        $response = Invoke-WebRequest -Uri $url -TimeoutSec 2 -UseBasicParsing -ErrorAction SilentlyContinue
        if ($response -and $response.StatusCode -eq 200) { $ready = $true; break }
    }
    if (-not $ready) {
    Write-Host ('FinScope v5.2.0 本地服务没有启动。请检查 Python 环境，或确认其他版本服务已关闭、{0} 端口未被占用。' -f $port) -ForegroundColor Red
        Read-Host '按 Enter 关闭'
        exit 1
    }
}

$page = Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing -ErrorAction SilentlyContinue
if (-not $page -or $page.StatusCode -ne 200 -or -not $page.Content.Contains('data-build="v5.2.0"') -or -not $page.Content.Contains('workspace-navigation') -or -not $page.Content.Contains('pane-history')) {
    Write-Host ('无法打开 FinScope v5.2.0。请确认其他版本服务已关闭，且 {0} 端口可用。' -f $port) -ForegroundColor Red
    Read-Host '按 Enter 关闭'
    exit 1
}

Start-Process $url
