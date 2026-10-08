# Быстрый запуск текущей версии

Python 3.11+ и Git для базового режима; Python 3.12 для поставленных весов MLP.

```powershell
git clone https://github.com/Military05/Hackathon.git
cd Hackathon
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Откройте http://127.0.0.1:8000. Единственный администратор создаётся автоматически:
**admin / 123456768**. Создавать его вручную не нужно. Регистрация создаёт заявку;
admin подтверждает её и назначает dispatcher-1 (логистика), dispatcher-2
(производство) либо dispatcher-3 (КПП). Дополнительные администраторы запрещены.

Повторный запуск: только scripts/start.ps1. Одна SQLite data/runtime/dispatch.db
хранит аккаунты, историю и данные. DISPATCH_DB задаёт другой путь к базе.
Для трёх аккаунтов используйте разные браузерные профили/устройства.

MLP и Qwen: [AI_CONNECTION](AI_CONNECTION.md). Другой ноутбук с общими аккаунтами:
[REMOTE_CLIENT_V6](REMOTE_CLIENT_V6.md). Новая независимая установка получает того же
bootstrap admin, но не получает других пользователей и журнал из Git.

Текущие правила доступа, вкладки, архивирование и сценарии:
[ADMIN_WORKSPACES_V7](ADMIN_WORKSPACES_V7.md). События датчиков сохраняются для MLP,
даже после очистки видимых карточек. Эскалация и автоматический резерв отключены.
