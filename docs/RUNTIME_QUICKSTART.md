# Запуск версии v5

Авторизация включена по умолчанию. Движение и правила работают без ML/ИИ.
Подробная инструкция для первого знакомства: [PRODUCT_V5](PRODUCT_V5.md).

## Первый запуск Windows

Python 3.11+ и Git; зависимости устанавливаются заранее с интернетом.

```powershell
git clone https://github.com/Military05/Hackathon.git
cd Hackathon
git rev-parse HEAD
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
.\.venv\Scripts\python.exe scripts/manage_users.py create-admin
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Откройте `http://127.0.0.1:8000`. Создайте собственный пароль 15–128 символов;
готовых учётных данных нет. Администратор входит, открывает «Администрирование»,
подтверждает заявки и назначает dispatcher-1/2/3. Заявка не даёт прав автоматически.
Для повторного запуска установленного проекта повторяется только `start.ps1`.

Один server worker и одна SQLite `data/runtime/dispatch.db`; аккаунты и история
сохраняются после restart. На другом порту:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Port 8001
```

Для отдельного учебного прогона назначьте новую базу, затем создайте администратора
в ней. Не удаляйте рабочую историю и не перепутайте базу CLI с базой сервера.

```powershell
$env:DISPATCH_DB='data/runtime/my-v5-qa.db'
.\.venv\Scripts\python.exe scripts/manage_users.py create-admin
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Список аккаунтов: `scripts/manage_users.py list`. Команда `create-dispatcher`
создаёт локально проверенный активный аккаунт с указанным профилем; основной
путь участников — регистрация и подтверждение в админ-панели.

## Рабочие места

1 — логистика/склады; 2 — производство; 3 — КПП. Используйте отдельные браузерные
профили/браузеры/компьютеры. Вкладки одного профиля разделяют cookie, поэтому
изменение `?operator=…` не создаёт другое рабочее место с другим входом.

«Штатная работа» включается автоматически. Панель рядом с журналом КПП меняет один общий
сценарий для всех клиентов. «Начало смены» сначала подтверждает проходы U1/U2/U3
через G1, затем включает транспорт в той же смене. «Остановить» прекращает
пакеты; после порога датчики становятся offline. Ручная проверка читает серверное
состояние и не выдумывает heartbeat.

## Переменные запуска

| Переменная | Назначение |
| --- | --- |
| DISPATCH_DB | Путь к общей SQLite |
| DEMO_AUTOSTART=0 | Сценарий запускается вручную кнопкой |
| DISPATCH_ENABLE_DEMO_TRAFFIC=0 | Отключить встроенный источник для внешних/ручных Event |
| DISPATCH_SOURCE_KEY | Случайный секрет от 24 символов для внешнего POST событий |
| DISPATCH_ENABLE_AUTH=0 | Только изолированные localhost-тесты; запрещён рабочий/LAN-показ |
| DISPATCH_ENABLE_SIMULATOR=1 | Подключить фактически переданный внешний источник Гриши |
| DISPATCH_ENABLE_ML=1 / DISPATCH_ENABLE_AGENT=1 | Только после поставки и проверки владельцев |

Не сохраняйте source key в Git, frontend, site.json или скриншоты. Встроенный
источник вызывает Service напрямую и не требует ключа; внешнему HTTP-источнику
нужен `X-Source-Key`. Публичный JSON Event остаётся прежним.

## Другой компьютер

Обычный удалённый HTTP отклоняется. Владелец заранее обеспечивает сертификат,
которому доверяют клиентские браузеры, с нужным адресом сервера:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Lan -TlsCert 'E:\certs\factory.crt' -TlsKey 'E:\certs\factory.key'
```

Участники открывают `https://адрес-сервера:8000`. Ключ сертификата не коммитится.
Альтернатива — реально проверенный шифрующий туннель с
`DISPATCH_TRUST_ENCRYPTED_TUNNEL=1`; переменная не создаёт туннель и сама ничего
не шифрует. Скрипт не настраивает маршрутизатор или Windows Firewall. Прямой
запуск Uvicorn: один worker, `--no-proxy-headers`.
Доверенный TLS и внешнее подключение проверяются отдельно — **NOT RUN** здесь.

## Проверка

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/runtime -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/qa -p 'test_*.py' -v
node --check src/interface/web/app.js
node --test tests/interface.test.js tests/map-view.test.cjs tests/sensors-panel.test.cjs tests/interface/*.cjs
```

Node нужен для проверок, приложению он не требуется. Тесты бизнес-правил могут
создавать `create_app(enable_auth=False)`; тесты защиты используют включённую
авторизацию. Обе области проверяются отдельно. Финальные результаты —
[RELEASE](RELEASE.md), а не наличие этих команд в инструкции.
