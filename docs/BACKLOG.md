# Backlog v2 — все обязательные задачи

Список для создания GitHub Issues через Issues → New issue → «Задача команды». Документ не означает, что Issues созданы или задачи выполнены. Все строки сейчас TODO. [План](TASK_03_PLAN.md), [контракт](CONTRACTS.md), [координация](DISPATCH_OPERATIONS.md).

| ID | Исполнитель | Результат и критерий | Зависимости / base |
| --- | --- | --- | --- |
| T01 | все, Егор С. фиксирует | контракт v2, ID/site/sector/profile, wire примеры подтверждены | main |
| T02 | Егор С. | requirements, SQLite, init-demo без стирания, site.json с участками | T01 / main |
| T03 | Егор С. | Event валидация/idempotency, read API и query provider | T02 / main |
| T04 | Егор М. | SVG/site/позиции, возраст данных, три demo-профиля | T01; сначала fixture / main |
| T05 | Егор С. | D1–D3, независимые Incident, recovery, routing, unknown | T03 / main |
| T06 | Гриша | normal и D1–D3 simulator, ID/seed/run_id/demo=true | T01; API для прогона / main |
| T07 | Егор С. + Егор М. | claim с revision/request_id, один победитель, общий статус | T04/T05 / main |
| T08 | Гаджи | уже скачанная Local-модель: API → настоящий tools → результат, измерено время | независимо / prototype/agent-runtime |
| T09 | Лия + авторы | smoke C1: D1–D3, routing, три профиля, claim; тег beta-1 | T03–T07 / main |
| T10 | Егор С. | серверные presence/transfer, accept/cancel/expire, прежний владелец до accept | T07 / main, beta-2 |
| T11 | Егор С. | persisted timer: reminder/escalation/unavailable/active_review, нет дублей после restart | T10 / main, beta-2 |
| T12 | Егор М. | summary, уведомления, звук с разрешением, claim/transfer UI и incoming вне сектора | T10/T11; fixture до API / main, beta-2 |
| T13 | Лия | полная регрессия фиксированного beta-1, отчёт SHA и баги | сразу после T09 |
| T14 | Гаджи | tools registry, provider, аргументы и evidence validator | T01; fixture до query / feature/beta1-f1 |
| T15 | Гаджи | модельный адаптер Bionic/LM Studio, ограниченный loop, очередь/кэш/тайм-ауты/restart | T08/T14 / feature/beta1-f1 |
| T16 | Гаджи + Егор М. | общая панель анализа одного incident/snapshot, карта не ждёт LLM | T15/T04 / feature/beta1-f1 |
| T17 | Гриша | один extractor movement-v1, эпизоды/split без leakage | T01; независимо / feature/beta1-f1 |
| T18 | Гриша | train/evaluate MLP, Pipeline/порог/версии/hash, baseline и ML_REPORT | T17 / feature/beta1-f1 |
| T19 | Гриша + Егор С. + Егор М. | inference D4, состояния not_trained/insufficient_data, карточка оценки | T18 / feature/beta1-f1 |
| T20 | Гриша + Лия | D5–D10 fixtures/scenarios, ожидаемые IDs/сектора, воспроизводимый прогон | T05/T10/T11; ранние fixtures / main |
| T21 | Лия + авторы | QA v2: конкурентный claim, transfer границы, presence, escalation, UI/offline; расширить старый runner | T10–T12/T20 / main |
| T22 | все, оператор Гаджи | main → F1, v2 совместим, один зафиксированный кандидат | T12/T15/T19/T21 / общая F1 |
| T23 | Лия + авторы | совместный SHA D1–D10, LLM-off, restart, повтор PATCH, три клиента | T22 |
| T24 | Гриша + Гаджи + Егор С. | перенос MLP и весов LLM, offline зависимости/ресурсы, запуск ноутбука | T18/T15; после проверки оборудования |
| T25 | Егор М. | все CSS/JS/иконки локально, никаких CDN-загрузок в демо | T04/T12/T16 |
| T26 | Лия, второй участник | чистый клон/установка, интернет выключен, второй повторил RUNBOOK | T23–T25 |
| T27 | все, интегратор Егор С. | итоговый PR, проверенный merge, smoke main, RELEASE/тег | T23/T26 |

## Порядок и ответственность

После C1: T10–T12, T13 и T14–T19 выполняются параллельно. T20 готовит данные заранее. T21 — обязательное расширение автоматизации; старые шесть кейсов не подтверждают новый продукт. T24/T25 не оставляем на последний час.

У каждого Issue: критерий, base, личная ветка, разрешённые файлы, контракт v2, зависимость с конкретным человеком, команда/expected и полный SHA при передаче. Статусы TODO/IN PROGRESS/IN REVIEW/READY FOR QA/TESTING/DONE/BLOCKED. DONE только по результату, не по плану.

## Приоритет исправлений

P0: сервер/данные/основной показ не работают. P1: неверная маршрутизация, потеря ответственности, два победителя claim, скрытая тревога, обязательная передача/эскалация/ML/agent отсутствует. P2: неудобство с обходом. P3: оформление. Передача и эскалация не превращаются в P2 из-за нехватки времени.

Исправление основы → main → F1; один общий файл меняет один согласованный автор. За 3 часа до сдачи новые требования прекращаются, незакрытые обязательные функции остаются видимыми в RELEASE.

## Уточнения после аудита — обязательные задачи T28–T39

Полные форматы, условия и проверки: [PREBUILD_FIXES](PREBUILD_FIXES.md). Frontend-часть уже выполнена только там, где это указано в review; серверная часть не считается выполненной по mock.

| ID | Владельцы | Результат |
| --- | --- | --- |
| T28 | Егор С., Егор М., Гаджи | response_plan + record_response; первая реакция без LLM |
| T29 | Егор С., Гриша, Егор М. | condition_state, freshness, never_started grace, серверное время |
| T30 | Егор С., Гриша, Лия | access=passage_confirmed; отказ не объявляется проникновением |
| T31 | Егор С., Гриша, Лия | зона: вход сразу, выход после двух свежих измерений; шум |
| T32 | Егор М., Егор С., Лия | connection/availability отдельно; фоновые вкладки/profile change |
| T33 | Егор С., Егор М., Лия | доступный резерв координатора, гонка восстановления |
| T34 | Егор С., Егор М., Гриша, Лия | same-sector-burst, краткий звук, priority/состояние отдельно |
| T35 | Егор С., Гриша, Егор М., Лия | dismiss_model и сохранённый raw score, новый эпизод |
| T36 | Гаджи, Егор С., Лия | согласованный snapshot + проверка утверждений/health evidence |
| T37 | Гаджи, Егор С., Лия | 1 running + 2 queued, admission wait<120, независимость мониторинга |
| T38 | Гриша, Лия | штатные операции, независимый split/baseline, качество эпизодов |
| T39 | все, Лия проверяет | измерение latency, notebook/offline/cold start, D1–D10 + A1–A12 |
