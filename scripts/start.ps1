param(
    [ValidateRange(1,65535)][int]$Port = 8000,
    [switch]$Lan,
    [string]$TlsCert,
    [string]$TlsKey
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw 'First run scripts/setup.ps1' }
$env:OMP_NUM_THREADS = '2'
$env:OPENBLAS_NUM_THREADS = '2'
$env:MKL_NUM_THREADS = '2'
if (($TlsCert -and !$TlsKey) -or ($TlsKey -and !$TlsCert)) { throw 'Provide both -TlsCert and -TlsKey' }
if ($TlsCert -and (!(Test-Path -LiteralPath $TlsCert) -or !(Test-Path -LiteralPath $TlsKey))) { throw 'TLS certificate or key file was not found' }
if ($Lan -and !$TlsCert -and $env:DISPATCH_TRUST_ENCRYPTED_TUNNEL -ne '1') {
    throw 'LAN authentication requires -TlsCert/-TlsKey or a verified encrypted tunnel (see docs/AUTH_SECURITY_V5.md)'
}
if ($Lan -and $env:DISPATCH_ENABLE_AUTH -eq '0') { throw 'Do not expose an unauthenticated test server to LAN' }
$demoBindAddress = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
$demoScheme = if ($TlsCert) { 'https' } else { 'http' }
Write-Host "Open ${demoScheme}://127.0.0.1:$Port"
$demoServerArgs = @('-m', 'uvicorn', 'src.core.main:app', '--host', $demoBindAddress, '--port', "$Port", '--workers', '1', '--no-proxy-headers')
if ($TlsCert) { $demoServerArgs += @('--ssl-certfile', $TlsCert, '--ssl-keyfile', $TlsKey) }
& .\.venv\Scripts\python.exe @demoServerArgs
if ($LASTEXITCODE -ne 0) { throw 'Server exited with an error' }
