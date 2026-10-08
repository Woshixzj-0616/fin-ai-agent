param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('business', 'profit', 'assets', 'cashflow', 'solvency', 'disclosure')]
    [string]$Module,
    [Parameter(Mandatory = $true)]
    [int]$Port
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Address = "http://127.0.0.1:$Port"
$ConfigFile = Join-Path $ProjectRoot '.env'
$LogDir = Join-Path $ProjectRoot 'data\modular_workbench\logs'
$StdoutLog = Join-Path $LogDir "$Module-$Port.out.log"
$StderrLog = Join-Path $LogDir "$Module-$Port.err.log"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $ConfigFile)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot '.env.example') -Destination $ConfigFile
}

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    try {
        $health = Invoke-RestMethod -Uri "$Address/api/health" -TimeoutSec 3
        if ($health.module_id -eq $Module) {
            Start-Process $Address
            Write-Host "模块工作台已经运行：$Address" -ForegroundColor Green
            exit 0
        }
    } catch { }
    throw "端口 $Port 已被其他服务占用；为避免连错服务，本次没有启动。"
}

$pythonCandidates = @(
    (Join-Path $ProjectRoot '.venv\Scripts\python.exe'),
    $env:FINLAB_PYTHON,
    (Get-Command python.exe -ErrorAction SilentlyContinue).Source
)
$Python = $pythonCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $Python) { throw '没有找到可用的 Python。请使用已有项目环境或安装 Python 3.12。' }

$env:FINLAB_CONFIG_PATH = $ConfigFile
$env:FINLAB_STANDALONE_MODULE = $Module
$env:PYTHONUTF8 = '1'
$Arguments = @('-m', 'uvicorn', 'backend.standalone_workbench:app', '--host', '127.0.0.1', '--port', "$Port")
$Server = Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog

$Ready = $false
for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri "$Address/api/health" -TimeoutSec 2
        if ($health.status -eq 'ok' -and $health.module_id -eq $Module) { $Ready = $true; break }
    } catch { }
    $Server.Refresh()
    if ($Server.HasExited) { break }
}
if (-not $Ready) {
    if (-not $Server.HasExited) { Stop-Process -Id $Server.Id -Force -ErrorAction SilentlyContinue }
    $tail = if (Test-Path -LiteralPath $StderrLog) { (Get-Content -LiteralPath $StderrLog -Tail 20) -join "`n" } else { '' }
    throw "模块工作台未能启动：$Address`n$tail"
}

Start-Process $Address
Write-Host "模块工作台已启动：$Address" -ForegroundColor Green
Write-Host "日志：$LogDir" -ForegroundColor DarkGray
