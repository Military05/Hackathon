# Контракт v2 — задание №3, три диспетчера

> Обязательные уточнения после аудита: [PREBUILD_FIXES](PREBUILD_FIXES.md). Нормативное дополнение v2; при расхождении старого текста действуют эти уточнения. Наличие инструкции не подтверждает реализацию.

Обновлено 7 октября 2026. Этот документ — согласованная цель реализации, не описание уже работающего API. Операции координации, поля и примеры полностью определены в [DISPATCH_OPERATIONS](DISPATCH_OPERATIONS.md), разделах 5–9; они являются частью v2. Старый PATCH только со status несовместим с v2. Изменение поля обсуждаем с Егором С. (A1) / Егором М. (A2) / Гаджи (B1) / Гришей (B2) и Лией (QA — тестировщик) до кода; несовместимое изменение получает новую версию.

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

Егор С. (A1) создаёт data/demo/site.json: buildings (id, name, rectangle), zones (id, rectangle), assets (id, type), sensors (id, type, asset_id), permissions (asset_id/employee_id и allowed zone/building ids), site_areas, sectors, operator_profiles и dispatch_config по DISPATCH_OPERATIONS. Перечень идентификаторов должен совпадать у симулятора, API и карты. Начальные здания W1,W2,P1,P2,O1,G1; зоны Z1,Z2; машины V1–V3; сотрудники U1–U3. Датчики получают явные id в конфигурации.

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
| POST | /api/events | приём одного события |
| GET | /api/assets | текущие позиции и last_seen |
| GET | /api/sensors | здоровье датчиков |
| GET | /api/events?asset_id=...&since=...&until=...&limit=... | история, сортировка по времени |
| GET | /api/incidents?status=... | журнал |
| GET | /api/incidents/{id} | карточка + evidence |
| PATCH | /api/incidents/{id} | action, expected_revision, request_id; атомарное принятие/передача/закрытие; DISPATCH_OPERATIONS §8 |
| GET | /api/model-observations?asset_id=... | оценки MLP |
| POST | /api/incidents/{id}/analysis | X-Demo-Operator; 202 job_id,status; 503 unavailable; 429 queue_full |
| GET | /api/agent-jobs/{job_id} | queued/running/completed/failed + result/error |

История limit default=100,max=500; tools agent max=100. Даты since/until валидируем, since<=until. Ошибка JSON: code,message,details без traceback. Ошибка API не выглядит как пустой успешный список.

## Происшествие (Incident) и его состояния

Поля: incident_id,type,asset_id или employee_id,sensor_id при наличии,building_id/zone_id при наличии,severity,detected_at,status,condition_active,rule_version,evidence_event_ids,details,demo; дополнительно site_area_id,responsible_sector_id,assigned_operator_id,acknowledged_at,dispatch_revision,pending_transfer,escalation_level по DISPATCH_OPERATIONS.

type: forbidden_zone,unauthorized_access,sensor_offline,model_anomaly.
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
- get_asset_policy(asset_id): разрешения объекта или сотрудника и policy_version. U1–U3 также зарегистрированы в assets с type=employee.
- get_sensor_health(sensor_id): last_received_at, состояние и threshold.

Егор С. (A1) предоставляет query функции, Гаджи (B1) регистрирует схемы и валидирует arguments. Tools доступны только из явного registry. Идентификаторы должны существовать; временные диапазоны и количество ограничены. Никакого arbitrary SQL, shell или управления оборудованием.

AgentResult: summary,facts[],hypotheses[],recommendations[],evidence_event_ids[],tool_trace[],model_name,created_at,incident_snapshot.
Факты о событиях должны ссылаться на evidence, полученные tools; факты о допуске дополнительно ссылаются на policy_version из get_asset_policy; гипотезы отдельно и с уровнем уверенности/ограничениями. Версии правил/модели и набор evidence определяют snapshot; после изменения старый analysis помечаем stale.

Одна running job для всех профилей, максимум 2 queued, ожидание до 120 секунд, выполнение до 60 секунд; max 3 model requests и max 6 tool calls. Кэш по incident_id+snapshot общий; requested_by_operator_id хранится отдельно от assigned_operator_id. Поведение restart/timeout — DISPATCH_OPERATIONS §9. Финальный ответ без валидных evidence не публикуем как успешный анализ. tool_trace показывает функцию, допустимые аргументы, длительность и полученные id; секреты не пишем.

## Дополнения v2: обязательная координация

[DISPATCH_OPERATIONS](DISPATCH_OPERATIONS.md) нормативно определяет SiteArea/Sector/OperatorProfile/Presence/Notification/Transfer/DispatchHistory и API /api/operator-profiles, /api/operator-presence, /api/dispatch-summary, /api/site-areas/summary, /api/dispatch-notifications. Все профили/сектора/пороги в site.json; назначение автоматическое по месту, не по ответу ИИ.

GET /api/incidents принимает scope=workstation/all и site_area_id; workstation требует X-Demo-Operator и включает свой сектор плюс адресованные передачи/эскалации/назначенные случаи. Без scope старый общий список сохраняется для read-only QA. status — дополнительный фильтр. Новые read-only поля не ломают старый шестисценарный runner; его POST analysis нужно адаптировать к обязательному профилю отдельно.

DispatchHistory: history_id,incident_id,action,actor_operator_id (null для timer),from_operator_id,to_operator_id,reason,created_at,dispatch_revision,request_id. accepted transfer/claim/close и история — одна транзакция. Смена владельца не меняет sector и snapshot ML/agent. Одна новая позиция не создаёт новую dispatch revision без координационного изменения.

Конкурентная запись: expected_revision + идемпотентный request_id, один победитель claim. Передача сохраняет прежнего до accept; исчезнувший оператор не означает освобождение ownership. Просрочка и отсутствие реакции создают Notification, не Incident. Header профиля — demo-идентификация, не обещание аутентификации.

## Изменения

PR автора содержит пример request/response, миграцию если нужна и затронутые роли. Егор С. (A1) фиксирует версию; Егор М. (A2) / Гаджи (B1) / Гриша (B2) подтверждают потребителей; Лия (QA — тестировщик) обновляет сценарии. Не меняем общий контракт только в личной ветке.
