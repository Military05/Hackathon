param(
    [Parameter(Mandatory=$true)][string]$Database,
    [string]$PythonExecutable,
    [string]$Checkout = (Split-Path -Parent $PSScriptRoot)
)
if (!$PythonExecutable) { $PythonExecutable = Join-Path $Checkout '.venv\Scripts\python.exe' }
if (![IO.Path]::IsPathRooted($Database) -or !(Test-Path -LiteralPath $Database -PathType Leaf)) { throw 'Нужен абсолютный путь существующей базы' }
New-Item -ItemType Directory -Path (Join-Path $Checkout 'data\local\diagnostics') -Force | Out-Null
$ErrorActionPreference = 'Stop'
$taskRoot = $Checkout
$taskCheckout = $Checkout
$taskPython = $PythonExecutable
$taskHelper = Join-Path $taskRoot 'scripts\device_runtime.py'
$taskReport = Join-Path $taskRoot 'data\local\diagnostics\switch-contour-report.json'
$taskLog = Join-Path $taskRoot 'data\local\diagnostics\start-current-contour.log'
$taskFailure = Join-Path $taskRoot 'data\local\diagnostics\start-current-contour-error.txt'
$taskMutex = New-Object System.Threading.Mutex($false, 'Local\ContourDeviceLaunch8000')
if (!$taskMutex.WaitOne(0)) { $taskMutex.Dispose(); exit }

function Test-CurrentContour {
    & $taskPython -X utf8 $taskHelper check --database $Database *> $null
    return $LASTEXITCODE -eq 0
}

try {
    if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Для запуска с существующей базой нужны права администратора' }
    if (!(Test-CurrentContour)) {
        if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) {
            & (Join-Path $taskCheckout 'scripts\restart-device.ps1') -Database $Database -PythonExecutable $PythonExecutable -Checkout $Checkout
        } else {
            & $taskPython -X utf8 $taskHelper prepare --database $Database
            if ($LASTEXITCODE -ne 0) { throw 'Проверка базы или резервная копия не готовы; сервер не запущен' }
            $models = Invoke-RestMethod -Uri 'http://127.0.0.1:1234/v1/models' -TimeoutSec 5
            if (!($models.data | Where-Object { $_.id -eq 'hackathon-qwen3-4b' })) { throw 'Сначала откройте LM Studio и загрузите Qwen с именем hackathon-qwen3-4b' }
            $taskNew = Start-Process -FilePath $taskPython -ArgumentList @('-u','-X','utf8',('"{0}"' -f $taskHelper),'run','--database',('"{0}"' -f $Database),'--root',('"{0}"' -f $taskCheckout)) -WorkingDirectory $taskCheckout -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRoot 'data\local\diagnostics\contour-current.stdout.log') -RedirectStandardError (Join-Path $taskRoot 'data\local\diagnostics\contour-current.stderr.log') -PassThru
            for ($attempt = 0; $attempt -lt 45; $attempt++) {
                if (Test-CurrentContour) { break }
                if (!(Get-Process -Id $taskNew.Id -ErrorAction SilentlyContinue)) { throw 'Сервер завершился; проверьте contour-current.stderr.log' }
                Start-Sleep -Seconds 1
            }
            if (!(Test-CurrentContour)) { throw 'Сервер не подтвердил готовность. Проверьте журнал запуска и LM Studio' }
            & $taskPython -X utf8 $taskHelper verify --database $Database
            if ($LASTEXITCODE -ne 0) { throw 'Проверка сохранности аккаунтов не пройдена' }
            $report = Get-Content -LiteralPath $taskReport -Raw -Encoding UTF8 | ConvertFrom-Json
            $listenerPid = Get-NetTCPConnection -State Listen -LocalPort 8000 | Select-Object -First 1 -ExpandProperty OwningProcess
            $report | Add-Member -NotePropertyName new_pid -NotePropertyValue $listenerPid -Force
            $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $taskReport -Encoding UTF8
        }
    }
    if (!(Test-CurrentContour)) { throw 'Новая версия не запущена. Существующий сервер не заменялся без успешных проверок' }
    ('Новая версия готова: http://127.0.0.1:8000 — ' + (Get-Date).ToString('s')) | Set-Content -LiteralPath $taskLog -Encoding UTF8
    Start-Process 'http://127.0.0.1:8000'
} catch {
    $_.Exception.Message | Set-Content -LiteralPath $taskFailure -Encoding UTF8
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Контур: запуск не завершён', 'OK', 'Warning') | Out-Null
} finally {
    $taskMutex.ReleaseMutex(); $taskMutex.Dispose()
}
