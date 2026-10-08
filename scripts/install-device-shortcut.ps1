param(
    [Parameter(Mandatory=$true)][string]$Database,
    [string]$PythonExecutable = (Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe')
)
$ErrorActionPreference = 'Stop'
if (![IO.Path]::IsPathRooted($Database) -or !(Test-Path -LiteralPath $Database -PathType Leaf)) { throw 'Нужен абсолютный путь существующей базы' }
if (!(Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) { throw 'Python не найден' }
$launcher = Join-Path $PSScriptRoot 'start-device.ps1'
$desktop = [Environment]::GetFolderPath('Desktop')
$linkPath = Join-Path $desktop 'Контур - обновлённая версия.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shortcut.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Database "{1}" -PythonExecutable "{2}"' -f $launcher,$Database,$PythonExecutable
$shortcut.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$shortcut.Description = 'Контур: запустить проверенную версию с существующей базой'
$shortcut.Save()
$bytes = [IO.File]::ReadAllBytes($linkPath)
$bytes[0x15] = $bytes[0x15] -bor 0x20
[IO.File]::WriteAllBytes($linkPath,$bytes)
Write-Output "Ярлык создан: $linkPath"
