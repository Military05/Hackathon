param(
    [ValidateRange(1,65535)][int]$Port = 8001,
    [string]$EnvFile = '.env',
    [switch]$SkipModelLoad
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Сначала выполните scripts/setup.ps1 -WithAI с Python 3.12'
}
if (!(Test-Path -LiteralPath $EnvFile)) {
    if ($EnvFile -ne '.env') { throw "Не найден файл конфигурации $EnvFile" }
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
$env:PYTHONUTF8 = '1'
if (!$SkipModelLoad) { & (Join-Path $PSScriptRoot 'load-qwen.ps1') }
Write-Host "Дашборд с MLP и AI: http://127.0.0.1:$Port"
& .\.venv\Scripts\python.exe -m src.agent.launch --port $Port --env-file $EnvFile
if ($LASTEXITCODE -ne 0) { throw 'Сервер AI завершился с ошибкой' }
