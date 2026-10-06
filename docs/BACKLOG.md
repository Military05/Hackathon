# Backlog задания №3

Это план Issues. Создайте по строкам реальные задачи GitHub: назначенный автор, проверяемый результат, base ветка, зависимости. Все строки сейчас TODO.

| ID | Автор | Задача и критерий | Base / зависимость |
| --- | --- | --- | --- |
| T01 | A1 + все | согласовать Event/API v1, site ids и units | main, до реализации |
| T02 | A1 | init-demo, SQLite, POST events с validation/idempotency | main, T01 |
| T03 | A2 | SVG план по /api/site и позиции по /api/assets | main, T01; mock только временно |
| T04 | B2 | normal simulator, heartbeat, фиксированный seed | main, T01 |
| T05 | A1 | D1 зона, D2 доступ, D3 offline/recovery | main, T02 |
| T06 | A2 | журнал, evidence карточка, ошибки/health | main, T02/T05 |
| T07 | Q | кейсы и fixtures, smoke скелета | main, T02–T06 |
| T08 | B1 | ранняя проверка Ollama с настоящими tools, latency | личная ветка, независимо до beta-1 |
| T09 | Q + A1 | фиксировать beta-1 после smoke | tag проверенного main |
| T10 | Q | полная регрессия beta-1 и Issues | фиксированный SHA, после T09 |
| T11 | A1 | beta-2: edge cases, восстановление, стабильность API | main, после T09 |
| T12 | A2 | beta-2: состояния сети, UX, demo reset flow | main, после T09 |
| T13 | B2 | episode generator, movement-v1, split | feature/beta1-f1 |
| T14 | B2 | MLP train/evaluate + baseline + ML_REPORT | feature/beta1-f1, T13 |
| T15 | B2 + A1 | online inference, ModelObservation, D4 | feature/beta1-f1, T14 |
| T16 | B1 | tools registry, async jobs, bounded agent | feature/beta1-f1, T08 |
| T17 | B1 + A2 | analysis UI, evidence/trace, unavailable | feature/beta1-f1, T16 |
| T18 | все, оператор B1 | sync main → F1, исправить конфликты | общая feature |
| T19 | Q | D1–D4 + agent на совместном фиксированном SHA | интеграционный кандидат |
| T20 | все, оператор A1 | PR F1 → main merge commit, повторный smoke | T19 |
| T21 | Q + второй разработчик | чистый клон + RUNBOOK без автора | финальный main |
| T22 | все | финальный tag, демо/защита, фактические ограничения | T21 |

После beta-1 одновременно стартуют T10, T11/T12 и T13–T17. QA не ждёт beta-2 или F1, чтобы начать проверку.

Если T02–T07 задерживаются, full-stack помогают завершить обязательный путь. T08 проверяется рано: нельзя впервые узнать о непригодности модели за час до защиты.

Порядок очереди QA: P0 остановка/потеря данных → P1 обязательное неверное поведение → интеграционный кандидат → прочие улучшения. Исправление общей основы попадает в main и затем main вливается в F1. Не дублируем одинаковый fix двумя независимыми переписываниями.

Готовая Issue содержит PR, проверенный SHA, команды, наблюдаемый результат и ограничения. Список задач не является отчётом выполненных работ.
