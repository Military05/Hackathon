# Запуск и повторение текущей сборки

Нормативная команда запуска этой поставки — [RUNTIME_QUICKSTART](RUNTIME_QUICKSTART.md).
Рабочая область и этапы — [DEVELOPMENT_STAGES](DEVELOPMENT_STAGES.md).

## Установка и запуск

Python 3.11+; зависимости устанавливаются заранее с интернетом.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Открыть http://127.0.0.1:8000. API Swagger: /docs. Один сервер/worker и SQLite.
Сервер отдаёт локальные файлы и не скачивает карту, модель или веса при запуске.
Для трёх рабочих мест открыть ?operator=dispatcher-1, dispatcher-2 и dispatcher-3.
Другие ноутбуки: start.ps1 -Lan и адрес сервера в доверенной локальной сети.

## Данные и подключаемые модули

site.json — общий план, датчики, здания, сектора, маршруты и пороги demo.
Event принимается POST /api/events; UI читает общую базу. История хранится
в data/runtime/dispatch.db или пути DISPATCH_DB. Перезапуск не стирает события,
owner/transfer/notification/history. Клиенты заново подтверждают presence.

В этой поставке нет исполняемых модулей симулятора, ML и агента. Эти части делает
другой исполнитель; сервер явно сообщает unavailable. После передачи включаются
только готовые модули по DEVELOPMENT_STAGES. Не запускайте два генератора событий
на одну базу; это вопрос интеграции второго исполнителя.

Без source через grace появляются offline expected sensors. Это корректный результат
отсутствия первого сигнала, не случайная тревога UI. ML score не подделывается.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/runtime -p 'test_*.py' -v
node --check src/interface/web/app.js
node --test tests/interface/*.cjs
node --test tests/interface.test.js
```

tests/runtime проверяет реальный backend и SQLite. Старые tests/interface/*.cjs
проверяют прежние mock/provider модули; tests/interface.test.js — текущий UI.
Старый QA-runner не объявляется полной проверкой v2: [QA_AUTORUN](QA_AUTORUN.md).
Результаты каждого этапа указываются в [RELEASE](RELEASE.md), автоматизация — GitHub Actions.

## Показ и финальная приёмка

Сначала карта и проверенный цикл Event → Incident → claim → response → restoration
→ close, затем transfer/expiry/reserve и три клиента. После подключения второго
исполнителя — D1–D10 с живым источником, артефактом и реальным tools-анализом.
Offline означает заранее установленный комплект; фактический холодный запуск и
нагрузку целевого ноутбука записывает Лия. Любой непройденный пункт — NOT RUN.
