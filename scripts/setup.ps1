param([switch]$WithAI)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($WithAI) { py -3.12 -m venv .venv } else { python -m venv .venv }
if ($LASTEXITCODE -ne 0) { throw 'Нужен Python 3.11+ для базового режима, Python 3.12 для AI' }
$dependencyFile = if ($WithAI) { 'requirements-ai.txt' } else { 'requirements.txt' }
& .\.venv\Scripts\python.exe -m pip install --no-cache-dir -r $dependencyFile
if ($LASTEXITCODE -ne 0) { throw 'Установка зависимостей не завершилась' }
Write-Host 'Готово. Запуск: powershell -ExecutionPolicy Bypass -File scripts/start.ps1'
