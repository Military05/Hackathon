# AI в основном приложении

Основной и AI-режим используют один `src.core.main:app`, тот же интерфейс,
авторизацию и SQLite. Базовый запуск остаётся без AI/ML по умолчанию.

Установите Python 3.12 и зависимости AI, затем запустите Qwen и приложение:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -WithAI
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -WithAI -Database 'C:\path\existing-dispatch.db'
```

Для уже работающего приложения сначала завершите его сервер и укажите **его
существующую базу** абсолютным путём:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -WithAI -Port 8000 -Database 'C:\path\dispatch.db'
```

`start-ai.ps1` — совместимый вход в этот же запуск; порт по умолчанию теперь 8000.
Не запускайте второй сервер на занятом порту. Не создавайте новую базу вместо
базы с аккаунтами. AI-запуск отклоняет отсутствующий/относительный путь,
отсутствующую базу и базу без активного администратора. Для первичной настройки
сначала используйте обычный режим и штатный bootstrap. `-Database` имеет приоритет над `DISPATCH_DB`; AI `.env`
не подменяет выбранную базу. `.env` нужен для нестандартного провайдера/имени
модели; стандартное имя — `hackathon-qwen3-4b` на `127.0.0.1:1234/v1`.

Проверка: `/api/health` должен показывать `ml.status=ready`,
`agent.status=ready`, `auth.enabled=true`. В карточке происшествия доступна
кнопка «Проанализировать ИИ». Qwen анализирует сохранённые факты MLP и событий;
готовность не заменяет проверку завершённого задания. При отключении Qwen
мониторинг продолжает работать; готовность модели перепроверяется каждые
5 секунд. LAN требует TLS или проверенного зашифрованного туннеля.

## Подготовка переключения существующего сервера 8000

Подтверждённая рабочая директория: `C:\Windows\System32\Hackathon`.
Текущая база: `C:\Windows\System32\Hackathon\data\runtime\dispatch.db`.
Python установлен в `C:\Users\_ADMIN_\AppData\Local\Programs\Python\Python312`;
родительский процесс использует `.venv` основной копии. Это разные процессы,
а не противоречащие пути одного PID.

Резервная копия перед подготовкой:
`C:\Users\_ADMIN_\Hackathon\data\local\backups\contour-8000-20261008-before-ai.db`.
SHA256: `efbaf3f7ea5c943a9d2301ac5f6f1ab70020f49478ef2e1fc34b6c0aa8169f92`.
Она содержит 1 активного администратора, 0 активных диспетчеров,
17 448 событий и 7 происшествий. Диспетчеров надо создавать штатным способом
позже; интеграция не создаёт аккаунты и не меняет пароли.

Проверка на отдельной копии этой резервной базы (не на рабочей базе):

```powershell
$taskPython = 'C:\Users\_ADMIN_\Hackathon\.venv\Scripts\python.exe'
& $taskPython -X utf8 scripts/ai_prepare_check.py --backup 'C:\Users\_ADMIN_\Hackathon\data\local\backups\contour-8000-20261008-before-ai.db' --expected-sha256 efbaf3f7ea5c943a9d2301ac5f6f1ab70020f49478ef2e1fc34b6c0aa8169f92 --main-copy 'C:\Windows\System32\Hackathon'
& $taskPython -m unittest discover -s tests/runtime -p 'test_*.py' -v
& $taskPython -m unittest discover -s tests/qa -p 'test_*.py' -v
& $taskPython -m pytest tests/agent tests/ml -q
node --check src/interface/web/app.js
node --test tests/map-view.test.cjs tests/sensors-panel.test.cjs
node --test tests/interface/*.cjs
node --test tests/interface.test.js
```

`ai_prepare_check.py` не запускает HTTP listener и не обращается к рабочей базе.
Он создаёт отдельную проверочную копию через backup API, сопоставляет важные
файлы основной копии, проверяет готовность реального провайдера и неизменность
аккаунтов/истории. Такая проверка не является генерацией ответа Qwen.
MLP — scikit-learn Pipeline на CPU; CUDA-зависимости для неё не нужны.
Qwen3-4B Q4_K_M загружается через LM Studio с `--gpu max`, context 8192,
parallel 1 и идентификатором `hackathon-qwen3-4b`.

До подтверждения переключения серверы не перезапускать и main не объединять.
После подтверждения:

1. Повторно проверить PID/listener и сделать свежую резервную копию через
   Connection.backup(): рабочая база продолжает получать события.
2. Штатно завершить сервер 8000 через Ctrl+C в его административном терминале.
   Убедиться, что порт освобождён. Qwen API на 1234 не останавливать.
3. Запустить подготовленную ветку в PowerShell от администратора, сохранив базу:

```powershell
powershell -ExecutionPolicy Bypass -File 'C:\Users\_ADMIN_\Hackathon\.worktrees\ai-server-launch\scripts\start.ps1' -WithAI -Port 8000 -Database 'C:\Windows\System32\Hackathon\data\runtime\dispatch.db' -PythonExecutable 'C:\Users\_ADMIN_\Hackathon\.venv\Scripts\python.exe'
```

4. Проверить health: auth enabled/configured, ml/agent ready; войти существующим
   администратором; проверить сохранённые происшествия и завершённый AI-анализ.
   Количество событий не должно уменьшиться относительно момента переключения.
5. Только после успеха завершить лишний сервер 8001. Объединение main — отдельное
   действие координатора.

При неудаче штатно завершить новый сервер, выставить DISPATCH_ENABLE_AGENT=0,
DISPATCH_ENABLE_ML=0 и явный прежний DISPATCH_DB, затем запустить прежний uvicorn
из основной копии. Не восстанавливать старый backup поверх рабочей базы:
это удалило бы события, полученные после резервного копирования.
