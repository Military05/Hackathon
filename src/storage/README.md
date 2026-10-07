# src/storage

## Обновлённая цель v2

Егор С.: таблицы v2 Incident/Transfer/Presence/Notification/DispatchHistory/Idempotency и общие jobs/results. Одна транзакция для назначения/истории/повтора; persistent deadlines/seq и restart. Спецификация ../../docs/DISPATCH_OPERATIONS.md.

Если слово незнакомо, открой [словарь простыми словами](../../docs/GLOSSARY.md). Команды и названия полей не переводим: в коде они должны остаться точными.

Егор С. (A1): SQLite schema и короткие транзакции, raw (исходные) events и evidence (исходные события, подтверждающие вывод), query functions для tools (инструменты агента). Один writer. Runtime база не в Git. Историю не удалять неявно.

Сейчас здесь инструкция; реализованный модуль ещё отсутствует.

## Уточнения аудита

[PREBUILD_FIXES](../../docs/PREBUILD_FIXES.md) задаёт обязательные решения A1–A12/T28–T39. Реализацию отличать от frontend mock; согласованные поля и операции не менять отдельно от владельцев контракта.
