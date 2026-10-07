# B1: общий локальный диспетчер

Реализованы LM Studio/OpenAI-compatible и Ollama adapters, четыре read-only tools,
сохранённый snapshot, проверка ID/полей/значений, общий SQLite job store и async worker.
После аудита: 1 running, 2 queued, 120 с ожидания, 60 с всего выполнения, 3 обращения
к модели, 6 tools. Ожидаемое время проверяется до приёма и строго меньше queue budget.

Qwen3 4B — готовая локальная языковая модель только для диспетчера. Собственная обучаемая
нейросеть — src/ml. Веса Qwen здесь не обучаются, облачного fallback нет.

    python -m src.agent.probe --env-file .env
    python -m src.agent.main --env-file .env --fixture tests/fixtures/agent_v2.json

Сначала уже скачанная Qwen в Local/Bionic/LM Studio с API; точный model ID из /v1/models.
Ollama qwen3:4b — резерв. Probe требует настоящую модель, tool call, Python-исполнение
и валидный результат; test double и недоступный runtime не получают PASS.
Main требует явный fixture и запускает только стенд B1 на 8001.

A1 подключает CaptureProvider, build_router и service в существующий lifespan и SQLite.
Таблиц Incident/Asset/Policy здесь нет. Смена владельца не меняет snapshot. Новые данные
и версии дают stale. Кэш общий; автор запроса хранится отдельно. Restart: running
failed/interrupted, queued сохраняют оригинальный deadline. Конфигурацию сохранённого
задания нельзя незаметно заменить; один worker защищён файловым lock.

Текст измеренных фактов формирует программа из проверенных значений; гипотезы Qwen
имеют confidence/limitations, рекомендации адресованы человеку. Tools не выполняют
SQL, shell, управление оборудованием, звонки или координацию.

Схемы и команды Windows: [AI_INTEGRATION](../../docs/AI_INTEGRATION.md).
