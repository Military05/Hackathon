# Контракт v2 — задание №3, три диспетчера

> Текущие правила v7: [ADMIN_WORKSPACES_V7](ADMIN_WORKSPACES_V7.md). Единственный admin / 123456768 создаётся автоматически, не имеет роли диспетчера и не обрабатывает происшествия. Эскалация/резерв отключены; старые описания этих функций и bootstrap ниже относятся к прежним версиям.

> Обязательные уточнения после аудита: [PREBUILD_FIXES](PREBUILD_FIXES.md). Нормативное дополнение v2; при расхождении старого текста действуют эти уточнения. Наличие инструкции не подтверждает реализацию.

Обновлено 7 октября 2026. Базовые Event/Incident и координация реализованы сервером Егора С.; ML и агент остаются отдельной областью владельцев, наличие их спецификации не подтверждает подключение. Версия продукта v5 добавляет авторизацию и КПП по прямому поручению координатора: [PRODUCT_V5](PRODUCT_V5.md), [AUTH_SECURITY_V5](AUTH_SECURITY_V5.md), [GATE_SHIFT_V5](GATE_SHIFT_V5.md). Эти требования заменяют прежние исключения «без аккаунтов/смен». Публичный JSON Event и contract_version=2 сохранены. Операции координации определены в [DISPATCH_OPERATIONS](DISPATCH_OPERATIONS.md), разделах 5–9. Старый PATCH только со status несовместим с v2. Несовместимое изменение согласуют владельцы и оно получает новую версию.

## Как читать этот технический документ

Здесь мы договариваемся о точных названиях полей. Например, `event_id` нельзя заменить русским словом в коде: все модули должны использовать одно имя. Сначала прочитай пояснения, затем нужный формат. [Полный словарь](GLOSSARY.md).

| Название | Что означает |
| --- | --- |
| API / HTTP / JSON | Обмен с сервером / протокол обмена / формат данных |
| Event / event_id / event_time | Событие / его идентификатор / время у источника |
| received_at / sensor_id / payload | Время приёма сервером / идентификатор датчика / содержимое события |
| asset_id / employee_id / building_id / zone_id | Идентификатор объекта / сотрудника / здания / зоны |
| type / status / severity | Тип / состояние / важность |
| demo=true | Эти данные созданы для демонстрации |
| Incident / evidence_event_ids | Происшествие / список исходных событий, подтверждающих вывод |
| ModelObservation / score / threshold | Оценка нейросети / числовой результат / порог срабатывания |
| Tool / AgentResult / tool_trace | Инструмент агента / ответ агента / журнал его действий |
| snapshot / stale | Снимок данных / устаревший анализ |
| UTC / ISO 8601 | Мировое время / согласованный формат даты |
| idempotency / duplicate | Повтор не обрабатывается заново / повторное событие |

## Общие правила

HTTP JSON, prefix /api. UTC ISO 8601 с Z; event_time — время источника, received_at — время сервера. Все id — строки. demo=true во всех синтетических данных. Координаты x/y от 0 до 100 в условных единицах плана, не метры. Граница зоны включена; восстановление выхода подтверждается двумя последовательными свежими position по PREBUILD_FIXES.

Егор С. (A1) ведёт data/demo/site.json: buildings (id, name, rectangle), zones (id, rectangle, kind), assets (id, type), sensors (id, type, asset_id/building_id), permissions, site_areas, sectors, operator_profiles и dispatch_config. Перечень идентификаторов совпадает у источника, API и карты. Текущая карта: 18 объектов с сохранёнными W1/W2/P1/P2/O1/G1, машины V1–V3, сотрудники U1–U4, ACCESS-G1. Z1/Z2 имеют kind=permit, Z3/Z6 — forbidden, Z4/Z5/Z7 — restricted с restricted_vehicle_types. V6 дополняет site личными demo_routes/safety_routes, pedestrian_paths и demo_safety_scenarios. sectors.focus_bounds задаёт наведение камеры, а не маршрутизацию. shift_employees задаёт ожидаемых зарегистрированных сотрудников с допуском G1. [FACTORY_V6](FACTORY_V6.md).

## Доступ к HTTP в продукте v5

Авторизация включена по умолчанию. Без сессии доступны /api/health,
/api/auth/status и формы POST /api/auth/login, /api/auth/register. Остальные
операторские чтения требуют активной серверной сессии, изменения — также
X-CSRF-Token и допустимый Origin. Middleware подставляет профиль сессии вместо
X-Demo-Operator; URL operator не определяет личность. X-Expected-User с user.id
на чтении/записи/CSV выявляет общую cookie, изменённую другой вкладкой:
409 session_identity_changed. Он не является заменой сессии.

Внешний POST /api/events требует отдельный X-Source-Key, а не аккаунт
диспетчера. Без настройки DISPATCH_SOURCE_KEY (от 24 символов) — 503,
неправильный ключ — 401. Внутренний источник вызывает Service напрямую.
Удалённый HTTP отклоняется, нужен HTTPS/проверенный защищённый туннель.
Полный контракт аккаунтов/сессии и безопасного запуска — AUTH_SECURITY_V5.
DISPATCH_ENABLE_AUTH=0 разрешён только для изолированных localhost-тестов.

## Событие (Event)

Обязательные поля: event_id, event_time, sensor_id, type, demo, payload. received_at добавляет сервер. Неизвестный sensor/asset/building: 404; неверная структура/координаты/время: 422.

~~~json
{
  "event_id": "demo-position-0001",
  "event_time": "2026-10-07T09:00:00Z",
  "sensor_id": "POS-V1",
  "type": "position",
  "demo": true,
  "payload": {"asset_id": "V1", "x": 12.0, "y": 24.0}
}
~~~

- position: payload asset_id,x,y; датчик соответствует asset_id.
- access: payload employee_id,building_id,direction (in/out). Это подтверждённое наблюдение прохода (passage_confirmed), не запрос карточки и не отказ; отсутствие разрешения проверяет правило.
- heartbeat: payload {}; состояние датчика связано с sensor_id.
- Duplicate: одинаковый event_id и нормализованный payload → 200 duplicate=true, повторных side effects нет; другой payload или другие поля события при том же id → 409.
- Новый Event → 201 с event_id,received_at,duplicate=false.
- Event append-only; более старое position сохраняется, но не откатывает текущую позицию. Сортировка истории event_time,event_id. Повторные времена не образуют скорость с делением на ноль.
- Текущее положение вычисляем по последнему event_time, при равенстве используем стабильный event_id порядок. Времена из будущего сверх согласованного допуска отклоняем; Егор С. (A1) фиксирует допуск и Лия (QA — тестировщик) проверяет границу.

## Адреса операций сервера (HTTP)

| Метод | Путь | Результат |
| --- | --- | --- |
| GET | /api/health | contract_version=2, rules, ml, agent, версии; без секретов |
| GET | /api/site | геометрия, объекты, конфигурация demo |
| POST | /api/events | приём одного события, внешний X-Source-Key |
| GET | /api/assets | текущие позиции и last_seen |
| GET | /api/sensors | здоровье датчиков |
| GET | /api/events?asset_id=...&since=...&until=...&limit=... | история, сортировка по времени |
| GET | /api/incidents?status=... | журнал |
| GET | /api/incidents/{id} | карточка + evidence |
| PATCH | /api/incidents/{id} | action, expected_revision, request_id; атомарное принятие/передача/закрытие; DISPATCH_OPERATIONS §8 |
| GET | /api/model-observations?asset_id=... | оценки MLP |
| POST | /api/incidents/{id}/analysis | сессия/CSRF; профиль подставляет сервер; подключённый модуль: 202 job_id,status; без него 503 unavailable |
| GET | /api/agent-jobs/{job_id} | queued/running/completed/failed + result/error |
| GET | /api/checkpoint/journal | фильтры q,direction,permission,since,until; limit default50/max200,offset |
| GET | /api/checkpoint/export.csv | те же фильтры, limit default200/max200, UTF-8 BOM, разделитель ;, русские колонки |
| GET | /api/shifts/current | последняя смена или null |
| GET | /api/operator-activity | успешные действия текущего профиля |
| GET | /api/dispatch-history/export.csv | scope=mine default,limit default1000/max2000; scope=all только admin; CSV v6 русские колонки/значения, время МСК |

История limit default=100,max=500; tools agent max=100. Даты since/until валидируем, since<=until. Ошибка JSON: code,message,details без traceback. Ошибка API не выглядит как пустой успешный список.

## Происшествие (Incident) и его состояния

Поля: incident_id,type,asset_id или employee_id,sensor_id при наличии,building_id/zone_id при наличии,severity,detected_at,status,condition_active,rule_version,evidence_event_ids,details,demo; дополнительно site_area_id,responsible_sector_id,assigned_operator_id,acknowledged_at,dispatch_revision,pending_transfer,escalation_level по DISPATCH_OPERATIONS. can_claim — вычисляемое read-only поле для текущего профиля: свой сектор либо адресованное уведомление резерва/эскалации, рабочий и ещё не принятый случай. Оно не хранится в Event и не заменяет проверку PATCH.

type: forbidden_zone,unauthorized_access,sensor_offline,model_anomaly,
route_deviation,collision (дополнение v2.2, Event не меняется).
collision дополнительно содержит other_asset_id, details.asset_ids, distance_units,
threshold_units,detection_mode и evidence обоих объектов; severity=critical.
route_deviation severity=warning, details.route_id,distance_units,threshold_units.
Правила/свежесть/восстановление — FACTORY_V6. Координаты условные; collision не
прогнозирует реальную аварию. Старые analysis после rules-v2.2-safety устаревают.
severity: info,warning,critical; demo defaults forbidden_zone/unauthorized_access=critical, sensor_offline/model_anomaly=warning. Это приоритет внимания диспетчера, не сертифицированная оценка риска.
status: open,acknowledged,closed. claim атомарно назначает оператора и acknowledged. close допускается только назначенным оператором при condition_active=false, condition_state!=unknown и без pending transfer; иначе 409. Для разового unauthorized_access condition_active=false с создания, но обработка остаётся open. У зоны/heartbeat/ML восстановление определяется детектором.
Одна ongoing condition → одно происшествие. Выход из зоны/возврат heartbeat помечает восстановление и время, историю сохраняем. Новый вход после восстановления — новый случай. Unauthorized_access привязан к уникальному access event.
Offline: last_received_at,detected_at,threshold_seconds=5; timer раз в секунду, часы received_at. До первого heartbeat sensor=unknown в пределах startup_sensor_grace_seconds=10. Если ожидаемый источник так и не начал передачу, создаётся sensor_offline с details.cause=never_started и last_received_at=null; это не потеря ранее существовавшей связи. После первого сигнала начинается обычный отсчёт.
Model anomaly: reuse активного incident на тот же asset; закрытие условия после двух последовательных достаточных normal окон. insufficient_data не считается normal. Параметр сохраняем в конфигурации и отчёте.

## movement-v1 — единая функция вычисления признаков Гриша (B2)

Окно: последние 10 секунд event_time, шаг 5 секунд; минимум 6 уникальных position samples. Используем только положительные dt, сортируем, дубли времени обрабатываем стабильно. Окно с отсутствующими данными получает insufficient_data. Не пересчитываем опубликованный результат из-за позднего события в MVP: событие остаётся в истории; следующая оценка использует доступные данные. Лия (QA — тестировщик) проверяет это поведение.

Соседние точки: distance=sqrt(dx²+dy²), speed=distance/dt; stationary speed<=0.05 условных единиц/сек. Семь features в строгом порядке:

| Feature | Определение / единица |
| --- | --- |
| speed_mean | среднее segment speed, единицы/сек |
| speed_std | стандартное отклонение speed, ddof=0 |
| idle_ratio | число stationary segments / все segments |
| path_length | сумма segment distances, единицы |
| max_step_distance | максимум segment distance, единицы |
| stop_start_count | число смен stationary ↔ moving между segments |
| mean_direction_change | средний угол 0..π радиан между соседними ненулевыми перемещениями; нет пары → 0 |

Никаких label,scenario,id,готового правила нарушения среди features. Сохраняем Pipeline scaler+MLP, class order,threshold,feature_version,model_version,split metadata. Score положительного класса anomaly не объявляем калиброванной вероятностью аварии.

ModelObservation: observation_id,asset_id,window_start,window_end,status,score (null при отсутствии результата),threshold,model_version,feature_version,evidence_event_ids,demo.

## Инструменты (tools) локального агента

- get_incident(incident_id): карточка с evidence.
- get_event_history(asset_id,since,until,limit): максимум 100 событий.
- get_asset_policy(asset_id): разрешения объекта или сотрудника и policy_version.
  V6 также отдаёт vehicle_type, forbidden_zone_ids, personal_route_id и
  personal_route_points (если назначены). Красные restricted зоны запрещают
  указанные restricted_vehicle_types, forbidden запрещает всем независимо от допуска.
  U1–U3 также зарегистрированы в assets с type=employee.
- get_sensor_health(sensor_id): last_received_at, состояние и threshold.

Егор С. (A1) предоставляет query функции, Гаджи (B1) регистрирует схемы и валидирует arguments. Tools доступны только из явного registry. Идентификаторы должны существовать; временные диапазоны и количество ограничены. Никакого arbitrary SQL, shell или управления оборудованием.

AgentResult: summary,facts[],hypotheses[],recommendations[],evidence_event_ids[],tool_trace[],model_name,created_at,incident_snapshot.
Факты о событиях должны ссылаться на evidence, полученные tools; факты о допуске дополнительно ссылаются на policy_version из get_asset_policy; гипотезы отдельно и с уровнем уверенности/ограничениями. Версии правил/модели и набор evidence определяют snapshot; после изменения старый analysis помечаем stale.

Одна running job для всех профилей, максимум 2 queued, ожидание до 120 секунд, выполнение до 60 секунд; max 3 model requests и max 6 tool calls. Кэш по incident_id+snapshot общий; requested_by_operator_id хранится отдельно от assigned_operator_id. Поведение restart/timeout — DISPATCH_OPERATIONS §9. Финальный ответ без валидных evidence не публикуем как успешный анализ. tool_trace показывает функцию, допустимые аргументы, длительность и полученные id; секреты не пишем.

## Дополнения v2: обязательная координация

[DISPATCH_OPERATIONS](DISPATCH_OPERATIONS.md) нормативно определяет SiteArea/Sector/OperatorProfile/Presence/Notification/Transfer/DispatchHistory и API /api/operator-profiles, /api/operator-presence, /api/dispatch-summary, /api/site-areas/summary, /api/dispatch-notifications. Все профили/сектора/пороги в site.json; назначение автоматическое по месту, не по ответу ИИ.

GET /api/incidents принимает scope=workstation/all и site_area_id; профиль берётся из сессии. workstation включает свой сектор плюс адресованные передачи/эскалации/назначенные случаи. Без scope общий список сохраняется для авторизованного обзора; status — дополнительный фильтр. Старый шестисценарный runner требует HTTP-сессию/CSRF для операторских запросов или явно изолированный тестовый режим, не открытый рабочий сервер.

DispatchHistory: history_id,incident_id,action,actor_operator_id (null для timer),from_operator_id,to_operator_id,reason,created_at,dispatch_revision,request_id. accepted transfer/claim/close и история — одна транзакция. Смена владельца не меняет sector и snapshot ML/agent. Одна новая позиция не создаёт новую dispatch revision без координационного изменения.

Конкурентная запись: expected_revision + идемпотентный request_id, один победитель claim. Передача сохраняет прежнего до accept; исчезнувший оператор не означает освобождение ownership. Просрочка и отсутствие реакции создают Notification, не Incident. В рабочей v5 личность обеспечивает серверная сессия; внутренний demo header — совместимость существующих роутеров, не доверенный пользовательский ввод.

## КПП и смена v5 без изменения Event

ACCESS-G1 сообщает подтверждённые проходы G1, обычный разрешённый проход
сохраняется без Incident, U4 без допуска создаёт unauthorized_access.
Сценарий shift ждёт уникальные разрешённые входы U1/U2/U3 перед фазой transport.
Service.ingest_event(event, shift_id=...) используется только внутренним
источником: событие и корреляция сохраняются в одной транзакции, публичный
payload не получает shift_id. Heartbeat не является проверкой человека.
Журнал/фильтры, observed-only occupancy, поля shift и ограниченный CSV описаны
в GATE_SHIFT_V5. Обычный источник — 3 position + 3 heartbeat в секунду.

## Изменения

PR автора содержит пример request/response, миграцию если нужна и затронутые роли. Егор С. (A1) фиксирует версию; Егор М. (A2) / Гаджи (B1) / Гриша (B2) подтверждают потребителей; Лия (QA — тестировщик) обновляет сценарии. Не меняем общий контракт только в личной ветке.
