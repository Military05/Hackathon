"""Текст для оператора из сохранённого контекста и проверенных фактов."""
import math

TITLES = {'forbidden_zone': 'Въезд в зону с ограничением',
          'model_anomaly': 'Необычное движение', 'sensor_offline': 'Нет сигнала датчика',
          'unauthorized_access': 'Проход без допуска', 'route_deviation': 'Отклонение от маршрута',
          'collision': 'Сближение транспорта'}


def present(session, facts):
    snapshot = session.snapshot
    incident = snapshot.data['incident']
    context = snapshot.data.get('display_context', {})
    context = context if isinstance(context, dict) else {}
    kind = incident.get('type')

    def name(group, identifier, fallback):
        mapping = context.get(group)
        value = mapping.get(identifier) if isinstance(mapping, dict) else None
        return value if isinstance(value, str) and value.strip() else fallback

    entity = name('assets', incident.get('asset_id') or incident.get('employee_id'), 'Объект не определён')
    place = name('zones', incident.get('zone_id'), '') or name('buildings', incident.get('building_id'), '') or name('site_areas', incident.get('site_area_id'), 'Место не определено')
    place_kind = ('zone' if name('zones', incident.get('zone_id'), '') else
                  'building' if name('buildings', incident.get('building_id'), '') else
                  'site_area' if name('site_areas', incident.get('site_area_id'), '') else 'unknown')
    place_description = (f'Участок карты: «{place}» (по сохранённым данным).' if place_kind == 'site_area'
                         else f'Место: {place}.')
    state = incident.get('condition_state')
    state_text = {'active': 'Условие происшествия наблюдалось.',
                  'restored': 'Система отметила условие как восстановленное.',
                  'unknown': 'Состояние объекта не подтверждено данными.'}.get(state, 'Состояние объекта не указано.')
    if kind == 'forbidden_zone' and state == 'active':
        state_text = 'Правило ещё не подтвердило окончание нарушения доступа.'
    elif kind == 'forbidden_zone' and state == 'restored':
        state_text = 'Система отметила завершение нарушения. Подтверждения выхода из зоны в этом срезе недостаточно.'
    elif kind == 'model_anomaly' and state == 'active':
        state_text = 'Модельное подозрение ещё не было снято системой.'
    elif kind == 'model_anomaly' and state == 'restored':
        state_text = 'Система сняла модельное подозрение. Причина движения не установлена.'
    details = incident.get('details') or {}
    exit_samples = details.get('exit_samples')
    required_exit_samples = context.get('zone_exit_confirm_samples')
    confirmed_exit = (kind == 'forbidden_zone' and state == 'restored'
                      and incident.get('condition_active') is False
                      and incident.get('rule_version') == 'rules-v2.2-safety'
                      and type(required_exit_samples) is int and required_exit_samples > 0
                      and type(exit_samples) is int and exit_samples >= required_exit_samples)
    if confirmed_exit:
        state_text = 'Выход из зоны подтверждён правилом: получено необходимое число последовательных измерений выхода.'

    observations = []
    if kind == 'forbidden_zone':
        description = f'Система зарегистрировала нарушение правила доступа в зону. Объект: {entity}. Место: {place}. Требуется проверить обстоятельства въезда.'
    elif kind == 'model_anomaly':
        description = f'Для объекта «{entity}» сохранено модельное подозрение на необычное движение. {place_description} Требуется проверить, соответствует ли движение текущей работе.'
        linked_id = details.get('observation_id')
        linked = {fact['field']: fact['value'] for fact in facts
                  if fact['source'] == 'model_observation' and fact['id'] == linked_id}
        score, threshold = linked.get('score'), linked.get('threshold')
        numeric = lambda value: type(value) in (int, float) and math.isfinite(value)
        def display(value):
            return format(value, '.6g').replace('.', ',')
        if numeric(score):
            observations.append(f'Оценка необычности движения: ≈ {display(score)}.')
        if numeric(threshold):
            observations.append(f'Порог срабатывания модели: ≈ {display(threshold)}. При достижении порога модель отмечает движение как подозрительное.')
        if numeric(score) or numeric(threshold):
            observations.append('Числа округлены для отображения; точные значения сохранены в технических данных.')
        if (linked.get('status') == 'anomaly' and type(score) in (int, float)
                and type(threshold) in (int, float) and math.isfinite(score)
                and math.isfinite(threshold) and score >= threshold):
            observations.append('Оценка необычности движения достигла или превысила заданный порог модели.')
        else:
            observations.append('Проверенных данных недостаточно, чтобы утверждать превышение порога модели.')
        observations.append('Причина движения моделью не подтверждена; её оценка не является вероятностью ДТП.')
    elif kind == 'sensor_offline':
        description = f'Система зарегистрировала отсутствие сигнала датчика. Объект: {entity}. Место: {place}. Требуется проверить источник данных.'
    else:
        description = f'Система зарегистрировала происшествие «{TITLES.get(kind, "Тип не определён") }». Объект: {entity}. Место: {place}. Требуется проверить сохранённые наблюдения.'

    for fact in facts:
        source, field = fact['source'], fact['field']
        if source == 'event' and field in ('payload.x', 'payload.y') and fact['value'] is None:
            text = 'Значение координаты отсутствует в исходном измерении; положение по нему не установлено.'
        elif (source == 'event' and field in ('payload.x', 'payload.y')
              and type(fact['value']) in (int, float) and math.isfinite(fact['value'])):
            axis = 'Горизонтальная' if field == 'payload.x' else 'Вертикальная'
            value = str(fact['value']).replace('.', ',')
            text = f'{axis} координата по сохранённому измерению: {value}.'
        elif (source == 'policy' and field == 'allowed_zone_ids'
              and fact['id'] == (incident.get('asset_id') or incident.get('employee_id'))
              and isinstance(fact['value'], list) and incident.get('zone_id')):
            text = ('В сохранённом допуске объекта эта зона отсутствует.'
                    if incident['zone_id'] not in fact['value'] else
                    'Эта зона указана в сохранённом допуске объекта; возможные дополнительные ограничения проверяются отдельно.')
        elif source == 'sensor_health' and field == 'last_received_at' and fact['value'] is None:
            text = 'В сохранённых данных нет времени последнего сигнала датчика.'
        else:
            continue
        if text not in observations:
            observations.append(text)

    coordinates = context.get('coordinate_system', {})
    coordinates = coordinates if isinstance(coordinates, dict) else {}
    if any(f['source'] == 'event' and f['field'] in ('payload.x', 'payload.y') for f in facts):
        if coordinates.get('units') == 'plan':
            observations.append('Координаты заданы в условных единицах плана предприятия. Перевод в метры не задан.')
            if coordinates.get('y_direction') == 'down' and any(f['field'] == 'payload.y' for f in facts):
                observations.append('На плане вертикальная координата увеличивается к нижней границе.')
        else:
            observations.append('Единицы координат и их физический смысл не указаны; расстояния по ним не оценивались.')

    contact = (incident.get('response_plan') or {}).get('contact')
    recommendations = ['Откройте объект на карте и проверьте время последних показаний.']
    if kind == 'model_anomaly':
        recommendations.append('Уточните у ответственного за объект, выполнялись ли в это время погрузка, ожидание или манёвр; причина пока не установлена.')
    elif kind == 'sensor_offline':
        recommendations.append('Проверьте время последнего сигнала и состояние датчика; не считайте старое положение текущим.')
    elif kind == 'forbidden_zone':
        recommendations.append('Уточните обстоятельства въезда и предусмотренный допуск к этой зоне.')
    if isinstance(contact, str) and contact.strip():
        recommendations.append(f'Свяжитесь с указанными в плане реакции ответственными: {contact}.')
    else:
        recommendations.append('Контакт для реакции не указан; уточните его в карточке происшествия.')
    recommendations.append('Запишите результат проверки в карточке. Завершайте обработку только при выполнении штатных условий.')
    return {'version': 1, 'title': TITLES.get(kind, 'Происшествие'), 'description': description,
            'entity': entity, 'place': place, 'place_kind': place_kind, 'as_of': snapshot.as_of,
            'state': {'code': state, 'text': state_text, 'confirmed_exit': confirmed_exit},
            'observations': observations, 'recommendations': recommendations}
