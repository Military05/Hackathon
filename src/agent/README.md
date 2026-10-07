# src/agent

## Обновлённая цель v2

Гаджи: Bionic Local → проверенный LM Studio/openai_compatible API; Ollama резерв. Общая очередь 1 running/20 queued/120 секунд ожидания/60 выполнения; tools/evidence/cache/restart. Не выполняет координационные действия. ../../docs/DISPATCH_OPERATIONS.md.

Если слово незнакомо, открой [словарь простыми словами](../../docs/GLOSSARY.md). Команды и названия полей не переводим: в коде они должны остаться точными.

Гаджи (B1): LocalModelClient, tools (инструменты агента) registry (список разрешённых инструментов), bounded orchestrator, async job queue, evidence (исходные события, подтверждающие вывод) validator. Один active job, max3model/max6tools/deadline60. Read-only (только для чтения) tools; реальный trace (журнал действий). Формат в ../../docs/CONTRACTS.md.

Сейчас здесь инструкция; реализованный модуль ещё отсутствует.
