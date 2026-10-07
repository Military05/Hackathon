$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ required' }
& .\.venv\Scripts\python.exe -m pip install --no-cache-dir -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
Write-Host 'Ready. Run: powershell -ExecutionPolicy Bypass -File scripts/start.ps1'
