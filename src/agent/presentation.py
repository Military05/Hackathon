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
        description = f'Для объекта «{entity}» сохранено модельное подозрение на необычное движение. Место: {place}. Требуется проверить, соответствует ли движение текущей работе.'
        linked_id = details.get('observation_id')
        linked = {fact['field']: fact['value'] for fact in facts
                  if fact['source'] == 'model_observation' and fact['id'] == linked_id}
        score, threshold = linked.get('score'), linked.get('threshold')
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
        elif source == 'event' and field in ('payload.x', 'payload.y'):
            axis = 'горизонтальную' if field == 'payload.x' else 'вертикальную'
            text = f'Датчик передал {axis} координату положения объекта; точное значение сохранено в технических данных.'
        elif source == 'event' and field == 'payload.asset_id':
            text = 'Измерение содержит привязку к зарегистрированному объекту.'
        elif source == 'event' and field == 'event_time':
            text = 'Время исходного измерения сохранено.'
        elif source == 'event' and field == 'type':
            text = 'Тип исходного события проверен.'
        elif source == 'policy':
            text = 'Прочитаны сведения о допуске объекта из сохранённой политики.'
        elif source == 'sensor_health':
            text = 'Прочитаны сохранённые сведения о состоянии источника данных.'
        elif source == 'model_observation' and kind == 'model_anomaly':
            continue
        else:
            text = 'Дополнительное наблюдение проверено; его смысл не уточнён. Исходное поле сохранено в технических данных.'
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
            'entity': entity, 'place': place, 'as_of': snapshot.as_of,
            'state': {'code': state, 'text': state_text, 'confirmed_exit': confirmed_exit},
            'observations': observations, 'recommendations': recommendations}
