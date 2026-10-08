$ErrorActionPreference = 'Stop'
$localCli = Get-Command lms -ErrorAction SilentlyContinue
$localCliPath = if ($localCli) { $localCli.Source } else {
    @(
        (Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe'),
        'C:\Program Files\Bionic\resources\app\.webpack-bionic\lms.exe'
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (!$localCliPath -or !(Test-Path -LiteralPath $localCliPath)) {
    throw 'Не найден lms. Установите Bionic или LM Studio'
}
$modelsJson = & $localCliPath ls --json
if ($LASTEXITCODE -ne 0) { throw 'Откройте Bionic, чтобы подготовить локальный runtime' }
$models = $modelsJson | ConvertFrom-Json
$qwen = @($models | Where-Object {
    $_.modelKey -eq 'qwen3-4b' -and $_.quantization.name -eq 'Q4_K_M'
})
if ($qwen.Count -ne 1) {
    throw 'Не найдены веса Qwen3 4B Q4_K_M. Скачайте или импортируйте их в Local Models Bionic'
}
$loadedJson = & $localCliPath ps --json
if ($LASTEXITCODE -ne 0) { throw 'Не удалось проверить загруженные модели' }
$loadedModels = $loadedJson | ConvertFrom-Json
$loaded = @($loadedModels | Where-Object {
    $_.identifier -eq 'hackathon-qwen3-4b'
})
if (!$loaded.Count) {
    & $localCliPath load qwen3-4b --identifier hackathon-qwen3-4b --context-length 8192 --gpu max --parallel 1
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось загрузить Qwen3 4B' }
} elseif ($loaded[0].modelKey -ne 'qwen3-4b' -or $loaded[0].contextLength -ne 8192) {
    throw 'API-идентификатор занят другой конфигурацией. Проверьте lms ps перед запуском'
}
& $localCliPath server start --port 1234 --bind 127.0.0.1
if ($LASTEXITCODE -ne 0) { throw 'Не удалось запустить локальный API Qwen' }
$catalog = Invoke-RestMethod -Uri 'http://127.0.0.1:1234/v1/models' -TimeoutSec 5
if (!($catalog.data | Where-Object { $_.id -eq 'hackathon-qwen3-4b' })) {
    throw 'Локальный API не содержит модель hackathon-qwen3-4b'
}
Write-Host 'Qwen3 4B готова: http://127.0.0.1:1234/v1'
