# Локальные Qwen и MLP в backend v5

Рабочая ветка: `codex/ai-ml-integration`. Базовый commit:
`e6867f5648c5e80c87bd13cfb61866d46a44d726`.
Исходные AI/ML-модули восстановлены из
`9ca9454950e0e6648b3ec2dacf9e0f91fa1d7151` и адаптированы к v5.
Дашборд и внешний simulator не изменены. Расширения базового приложения
остаются выключенными по умолчанию; отдельный launcher включает их.

## Запуск

Из корня этого checkout в PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ai-dev.txt
Copy-Item .env.example .env
.\scripts\start-ai.ps1
```

Перед запуском нужны перенесённые `artifacts/local/movement.joblib` и
сопутствующие metadata, а также установленный Bionic с моделью Qwen3 4B Q4_K_M.
Для первого входа в другом терминале создайте администратора:

```powershell
.\.venv\Scripts\python.exe scripts/manage_users.py create-admin
```

Пароль вводится интерактивно; пароля по умолчанию нет. Интерфейс доступен по
`http://127.0.0.1:8001`, локальный API модели — `http://127.0.0.1:1234/v1`.
Не запускайте второй сервер на уже занятом порту.

## Модели и безопасность

Qwen3 4B Q4_K_M загружена с `--gpu max`, context 4096, parallel 1,
identifier `hackathon-qwen3-4b`. На проверенном ноутбуке используется
NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB. `nvidia-smi` подтверждает
CUDA-процесс `llama-server.exe`; при ожидании занято около 3155 MiB VRAM.
Веса остаются в локальном хранилище Bionic:
`C:\Users\_ADMIN_\.lmstudio\models\lmstudio-community\Qwen3-4B-GGUF\Qwen3-4B-Q4_K_M.gguf`.
SHA256: `d0c2ac093a77c402f2ddc23a64f68b7c70cfef151899b33e3a6066247104ced3`.
В Git веса не добавляются.

MLP выполняется на CPU и использует перенесённые обученные веса, без
повторного обучения. SHA256 `movement.joblib`:
`24b808dd0a513fc701862df96bbcd5fff9df6e048ad40d7e5ed3ae96cec351a7`.
Сохраняются точные версии numpy 2.3.5, scikit-learn 1.8.0, joblib 1.5.3.

MLP сохраняет реальные ModelObservation и связанные Incident в общей SQLite.
Qwen читает замороженный снимок через разрешённые инструменты. Для модельного
подозрения обязательны проверенные status, score и threshold связанного
наблюдения. Score не является вероятностью аварии. Недостоверные значения
и неподтверждённые факты не публикуются как успешный анализ.

Общая очередь содержит одно выполняемое и максимум два ожидающих задания.
Сохраняются session authentication, CSRF, Expected-User и source-key проверки.
Исторический standalone API агента предназначен для отдельных тестов;
production-подключение использует `src/agent/runtime.py` и сессии backend v5.

## Выполненные проверки

На этом ноутбуке прошли:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/runtime -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/qa -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m pytest tests/agent tests/ml -q
node --check src/interface/web/app.js
node --test tests/map-view.test.cjs tests/sensors-panel.test.cjs
node --test tests/interface/*.cjs
node --test tests/interface.test.js
```

Результаты: runtime 104, QA 18, agent/ML 61, Node 29 + 16 + 20 — всего
248 успешных автоматических тестов. `pip check` и проверка синтаксиса также
прошли. Логи сохранены в игнорируемом `artifacts/local`.

Живые проверки настоящей Qwen: ZONE 5,25 с; SENSOR 13,78 с;
D4 после выгрузки/загрузки 15,86 с; D4 повторно 15,40 с.
`scripts/ai_live_acceptance.py` проверил настоящий HTTP backend, авторизацию,
приём Events, MLP, сохранение Incident, очередь и Qwen: PASS, 14,92 с.
Отчёт: `artifacts/local/qwen-backend-live.json`.
Эта детерминированная проверка использует синтетический D4 и явный вызов
inference с выключенным scheduler. Отдельная проверка работающего scheduler
подтвердила 72 Events и 9 ModelObservation, включая нормальные оценки трёх
машин: `artifacts/local/scheduler-smoke.json`.

Ограничения: синтетические сценарии не доказывают качество на реальном заводе.
Перезапуск модели не является холодным запуском всей ОС. Длительный benchmark,
работа без сети после перезагрузки и приёмка в трёх браузерных профилях
не выполнены и здесь не заявляются.
