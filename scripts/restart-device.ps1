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
$taskPython = $PythonExecutable
$taskHelper = Join-Path $taskRoot 'scripts\device_runtime.py'
$taskCheckout = $Checkout
$taskDatabase = $Database
$taskReport = Join-Path $taskRoot 'data\local\diagnostics\switch-contour-report.json'
$taskLog = Join-Path $taskRoot 'data\local\diagnostics\switch-contour.log'
$taskFailure = Join-Path $taskRoot 'data\local\diagnostics\switch-contour-error.txt'
$taskStopped = $false
$taskNew = $null

function Start-Contour([string]$Directory, [string]$Label) {
    $outFile = Join-Path $taskRoot "data\local\diagnostics\contour-$Label.stdout.log"
    $errFile = Join-Path $taskRoot "data\local\diagnostics\contour-$Label.stderr.log"
    return Start-Process -FilePath $taskPython -ArgumentList @('-u','-X','utf8',('"{0}"' -f $taskHelper),'run','--database',('"{0}"' -f $Database),'--root',('"{0}"' -f $Directory)) -WorkingDirectory $Directory -WindowStyle Hidden -RedirectStandardOutput $outFile -RedirectStandardError $errFile -PassThru
}

function Wait-Contour([int]$ProcessId) {
    for ($attempt = 0; $attempt -lt 45; $attempt++) {
        if (!(Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { throw 'Новый сервер завершился; проверьте журнал запуска' }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
            if ($health.agent.status -eq 'ready' -and $health.agent.worker_running -and $health.ml.status -eq 'ready') { return }
        } catch { }
        Start-Sleep -Seconds 1
    }
    throw 'Сервер не подтвердил готовность AI/ML за отведённое время'
}

function Stop-StartedContour([int]$ProcessId) {
    # The Windows virtualenv launcher may own a child interpreter; stop only this tree.
    $tree = @($ProcessId)
    $processes = @(Get-CimInstance Win32_Process)
    for ($index = 0; $index -lt $tree.Count; $index++) {
        $parentId = $tree[$index]
        $children = @($processes | Where-Object { $_.ParentProcessId -eq $parentId } | Select-Object -ExpandProperty ProcessId)
        foreach ($childId in $children) { if ($tree -notcontains $childId) { $tree += $childId } }
    }
    [array]::Reverse($tree)
    foreach ($memberId in $tree) { if (Get-Process -Id $memberId -ErrorAction SilentlyContinue) { Stop-Process -Id $memberId -ErrorAction Stop } }
}

Start-Transcript -Path $taskLog -Force | Out-Null
try {
    if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Запустите переключение с правами администратора' }
    $owner = @(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owner.Count -ne 1) { throw 'Владелец порта 8000 изменился; переключение отменено' }
    $taskOldPid = [int]$owner[0]
    $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$taskOldPid"
    if ($oldProcess.ExecutablePath -notmatch 'python(w)?\.exe$' -or $oldProcess.CommandLine -notmatch '(src\.core\.(main|launch)|(?:switch_contour|device_runtime)\.py.+run)') { throw 'Процесс на порту 8000 не соответствует ожидаемому серверу' }
    & $taskPython -X utf8 $taskHelper prepare --database $Database --pid $taskOldPid
    if ($LASTEXITCODE -ne 0) { throw 'Предварительная проверка или резервное копирование не пройдены. Старый сервер оставлен работающим' }
    $report = Get-Content -LiteralPath $taskReport -Raw -Encoding UTF8 | ConvertFrom-Json
    Set-Location -LiteralPath $taskCheckout
    & $taskPython -X utf8 -m src.core.launch --with-ai --db $taskDatabase --env-file (Join-Path $taskRoot '.env') --check-only
    if ($LASTEXITCODE -ne 0) { throw 'Настройки новой версии не прошли проверку' }
    & $taskPython -c 'import numpy, sklearn, joblib; from src.agent.runtime import AgentManager; from src.ml.movement import MovementModel'
    if ($LASTEXITCODE -ne 0) { throw 'Зависимости AI/ML недоступны' }
    $models = Invoke-RestMethod -Uri 'http://127.0.0.1:1234/v1/models' -TimeoutSec 5
    if (!($models.data | Where-Object { $_.id -eq 'hackathon-qwen3-4b' })) { throw 'Qwen не загружена в LM Studio' }
    $owner = @(Get-NetTCPConnection -State Listen -LocalPort 8000 | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owner.Count -ne 1 -or $owner[0] -ne $taskOldPid) { throw 'Владелец порта изменился перед переключением' }
    Stop-Process -Id $taskOldPid -ErrorAction Stop
    $taskStopped = $true
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        if (!(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 300
    }
    if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) { throw 'Порт 8000 не освободился' }
    $taskNew = Start-Contour $taskCheckout 'current'
    Wait-Contour $taskNew.Id
    & $taskPython -X utf8 $taskHelper verify --database $Database
    if ($LASTEXITCODE -ne 0) { throw 'Проверка новой версии не пройдена' }
    $report = Get-Content -LiteralPath $taskReport -Raw -Encoding UTF8 | ConvertFrom-Json
    $listenerPid = Get-NetTCPConnection -State Listen -LocalPort 8000 | Select-Object -First 1 -ExpandProperty OwningProcess
    $report | Add-Member -NotePropertyName new_pid -NotePropertyValue $listenerPid -Force
    $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $taskReport -Encoding UTF8
    Write-Output 'Переключение завершено: новая версия на порту 8000, существующая база сохранена'
} catch {
    $message = $_.Exception.Message
    if ($taskStopped) {
        try {
            if ($taskNew -and (Get-Process -Id $taskNew.Id -ErrorAction SilentlyContinue)) { Stop-StartedContour $taskNew.Id }
            Start-Sleep -Seconds 1
            if (!(Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue)) {
                $fallback = Start-Contour $report.process.cwd 'rollback'
                Wait-Contour $fallback.Id
                $message += '; предыдущая версия снова запущена. База не восстанавливалась поверх рабочей'
            }
        } catch { $message += '; автоматический возврат не удался: ' + $_.Exception.Message }
    }
    $message | Set-Content -LiteralPath $taskFailure -Encoding UTF8
    Write-Error $message
} finally {
    Stop-Transcript | Out-Null
}
