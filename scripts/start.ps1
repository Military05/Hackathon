param([ValidateRange(1,65535)][int]$Port = 8000, [switch]$Lan)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw 'First run scripts/setup.ps1' }
$env:OMP_NUM_THREADS = '2'
$env:OPENBLAS_NUM_THREADS = '2'
$env:MKL_NUM_THREADS = '2'
$demoBindAddress = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
Write-Host "Open http://127.0.0.1:$Port"
& .\.venv\Scripts\python.exe -m uvicorn src.core.main:app --host $demoBindAddress --port $Port --workers 1
if ($LASTEXITCODE -ne 0) { throw 'Server exited with an error' }
