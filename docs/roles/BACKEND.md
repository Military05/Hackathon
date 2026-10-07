# Егор С. — сервер, аккаунты, SQLite и КПП

Текущая базовая поставка v5 включает вход/регистрацию и начало смены по прямому
поручению координатора. Прежнее исключение «без аккаунтов/смен» больше не
применяется. JSON Event v2 сохранён; ML, агент и внешний симулятор принадлежат
Грише/Гаджи и не меняются в этой задаче.

Сначала прочитайте [PRODUCT_V5](../PRODUCT_V5.md), [CONTRACTS](../CONTRACTS.md),
[DISPATCH_OPERATIONS](../DISPATCH_OPERATIONS.md), [AUTH_SECURITY_V5](../AUTH_SECURITY_V5.md),
[GATE_SHIFT_V5](../GATE_SHIFT_V5.md), [RELEASE](../RELEASE.md) и корневой AGENTS.md.

## Область ответственности

`src/core`, `src/storage`, `data/demo/site.json`, requirements и серверные скрипты.
`demo_traffic.py` — согласованный встроенный источник core, отдельный от
`src/simulator`. `auth.py` хранит аккаунты/сессии, `operations.py` проецирует
журнал КПП/смены/действий из реальных сохранённых событий.

## Что сохранять при доработке

1. Авторизация включена по умолчанию. Регистрация принимает только username,
   name, password и создаёт pending. Профиль назначает admin; роль admin создаёт CLI.
2. Пароли PBKDF2, сессии в SQLite, cookie HttpOnly/SameSite, CSRF и Origin для
   изменяющих запросов. Личность берётся из сессии, X-Demo-Operator заменяется
   сервером; X-Expected-User защищает старую вкладку при общей cookie.
3. Без правильного X-Source-Key внешний HTTP POST событий не принимается.
   Внутренний Service.ingest_event остаётся вызовом Python, не авторизованным
   HTTP-клиентом. Удалённый транспорт требует HTTPS/проверенного туннеля.
4. Три профиля: логистика, производство, КПП. coordination ID и резерв/unknown
   сохраняются; dispatcher-3 не получает безусловное право claim чужого сектора.
   Read-only can_claim отражает проверку сервера и не выдаёт новые права.
5. Событие, правила, Incident и корреляция смены записываются атомарно. Повтор
   event_id не дублирует проход/тревогу. Публичный Event не получает shift_id.
6. shift сначала ждёт уникальные разрешённые входы U1/U2/U3 через G1; только
   затем transport. Во время ожидания позиции/heartbeat продолжают поступать.
7. Journal API ограничивает страницу КПП 200; CSV действий default1000/max2000.
   scope=all требует admin. Начальная численность неизвестна; occupancy —
   наблюдение по журналу, не реестр всего завода.
8. Claim/revision/request_id, owner до accept, таймаут передачи, recovery,
   напоминания и эскалация сохраняют историю и ограничения Service.
9. Один worker, короткие SQLite-транзакции, общий timer. PBKDF2 и долгие модельные
   вычисления не удерживают event loop или транзакцию приёма событий.

## Согласование с командой

Егор М.: JSON сессии, CSRF/Expected-User, can_claim, Operations/CSV и ошибки.
Гриша: общий site.json, ACCESS-G1 и ключ HTTP-источника, без изменения JSON Event.
Гаджи: сессия/CSRF HTTP-клиента, query функции, snapshot/таймауты; его файлы
не переписываются автоматически. Лия: три независимых входа, гонки, отказ
источника КПП, restart и замеры на целевых ноутбуках.

Не объявляйте модуль подключённым по одному env-флагу. Agent/ML status unavailable
сохраняется до фактической поставки, без выдуманных оценок и ответов.

## Проверка и передача

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/runtime -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/qa -p 'test_*.py' -v
git status
git rev-parse HEAD
```

Бизнес-тесты могут отключать auth в create_app для проверки правил отдельно;
test_auth проверяет включённую защиту. Итоговый отчёт содержит полный SHA,
фактические команды, PASS/FAIL/NOT RUN, неизвестные зависимости и ограничения.
Личные изменения — отдельный checkout и ветка codex/…; в staging только
согласованные файлы. Final main объединяет координатор по поручению, без force push.
