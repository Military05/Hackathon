# Запуск текущего сервера

В текущей поставке реализуются задачи Егора С./М. Симулятор, ML и агент передаются
отдельным исполнителем. Начинать работу можно с опубликованных сервера, карты и
контрактов; движение появится после подключения генератора событий.

Python 3.11+ и Git. Зависимости устанавливаются заранее при доступном интернете:

```powershell
git clone https://github.com/Military05/Hackathon.git
cd Hackathon
git rev-parse HEAD
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Откройте http://127.0.0.1:8000. Один FastAPI worker и одна SQLite
data/runtime/dispatch.db. История сохраняется после restart. Папка игнорируется Git.
Для чистого теста задайте DISPATCH_DB другому файлу, не удаляйте чужую историю.

```powershell
$env:DISPATCH_DB='data/runtime/my-qa.db'
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Port 8001
```

Для других ноутбуков в доверенной комнате используйте -Lan и адрес компьютера
сервера. Windows Firewall и доступ к порту проверяются владельцем на месте.
Три вкладки используют один сервер и разные ?operator=dispatcher-1/2/3.
Demo-профили не являются авторизацией для публичного интернета.

Без источника событий транспорт не анимируется; через grace=10 секунд ожидаемые
датчики дают sensor_offline. Это фактическое отсутствие сигнала, а не ошибка карты.
События можно отправлять через POST /api/events; интерактивная схема API — /docs.
Правила и диспетчерские операции работают независимо от ML/LLM.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/runtime -p 'test_*.py' -v
node --check src/interface/web/app.js
node --test tests/interface/*.cjs
```

Node используется для проверки интерфейса; приложению он не нужен.
Подключение модулей второго агента: [DEVELOPMENT_STAGES](DEVELOPMENT_STAGES.md).
Включение env flags само по себе не устанавливает модуль или модель.
Результаты проверок и ограничения: [RELEASE](RELEASE.md), [PERFORMANCE](PERFORMANCE.md).
Подробные роли и алгоритмы: [TEAM_ROOM_PLAYBOOK](TEAM_ROOM_PLAYBOOK.md),
[CHATGPT_GITHUB_GUIDE](CHATGPT_GITHUB_GUIDE.md).
