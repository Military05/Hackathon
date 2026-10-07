# SQLite / Егор С.

sqlite_store.py: WAL, короткие атомарные транзакции, отдельные записи событий,
истории, transfers, requests/idempotency, notifications, presence и observations.
Одно приложение/worker. Путь по умолчанию data/runtime/dispatch.db; override DISPATCH_DB.
История сохраняется после restart и не отправляется в Git.
