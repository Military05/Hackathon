param(
    [ValidateRange(1,65535)][int]$Port = 8000,
    [string]$Database,
    [switch]$Lan,
    [string]$TlsCert,
    [string]$TlsKey,
    [string]$PythonExecutable
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start.ps1') -WithAI -Port $Port -Database $Database -PythonExecutable $PythonExecutable -Lan:$Lan -TlsCert $TlsCert -TlsKey $TlsKey
