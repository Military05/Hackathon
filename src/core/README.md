# src/core

## Обновлённая цель v2

Егор С.: кроме events/rules реализует v2 dispatch/presence/transfers/reminder/escalation/summary и атомарные действия. Контракт owner/revision/request_id/history — ../../docs/DISPATCH_OPERATIONS.md. Один server worker; очередь ИИ не очередь тревог.

Если слово незнакомо, открой [словарь простыми словами](../../docs/GLOSSARY.md). Команды и названия полей не переводим: в коде они должны остаться точными.

Егор С. (A1): FastAPI main.py, Event validation/idempotency, D1–D3, scheduler (таймер фоновых проверок), read/query API (способ обмена данными с сервером). Один server worker (обработчик задач). Все общие routes согласовать с Егором М. (A2) / Гаджи (B1) / Гришей (B2). Точные поля в ../../docs/CONTRACTS.md.

Сейчас здесь инструкция; реализованный модуль ещё отсутствует.

## Уточнения аудита

[PREBUILD_FIXES](../../docs/PREBUILD_FIXES.md) задаёт обязательные решения A1–A12/T28–T39. Реализацию отличать от frontend mock; согласованные поля и операции не менять отдельно от владельцев контракта.
