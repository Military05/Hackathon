param([ValidateRange(1,65535)][int]$Port = 8001)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Сначала создайте .venv и установите requirements-ai.txt'
}
$env:PYTHONUTF8 = '1'
& (Join-Path $PSScriptRoot 'load-qwen.ps1')
Write-Host "Дашборд с MLP и AI: http://127.0.0.1:$Port"
& .\.venv\Scripts\python.exe -m src.agent.launch --port $Port
if ($LASTEXITCODE -ne 0) { throw 'Сервер AI завершился с ошибкой' }
