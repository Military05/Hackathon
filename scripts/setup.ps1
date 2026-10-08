param([switch]$WithAI, [string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if ($WithAI) {
    & $Python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)"
    if ($LASTEXITCODE -ne 0) { throw 'MLP requires Python 3.12. Use -Python with the path to Python 3.12.' }
    if (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
        & .\.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)"
        if ($LASTEXITCODE -ne 0) { throw 'Existing .venv uses another Python version. Rename it and run setup -WithAI with Python 3.12 in a new environment.' }
    }
}
& $Python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ required' }
$demoRequirements = if ($WithAI) { 'requirements-ai.txt' } else { 'requirements.txt' }
& .\.venv\Scripts\python.exe -m pip install --no-cache-dir -r $demoRequirements
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
$demoStartScript = if ($WithAI) { 'start-ai.ps1' } else { 'start.ps1' }
Write-Host "Ready. Run: powershell -ExecutionPolicy Bypass -File scripts/$demoStartScript"
