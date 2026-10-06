# Запуск и повторение демо

**Сейчас здесь только структура и инструкции.** requirements.txt, перечисленные .py, данные и обученный артефакт ещё должны создать владельцы. Команды ниже — целевой интерфейс: автор реализует его либо меняет этот файл в том же PR. Нельзя ставить «проверено» до повторения другим участником.

## Подготовка сегодня

1. Владелец GitHub: Settings → Collaborators → добавить четыре GitHub-ника; участники принимают приглашения.
2. Каждый ставит Git и Python 3.11+; проверяет git --version и python --version.
3. Клонирует:

~~~powershell
git clone https://github.com/Military05/Hackathon.git
cd Hackathon
git remote -v
~~~

4. Читает START_HERE и свою роль. Имя/почта Git задаются личные, не чужие.
5. Личная ветка → commit → push → Pull request с нужной base; инструкции в TEAM_WORKFLOW и ролях.
6. Владелец включает правило main: PR и хотя бы один review; при наличии реализованного CI добавляет обязательную проверку. Сейчас автоматические проверки не созданы.

## Целевой базовый запуск — A1

A1 создаёт и проверяет requirements.txt с закреплёнными совместимыми версиями fastapi,uvicorn,httpx,numpy,scikit-learn,joblib,pytest. Не пишем выдуманные версии. Не добавляем Node/Docker без необходимости.

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m src.core.main --init-demo
.\.venv\Scripts\python.exe -m uvicorn src.core.main:app --host 127.0.0.1 --port 8000
~~~

--init-demo создаёт runtime базу из site.json, повторный запуск не удаляет историю. Отдельный явно описанный --reset-demo допускается только для demo-базы; QA проверяет его область. Сервер один worker, scheduler запускается один раз. Не нужен PowerShell Activate: вызываем venv Python напрямую.

Открыть http://127.0.0.1:8000, проверить /api/health. Остановка Ctrl+C. Состояние сохраняется в runtime/demo.sqlite, путь документирует A1.

## Целевой симулятор — B2, второе окно терминала

~~~powershell
.\.venv\Scripts\python.exe -m src.simulator.run --scenario normal --seed 42 --base-url http://127.0.0.1:8000
~~~

Сценарии: normal,forbidden-zone,unauthorized-access,sensor-offline,unusual-movement. Каждый имеет seed, уникальные event ids и печатает краткие ожидаемые изменения. Повторный run получает новый run_id, чтобы не столкнуться с event-idempotency. Отдельный режим replay сохраняет id для проверки дублей. Сценарий offline сначала посылает heartbeat, затем замолкает. Все события demo=true.

## Целевое обучение и оценка — B2

~~~powershell
.\.venv\Scripts\python.exe -m src.ml.generate --seed 42 --output data/generated
.\.venv\Scripts\python.exe -m src.ml.train --input data/generated --output artifacts/local
.\.venv\Scripts\python.exe -m src.ml.evaluate --input data/generated --model artifacts/local/movement.joblib
~~~

Файлы split metadata и test report сохраняются; README и ML_REPORT фиксируют фактические параметры. Обучение отдельной командой, не во время обработки событий. Если CLI отличается, B2 обновляет эту страницу до передачи QA.

Артефакт joblib загружаем только от своей команды. Для повторяемости фиксируем hash и версии библиотек; не загружаем случайные чужие pickle/joblib файлы. Сервер после перезапуска читает согласованный путь артефакта.

## Локальная LLM — B1

Это дополнительный компонент. Без него базовая карта и правила работают. На машине заранее проверить RAM/VRAM и свободное место; скорость реального tool calling измерить. Кандидат qwen3:4b, не гарантированное решение для любого компьютера.

Установить Ollama по официальной инструкции. Веса могут занимать несколько ГБ; на машине владельца предпочесть E:. В Windows завершить Ollama в трее, добавить пользовательскую переменную OLLAMA_MODELS со значением E:\HackathonModels, добавить OLLAMA_NO_CLOUD=1 для локального режима, затем заново запустить Ollama. Папку создать на существующем диске с местом. Не менять путь системного HOME. Инструкция: [Ollama FAQ](https://docs.ollama.com/faq).

~~~powershell
ollama pull qwen3:4b
ollama list
~~~

Ollama должен слушать локальный http://127.0.0.1:11434. B1 реализует конфигурацию OLLAMA_BASE_URL и OLLAMA_MODEL (точные имена фиксировать в коде и .env.example). На одном demo-компьютере внешний ключ не требуется. Не открывать сервис в интернет ради хакатона.

Перед интеграцией: реальный запрос с tools → фактический tool call → исполнение кода → ответ с evidence. Сохраняем время и model tag в TEAM. Если модель не тянет, пробуем меньший совместимый вариант и повторяем тот же тест; не выдаём за агентность заранее подготовленное объяснение.

## Целевые проверки — QA и авторы

~~~powershell
.\.venv\Scripts\python.exe -m pytest
~~~

QA также выполняет ручные сценарии из TESTING, проверяет отключение Ollama, неизвестную модель, отсутствие trained artifact и сетевую ошибку. Отчёт всегда привязан к git rev-parse HEAD.

## Ошибки

- Module not found / отсутствует requirements: сначала проверить реализован ли соответствующий PR; это руководство не установило приложение.
- Порт занят: остановить предыдущий свой сервер или согласовать новый порт и base-url симулятора.
- Пустая карта: /api/site, /api/assets, network ошибки; проверить одинаковые id.
- Агент unavailable: проверить Ollama, tag и реальный API; мониторинг не должен останавливаться.
- ML not_trained: выполнить обучение, проверить путь и версии; не подменять отсутствие модели значением normal.
- SQLite locked: убрать второй сервер/writer и длинные транзакции; не удалять базу как универсальное «исправление».

## Журнал воспроизводимости

| SHA | Машина / версии | Установка | D1–D3 | D4 | Agent tools | Проверил |
| --- | --- | --- | --- | --- | --- | --- |
| ещё не проверено | — | — | — | — | — | — |

Чистый клон на второй машине — обязательная проверка перед финальным тегом. Видео/скриншоты — резерв для защиты, подписанный записью, а не заменитель живого работающего результата.
