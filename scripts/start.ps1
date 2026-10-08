param(
    [ValidateRange(1,65535)][int]$Port = 8000,
    [switch]$Lan,
    [string]$TlsCert,
    [string]$TlsKey,
    [switch]$WithAI,
    [string]$Database,
    [string]$PythonExecutable,
    [string]$EnvFile = '.env',
    [switch]$SkipModelLoad
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (!$PythonExecutable) { $PythonExecutable = Join-Path $PWD '.venv\Scripts\python.exe' }
if (!(Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) { throw 'Не найден Python; выполните scripts/setup.ps1 или укажите -PythonExecutable' }
if ($WithAI) {
    if (!$Database) { $Database = $env:DISPATCH_DB }
    if (!$Database -or ![IO.Path]::IsPathRooted($Database) -or !(Test-Path -LiteralPath $Database -PathType Leaf)) {
        throw 'Для AI обязателен абсолютный -Database существующей базы; пустая база не создаётся'
    }
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Порт $Port занят. Сначала согласуйте переключение существующего сервера"
}
$env:OMP_NUM_THREADS = '2'
$env:OPENBLAS_NUM_THREADS = '2'
$env:MKL_NUM_THREADS = '2'
if (($TlsCert -and !$TlsKey) -or ($TlsKey -and !$TlsCert)) { throw 'Укажите вместе -TlsCert и -TlsKey' }
if ($TlsCert -and (!(Test-Path -LiteralPath $TlsCert) -or !(Test-Path -LiteralPath $TlsKey))) { throw 'Не найден сертификат или ключ TLS' }
if ($Lan -and !$TlsCert -and $env:DISPATCH_TRUST_ENCRYPTED_TUNNEL -ne '1') {
    throw 'Для LAN необходимы -TlsCert/-TlsKey или проверенный зашифрованный туннель; см. docs/AUTH_SECURITY_V5.md'
}
if ($Lan -and $env:DISPATCH_ENABLE_AUTH -eq '0') { throw 'Нельзя открыть LAN-сервер без авторизации' }
$demoBindAddress = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
$demoScheme = if ($TlsCert) { 'https' } else { 'http' }
Write-Host "Откройте ${demoScheme}://127.0.0.1:$Port"
$demoServerArgs = @('-m', 'src.core.launch', '--host', $demoBindAddress, '--port', "$Port")
if ($Database) { $demoServerArgs += @('--db', $Database) }
if ($WithAI) {
    & $PythonExecutable -c "import sys, numpy, sklearn, joblib; sys.exit(0 if sys.version_info[:2] == (3,12) else 1)"
    if ($LASTEXITCODE -ne 0) { throw 'Для AI нужны Python 3.12 и зависимости: scripts/setup.ps1 -WithAI' }
    & $PythonExecutable -m src.core.launch --with-ai --db $Database --env-file $EnvFile --check-only
    if ($LASTEXITCODE -ne 0) { throw 'Проверка существующей базы не пройдена; сервер не запущен' }
    if (!$SkipModelLoad) { & (Join-Path $PSScriptRoot 'load-qwen.ps1') }
    $demoServerArgs += @('--env-file', $EnvFile)
    $demoServerArgs += '--with-ai'
}
if ($TlsCert) { $demoServerArgs += @('--ssl-certfile', $TlsCert, '--ssl-keyfile', $TlsKey) }
& $PythonExecutable @demoServerArgs
if ($LASTEXITCODE -ne 0) { throw 'Сервер завершился с ошибкой' }
