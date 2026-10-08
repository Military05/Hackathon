"""Заключения для диспетчера из проверенных фактов и сохранённых правил."""
import math

TITLES = {'forbidden_zone': 'Въезд в зону с ограничением',
          'model_anomaly': 'Проверка необычного движения', 'sensor_offline': 'Нет сигнала датчика',
          'unauthorized_access': 'Проход без допуска', 'route_deviation': 'Отклонение от маршрута',
          'collision': 'Сближение транспорта'}


def present(session, facts):
    snapshot = session.snapshot
    incident = snapshot.data['incident']
    context = snapshot.data.get('display_context') or {}
    context = context if isinstance(context, dict) else {}
    kind = incident.get('type')
    details = incident.get('details') or {}

    def name(group, identifier, fallback):
        mapping = context.get(group)
        value = mapping.get(identifier) if isinstance(mapping, dict) else None
        return value if isinstance(value, str) and value.strip() else fallback

    entity = name('assets', incident.get('asset_id') or incident.get('employee_id'), 'Объект не определён')
    place_kind, place = 'unknown', 'Место не определено'
    for group, category, identifier in [('zones', 'zone', incident.get('zone_id')),
                                       ('buildings', 'building', incident.get('building_id')),
                                       ('site_areas', 'site_area', incident.get('site_area_id'))]:
        registered = name(group, identifier, '')
        if registered:
            place_kind, place = category, registered
            break
    location = f'Участок карты: «{place}» (по сохранённым данным).' if place_kind == 'site_area' else f'Место: {place}.'
    description = f'Объект: {entity}. {location}'
    state = incident.get('condition_state')
    active = state == 'active' and incident.get('condition_active') is True
    restored = state == 'restored' and incident.get('condition_active') is False
    known_rule = incident.get('rule_version') == 'rules-v2.2-safety'

    def enough(count, required):
        return type(count) is int and type(required) is int and required > 0 and count >= required

    confirmed_exit = (kind == 'forbidden_zone' and restored and known_rule
                      and enough(details.get('exit_samples'), context.get('zone_exit_confirm_samples')))
    title, outcome = 'Данных недостаточно для вывода', 'insufficient'
    established = 'Подтвердить состояние объекта по имеющимся данным не удалось.'
    state_text = 'Состояние объекта не подтверждено данными.' if state else 'Состояние объекта не указано.'
    attention = 'Перед обработкой происшествия нужно уточнить состояние объекта.'
    unknown = 'Для заключения нужны свежие сведения об объекте.'
    observations = []

    if kind == 'forbidden_zone':
        evidence = incident.get('evidence_event_ids', [])
        rule_confirmed = known_rule and bool(evidence) and set(evidence) <= session.records['event'].keys()
        confirmed_exit = confirmed_exit and rule_confirmed
        if rule_confirmed:
            established = 'Зафиксирован въезд объекта в зону в нарушение ограничения доступа.'
            if confirmed_exit:
                title, outcome = 'Нарушение перестало наблюдаться', 'restored'
                state_text = 'Выход из зоны подтверждён. Это состояние на момент анализа.'
            elif active:
                title, outcome = 'Нарушение зоны подтверждено', 'confirmed'
                state_text = 'Прекращение нарушения на момент анализа ещё не подтверждено.'
        attention = 'Нужно проверить соблюдение ограничения доступа на участке.'
        unknown = 'Причина въезда неизвестна.'
        if not rule_confirmed:
            unknown = 'Не хватает подтверждённых сведений о нарушении доступа.'
        elif not confirmed_exit and not active:
            unknown += ' Не установлено, прекратилось ли нарушение к моменту анализа.'
    elif kind == 'model_anomaly':
        linked = {fact['field']: fact['value'] for fact in facts
                  if fact['source'] == 'model_observation' and fact['id'] == details.get('observation_id')}
        score, threshold = linked.get('score'), linked.get('threshold')
        deviation = (linked.get('status') == 'anomaly' and type(score) in (int, float)
                     and type(threshold) in (int, float) and math.isfinite(score)
                     and math.isfinite(threshold) and score >= threshold)
        if deviation:
            established = 'По проверенным данным обнаружено необычное движение объекта.'
            if active:
                title, outcome = 'Обнаружено необычное движение', 'confirmed'
                state_text = 'На момент анализа прекращение необычного движения ещё не подтверждено.'
            elif restored and known_rule and enough(details.get('normal_windows'), context.get('model_normal_windows')):
                title, outcome = 'Необычное движение перестало наблюдаться', 'restored'
                state_text = 'Последующие проверки подтвердили, что необычное движение перестало наблюдаться.'
        attention = 'Нужно выяснить, связано ли изменение движения с выполняемой работой.'
        unknown = 'Причина движения неизвестна. Наличие аварии не установлено.'
        if not deviation:
            unknown = 'Не хватает согласованных данных для подтверждения необычного движения.'
    elif kind == 'sensor_offline':
        title = 'Нет сигнала датчика'
        established = 'Система зарегистрировала отсутствие связи с датчиком.'
        attention = 'Без свежих показаний нельзя подтвердить положение объекта.'
        unknown = 'Причина отсутствия связи неизвестна.'
    else:
        title = TITLES.get(kind, 'Данных недостаточно для вывода')
        established = 'Система зарегистрировала это происшествие; подробности нужно проверить.'

    for fact in facts:
        if (fact['source'] == 'policy' and fact['field'] == 'allowed_zone_ids'
                and fact['id'] == (incident.get('asset_id') or incident.get('employee_id'))
                and isinstance(fact['value'], list) and incident.get('zone_id')
                and incident['zone_id'] not in fact['value']):
            observations.append('На момент проверки эта зона отсутствовала в допуске объекта.')
        elif fact['source'] == 'sensor_health' and fact['field'] == 'last_received_at' and fact['value'] is None:
            observations.append('Время последнего сигнала датчика неизвестно.')
    observations = list(dict.fromkeys(observations))
    contact = (incident.get('response_plan') or {}).get('contact')
    contact = contact if isinstance(contact, str) and contact.strip() else None
    recommendations = ['Проверьте свежие показания объекта на карте.']
    question = 'Уточните состояние объекта.'
    if kind == 'forbidden_zone':
        question = 'Уточните причину въезда и допуск к зоне.'
    elif kind == 'model_anomaly':
        question = 'Уточните, какую работу выполнял объект в это время.'
    elif kind == 'sensor_offline':
        question = 'Попросите проверить связь с датчиком.'
    if contact:
        recommendations.append(f'Свяжитесь с ответственными: {contact}. {question}')
    else:
        recommendations.append(f'Уточните ответственного в карточке происшествия. {question}')
    recommendations.append('Запишите результат проверки в карточке происшествия.')
    return {'version': 2, 'title': title, 'outcome': outcome, 'description': description,
            'established': established, 'attention': attention, 'unknown': unknown,
            'entity': entity, 'place': place, 'place_kind': place_kind, 'as_of': snapshot.as_of,
            'state': {'code': state, 'text': state_text, 'confirmed_exit': confirmed_exit},
            'observations': observations, 'recommendations': recommendations}


def review_model_text(answer, presentation):
    """Only exact supported actions qualify; arbitrary explanations never become evidence."""
    actions = set(presentation['recommendations'])
    review = [{'kind': 'hypothesis', 'text': hypothesis.text, 'admitted': False,
               'reason': 'Свободное предположение не подтверждает событие или причину.'}
              for hypothesis in answer.hypotheses]
    review.extend({'kind': 'recommendation', 'text': text, 'admitted': text in actions,
                   'reason': ('Действие соответствует проверенному плану.' if text in actions else
                              'Свободный текст не соответствует подтверждённым действиям диспетчера.')}
                  for text in answer.recommendations)
    return {'policy': 'supported_actions_only; hypotheses_never_facts', 'items': review,
            'main_text_source': 'program_from_verified_facts_and_rules'}
