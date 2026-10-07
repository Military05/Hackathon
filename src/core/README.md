# Сервер / Егор С.

Реализованы main.py, service.py и geometry.py: FastAPI, правила и координация.
SQLite находится в src/storage/sqlite_store.py. Запуск: scripts/start.ps1.
Контракты: docs/CONTRACTS.md, DISPATCH_OPERATIONS.md и PREBUILD_FIXES.md.
Отдельные simulator/ML/agent подключаются через env flags после передачи владельцев;
их код в текущий этап не включён. Проверки: tests/runtime.
