# Запуск и подключение B1/B2

Реализованы AI-задачи Гаджи и Гриши. [Источники требований](AI_BRANCH_REVIEW.md).
Dashboard, основной backend, правила и координация операторов в этом изменении не меняются.

## Подготовка на Windows

Из корня Hackathon, с Python 3.12 (проверено 3.12.14):

    git switch codex/three-dispatcher-plan
    git pull --ff-only origin codex/three-dispatcher-plan
    py -3.12 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
    Copy-Item .env.example .env

Распаковать архив модели в корень: artifacts/local/movement.joblib и
artifacts/local/movement.metadata.json. Веса MLP уже обучены; перенос .venv между ОС
не требуется. Missing artifact → not_trained; hash/версии не совпадают → artifact_invalid;
менее 6 уникальных позиций → insufficient_data, score=null.

Повторить собственное обучение и экспорт независимого D4:

    .\.venv\Scripts\python.exe -m src.ml.generate --seed 42 --test-seed 20261007 --output data/generated
    .\.venv\Scripts\python.exe -m src.ml.train --input data/generated --output artifacts/local
    .\.venv\Scripts\python.exe -m src.ml.evaluate --input data/generated --model artifacts/local/movement.joblib
    .\.venv\Scripts\python.exe -m src.ml.demo --input data/generated --model artifacts/local/movement.joblib

Demo создаёт episode, реальные ModelObservation и B1 snapshot. Они включены в архив.
Фактические метрики/ошибки: [ML_REPORT](ML_REPORT.md).

## Настоящий Qwen3 4B

На своём компьютере открыть установленный Local/Bionic/LM Studio, выбрать уже скачанную
локальную Qwen3 4B, запустить сервер в Developer. Проверить фактический порт и model ID:

    Invoke-RestMethod http://127.0.0.1:1234/v1/models

В .env заполнить LOCAL_LLM_MODEL точным id из ответа. При другом порте изменить
LOCAL_LLM_BASE_URL; при авторизации заполнить LOCAL_LLM_API_KEY только локально.

    .\.venv\Scripts\python.exe -m src.agent.probe --env-file .env
    .\.venv\Scripts\python.exe -m src.agent.probe --env-file .env --fixture artifacts/local/d4-agent-snapshot.json --incident-id INC-MODEL-D4 --output artifacts/local/qwen-d4-probe.json
    .\.venv\Scripts\python.exe -m src.agent.probe --env-file .env --incident-id INC-NEVER-STARTED --output artifacts/local/qwen-sensor-probe.json

PASS требует реальную модель, обычный ответ, настоящий tool call, Python-исполнение и
валидные evidence. Exit 2/BLOCKED сохраняет причину в JSON. API 1234 в среде разработки
недоступен: живой Qwen здесь не подтверждён. Qwen работает только диспетчером; собственное
обучение относится к MLP, новая языковая модель с нуля не требуется.

Резерв Ollama: LOCAL_LLM_PROVIDER=ollama, LOCAL_LLM_BASE_URL=http://127.0.0.1:11434,
LOCAL_LLM_MODEL=qwen3:4b. Сначала проверить уже установленные модели через /api/tags.
Автоматического скачивания, замены модели и cloud fallback нет.

Для offline показа заранее подготовить Python, runtime, Qwen и зависимости на целевой ОС:

    .\.venv\Scripts\python.exe -m pip download -r requirements-ai.txt --dest artifacts/local/wheelhouse
    .\.venv\Scripts\python.exe -m pip install --no-index --find-links artifacts/local/wheelhouse -r requirements-ai.txt

Cold start без внешнего интернета, версия runtime, RAM/GPU/VRAM и latency ещё проверяются
на ноутбуке. Probe сохраняет CPU/RAM при доступности; GPU/VRAM записываются отдельно.

## Явный тестовый стенд без A1

    .\.venv\Scripts\python.exe -m src.agent.main --env-file .env --fixture tests/fixtures/agent_v2.json --port 8001
    $headers = @{"X-Demo-Operator"="dispatcher-1"}
    $job = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8001/api/incidents/INC-ZONE-1/analysis -Headers $headers
    Invoke-RestMethod "http://127.0.0.1:8001/api/agent-jobs/$($job.job_id)"

POST возвращает 202, job_id/status; профиль обязателен. Runtime unavailable → 503,
queue_full/queue_busy → 429. GET: queued/running/completed/failed, result/error, stale.
Fixture mode явный; этот стенд не является основным сервером карты.
Профили стенда совпадают с main frontend: dispatcher-1, dispatcher-2, dispatcher-3.

## Подключение для Егора С. (A1)

Использовать существующие FastAPI lifespan и SQLite. Пример адаптации; методы query
предоставляет A1, такого backend-файла в этой ветке пока нет:

    from dataclasses import replace
    import asyncio
    from src.agent.config import AgentConfig
    from src.agent.providers import CaptureProvider
    from src.agent.model_client import LocalModelClient
    from src.agent.service import AgentService
    from src.agent.api import build_router

    config = replace(AgentConfig.from_env(), database=shared_sqlite_path)
    async def capture(incident_id):
        return await asyncio.to_thread(query.capture_agent_snapshot, incident_id)
    agent = AgentService(config, CaptureProvider(capture), LocalModelClient(config))
    app.include_router(build_router(agent, query.operator_exists))
    # В существующем lifespan: await agent.start(); ...; await agent.stop()

Capture в ОДНОЙ короткой транзакции возвращает словарь:

| Поле | Содержимое |
| --- | --- |
| as_of | ISO timestamp с timezone |
| rule_version / model_version | Активные версии правил и MLP |
| incident | Карточка с incident_id и evidence_event_ids |
| events / observations | Захваченные Event / ModelObservation, максимум 100 каждого |
| policies | Ключ asset/employee ID → объект с policy_version |
| sensor_health | Ключ sensor ID → status, last_received_at, thresholds и detector timestamps |
| history_bounds | Реальные since/until захваченной ограниченной истории |

Полный пример: tests/fixtures/agent_v2.json; настоящий D4: artifacts/local/d4-agent-snapshot.json.
Все incident evidence должны присутствовать в events; общий snapshot не более 256 KB.
Контекст U1–U3 добавляется при необходимости. Транзакцию закрыть ДО LLM. Tools читают
сохранённый JSON, не живые query; входной snapshot_id не доверяется, hash вычисляет модуль.
Новые измерения/evidence/версии меняют snapshot; owner/revision/claim/transfer — нет.
Never_started: events/evidence могут быть пустыми, но health сохраняет threshold,
waiting_since/detected_at и last_received_at=null.

Таблицы только b1_agent_jobs/b1_agent_requesters, без второй базы доменных объектов.
Сеть вне DB-транзакции, один async worker, один uvicorn process. Кэш общий для профилей;
requesters отдельно от владельца. Restart: running failed/interrupted, queued сохраняют
оригинальный queue_deadline. Изменённую model/prompt конфигурацию нужно запросить заново.
История failed сохраняется.

## Подключение MLP для A1

    from src.ml.inference import MovementModel, WindowEvaluator, AnomalyState
    model = MovementModel("artifacts/local/movement.joblib")  # один раз при startup
    evaluator = WindowEvaluator(model, published=restored_observations_by_asset)
    observation, fresh = await asyncio.to_thread(evaluator.evaluate, saved_events, asset_id, window_end)
    if fresh:
        next_state, action = restored_anomaly_state.advance(observation)
        # A1 атомарно сохраняет observation, watermark, next_state и incident effect.

Window end на UTC сетке 5 секунд; окно [end-10,end], Event-time порядок, lexicographic max
event_id при одинаковом времени. Late Event остаётся в истории A1, опубликованное окно
не пересчитывается. При fresh=False lifecycle повторно не продвигать. Backend восстанавливает
watermark/state из своей базы после restart.

Create открывает model_anomaly; reuse использует существующий Incident; restore после
двух достаточных normal окон завершает условие. Insufficient/unavailable не закрывает
условие и сбрасывает normal counter. Авторизованный dismiss_model сохраняет state.dismiss()
и disposition; raw observation не изменяется. Suppressed эпизод не создаёт новой рабочей
задачи до restore; следующая anomaly получает новый Incident. PATCH, owner/revision/request_id,
правила и хранение Incident остаются в A1.

Frontend main уже использует эти POST/GET analysis/job маршруты и отображает summary,
stale и error. Facts/hypotheses/recommendations готовы для следующей интеграции; текущий
экран показывает только summary. Изменения экрана в эту работу не входят.

## Проверка и оставшаяся приёмка

    .\.venv\Scripts\python.exe -m pip install -r requirements-ai-dev.txt
    .\.venv\Scripts\python.exe -m pytest tests/ml tests/agent tests/qa -q

63 теста прошли: модульная логика, реальная обученная MLP → snapshot → tools → validator,
HTTP protocol на явной model test double, 202/poll/cache/ошибки API. TestClient даёт одно
предупреждение о будущем переходе Starlette на httpx2; проверки проходят с текущими версиями.
Это не свидетельство настоящего Qwen или полного D1–D10 через общий backend.

От владельца/команды: запустить скачанную Qwen и probe на своей машине, перенести артефакт
на ноутбук; A1 подключить snapshot query/router/inference; затем Лия проверяет полный путь,
offline cold start и laptop latency. Необходимые архитектурные параметры уже взяты из инструкций.

Официальные API: [LM Studio tools](https://lmstudio.ai/docs/developer/openai-compat/tools),
[Ollama chat](https://docs.ollama.com/api/chat).
