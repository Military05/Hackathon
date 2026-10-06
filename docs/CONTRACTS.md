# Контракт v1 — задание №3

Этот документ — согласованная цель реализации, не описание уже работающего API. Изменение поля обсуждаем с A1/A2/B1/B2 и QA до кода; несовместимое изменение получает новую версию.

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

HTTP JSON, prefix /api. UTC ISO 8601 с Z; event_time — время источника, received_at — время сервера. Все id — строки. demo=true во всех синтетических данных. Координаты x/y от 0 до 100 в условных единицах плана, не метры. Граница зоны включена.

A1 создаёт data/demo/site.json: buildings (id, name, rectangle), zones (id, rectangle), assets (id, type), sensors (id, type, asset_id), permissions (asset_id/employee_id и allowed zone/building ids). Перечень идентификаторов должен совпадать у симулятора, API и карты. Начальные здания W1,W2,P1,P2,O1,G1; зоны Z1,Z2; машины V1–V3; сотрудники U1–U3. Датчики получают явные id в конфигурации.

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
- access: payload employee_id,building_id,direction (in/out). Это наблюдение прохода; отсутствие разрешения проверяет правило.
- heartbeat: payload {}; состояние датчика связано с sensor_id.
- Duplicate: одинаковый event_id и нормализованный payload → 200 duplicate=true, повторных side effects нет; другой payload или другие поля события при том же id → 409.
- Новый Event → 201 с event_id,received_at,duplicate=false.
- Event append-only; более старое position сохраняется, но не откатывает текущую позицию. Сортировка истории event_time,event_id. Повторные времена не образуют скорость с делением на ноль.
- Текущее положение вычисляем по последнему event_time, при равенстве используем стабильный event_id порядок. Времена из будущего сверх согласованного допуска отклоняем; A1 фиксирует допуск и QA проверяет границу.

## Адреса операций сервера (HTTP)

| Метод | Путь | Результат |
| --- | --- | --- |
| GET | /api/health | rules, ml, agent, версии; без секретов |
| GET | /api/site | геометрия, объекты, конфигурация demo |
| POST | /api/events | приём одного события |
| GET | /api/assets | текущие позиции и last_seen |
| GET | /api/sensors | здоровье датчиков |
| GET | /api/events?asset_id=...&since=...&until=...&limit=... | история, сортировка по времени |
| GET | /api/incidents?status=... | журнал |
| GET | /api/incidents/{id} | карточка + evidence |
| PATCH | /api/incidents/{id} | только операторский status: acknowledged/closed; история изменения |
| GET | /api/model-observations?asset_id=... | оценки MLP |
| POST | /api/incidents/{id}/analysis | 202 с job_id,status; 503 если модель недоступна |
| GET | /api/agent-jobs/{job_id} | queued/running/completed/failed + result/error |

История limit default=100,max=500; tools agent max=100. Даты since/until валидируем, since<=until. Ошибка JSON: code,message,details без traceback. Ошибка API не выглядит как пустой успешный список.

## Происшествие (Incident) и его состояния

Поля: incident_id,type,asset_id или employee_id,sensor_id при наличии,building_id/zone_id при наличии,severity,detected_at,status,condition_active,rule_version,evidence_event_ids,details,demo.

type: forbidden_zone,unauthorized_access,sensor_offline,model_anomaly.
severity: info,warning,critical; demo defaults forbidden_zone/unauthorized_access=critical, sensor_offline/model_anomaly=warning. Это приоритет внимания диспетчера, не сертифицированная оценка риска.
status: open,acknowledged,closed. condition_active — отдельное поле: закрытие оператором не меняет факт нарушения.
Одна ongoing condition → одно происшествие. Выход из зоны/возврат heartbeat помечает восстановление и время, историю сохраняем. Новый вход после восстановления — новый случай. Unauthorized_access привязан к уникальному access event.
Offline: last_received_at,detected_at,threshold_seconds=5; timer раз в секунду, часы received_at. До первого heartbeat sensor=unknown, а не автоматически offline; после первого начинается отсчёт.
Model anomaly: reuse активного incident на тот же asset; закрытие условия после двух последовательных достаточных normal окон. insufficient_data не считается normal. Параметр сохраняем в конфигурации и отчёте.

## movement-v1 — единая функция вычисления признаков B2

Окно: последние 10 секунд event_time, шаг 5 секунд; минимум 6 уникальных position samples. Используем только положительные dt, сортируем, дубли времени обрабатываем стабильно. Окно с отсутствующими данными получает insufficient_data. Не пересчитываем опубликованный результат из-за позднего события в MVP: событие остаётся в истории; следующая оценка использует доступные данные. QA проверяет это поведение.

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

A1 предоставляет query функции, B1 регистрирует схемы и валидирует arguments. Tools доступны только из явного registry. Идентификаторы должны существовать; временные диапазоны и количество ограничены. Никакого arbitrary SQL, shell или управления оборудованием.

AgentResult: summary,facts[],hypotheses[],recommendations[],evidence_event_ids[],tool_trace[],model_name,created_at,incident_snapshot.
Факты о событиях должны ссылаться на evidence, полученные tools; факты о допуске дополнительно ссылаются на policy_version из get_asset_policy; гипотезы отдельно и с уровнем уверенности/ограничениями. Версии правил/модели и набор evidence определяют snapshot; после изменения старый analysis помечаем stale.

Один worker, max 3 model requests, max 6 tool calls, общий бюджет 60 секунд. Финальный ответ без валидных evidence не публикуем как успешный анализ. tool_trace показывает функцию, допустимые аргументы, длительность и полученные id; секреты не пишем.

## Изменения

PR автора содержит пример request/response, миграцию если нужна и затронутые роли. A1 фиксирует версию; A2/B1/B2 подтверждают потребителей; QA обновляет сценарии. Не меняем общий контракт только в личной ветке.
