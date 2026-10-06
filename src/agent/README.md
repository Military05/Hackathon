# src/agent

Если слово незнакомо, открой [словарь простыми словами](../../docs/GLOSSARY.md). Команды и названия полей не переводим: в коде они должны остаться точными.

B1: OllamaClient, tools (инструменты агента) registry (список разрешённых инструментов), bounded orchestrator, async job queue, evidence (исходные события, подтверждающие вывод) validator. Один active job, max3model/max6tools/deadline60. Read-only (только для чтения) tools; реальный trace (журнал действий). Формат в ../../docs/CONTRACTS.md.

Сейчас здесь инструкция; реализованный модуль ещё отсутствует.
