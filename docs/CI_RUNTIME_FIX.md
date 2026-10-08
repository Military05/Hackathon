# Исправление проверок CI

Ветка: `codex/fix-ci-runtime-dependencies`.
Исходный main: `87129193ad49e12b694872e71109f662a5ad9919`.

## Зависимости

Workflow `Backend and interface` устанавливал только `requirements.txt`,
но обнаруживал все тесты `tests/runtime`, включая проверки интеграции AI.
`test_incident_log` импортирует `src.agent.backend`, который использует
`src.ml.features`; проверка восстановления Qwen в `test_launch` импортирует
`src.agent.runtime` и тот же backend. Импорт `numpy` не мог выполниться в CI.
Это ошибка состава окружения проверки, а не свидетельство неисправности
обученной модели или GPU.

Workflow теперь устанавливает `requirements-ai-dev.txt`: он включает базовые
зависимости, AI/ML-зависимости и pytest. Python согласован с целевой версией 3.12;
pip cache учитывает все три файла требований. Тесты не пропускаются, исключения
импорта не скрываются. Базовый `requirements.txt` и настройки запуска приложения
не меняются. CI не загружает веса Qwen и не подтверждает работу реального GPU.

## Отказ в доступе

Тест `test_three_cookie_sessions_keep_their_roles_despite_forged_profile_header`
ожидал HTTP 409 при попытке диспетчера 1 принять происшествие КПП,
подставив заголовок диспетчера 3. Но middleware берёт личность из cookie-сессии
и проверяет доступ к сектору до выполнения команды: правильный результат —
HTTP 403 `incident_sector_required`. HTTP 409 остаётся для настоящих конфликтов
состояния, версии карточки или смены аккаунта в другой вкладке.

Исправлено ожидание теста. Дополнительно проверяется неизменность владельца,
версии и истории после запрещённого запроса. Поддельный заголовок по-прежнему
не даёт прав; настоящий диспетчер КПП по-прежнему успешно принимает случай.
Авторизация, CSRF и код ответа сервера не изменены.

## Проверки

```powershell
python -m pip check
python -m unittest discover -s tests/runtime -p 'test_*.py' -v
python -m unittest discover -s tests/qa -p 'test_*.py' -v
python -m pytest tests/agent tests/ml -q
node --check src/interface/web/app.js
node --test tests/interface.test.js tests/map-view.test.cjs tests/sensors-panel.test.cjs tests/interface/*.cjs
```

Проверки SQLite выполняются на временных базах тестов. Производственная база
и сервер 8000 не изменяются; объединение в main выполняется отдельно по поручению.

Локальные результаты на Python 3.12.10: `pip check` — OK; runtime — 128 PASS;
agent/ML — 129 PASS; интерфейс — 91 PASS; синтаксис всех шести JavaScript-файлов
из workflow — OK. Итог GitHub Actions проверяется отдельно после push.
