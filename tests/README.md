# tests

## Обновлённая цель v2

Приёмка v2 включает D1–D10/Q33–Q57: concurrent claim, idempotency, transfer/expiry, multi-tab presence, escalation/restart, routing/UI/offline/ML/agent. Старые tests/qa проверяют только прежний runner, не приложение. ../docs/TESTING.md.

Если слово незнакомо, открой [словарь простыми словами](../docs/GLOSSARY.md). Команды и названия полей не переводим: в коде они должны остаться точными.

Авторы добавляют проверки значимого поведения своих модулей; Лия (QA — тестировщик) готовит fixtures (подготовленные тестовые данные) и ведёт docs/TESTING.md. Тесты core от main, F1 от общей feature ветки.

В `qa/test_runner.py` реализованы проверки QA-инструмента на тестовом HTTP-сервере. Запуск: `python -m unittest discover -s tests/qa -p 'test_*.py' -v`. [Автоматический прогон против приложения](../docs/QA_AUTORUN.md). Успех test double не доказывает работу настоящего приложения.
