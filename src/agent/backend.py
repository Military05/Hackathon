"""Bounded snapshot from one v5 SQLite read transaction; no network while reading."""
import json
import hashlib
from datetime import timedelta

from src.core.service import stamp
from .errors import AgentError
from .providers import canonical
from src.ml.features import parse_time

EVENT_LIMIT = 100


def select_evidence(incident, observations, events, recent, linked_id):
    """Keep observation evidence atomically; truncate only the detached Incident copy."""
    original = incident.get('evidence_event_ids', [])
    required = set(original)
    for observation in observations:
        required.update(observation['evidence_event_ids'])
    if len(required) <= EVENT_LIMIT:
        selected = dict((identifier, events[identifier]) for identifier in sorted(required))
        for event in recent:
            if len(selected) >= EVENT_LIMIT:
                break
            selected.setdefault(event['event_id'], event)
        return incident, observations, selected, None

    linked = next((row for row in observations if row['observation_id'] == linked_id), None)
    recent = sorted(recent, key=lambda row: (parse_time(row['event_time']), row['event_id']), reverse=True)
    selected_ids = set(linked['evidence_event_ids']) if linked else set()
    if len(selected_ids) > EVENT_LIMIT:
        raise AgentError('snapshot_invalid', 'Доказательства связанной оценки не помещаются в ограниченный анализ.')
    anchors = original[:1]
    if incident.get('type') == 'collision':
        for subject in (incident.get('asset_id'), incident.get('other_asset_id')):
            identifier = next((identifier for identifier in original
                               if events[identifier].get('payload', {}).get('asset_id') == subject), None)
            if identifier:
                anchors.append(identifier)
    # Reserve the newest captured measurement for each object before optional history.
    seen = set()
    fresh_ids = []
    for event in recent:
        if event.get('type') != 'position':
            continue
        subject = event.get('payload', {}).get('asset_id') or event.get('payload', {}).get('employee_id')
        if subject not in seen:
            seen.add(subject)
            anchors.append(event['event_id'])
            fresh_ids.append(event['event_id'])
    for identifier in anchors:
        if len(selected_ids) < EVENT_LIMIT:
            selected_ids.add(identifier)
    kept = [linked] if linked else []
    for observation in observations:
        if observation is linked:
            continue
        expanded = selected_ids | set(observation['evidence_event_ids'])
        if len(expanded) <= EVENT_LIMIT:
            selected_ids = expanded
            kept.append(observation)
    # Newest incident confirmations, then newest object measurements; ID breaks ties.
    ordered = sorted((events[identifier] for identifier in set(original)),
                     key=lambda row: (parse_time(row['event_time']), row['event_id']), reverse=True)
    for event in ordered + recent:
        if len(selected_ids) >= EVENT_LIMIT:
            break
        selected_ids.add(event['event_id'])
    copy = dict(incident, evidence_event_ids=[identifier for identifier in original if identifier in selected_ids])
    all_events = {**events, **{row['event_id']: row for row in recent}}
    metadata = {'version': 'evidence-selection-v1', 'partial': True,
                'original_incident_count': len(original), 'required_union_count': len(required),
                'selected_event_count': len(selected_ids),
                'omitted_incident_count': len(original)-len(copy['evidence_event_ids']),
                'omitted_observation_ids': [row['observation_id'] for row in observations if row not in kept],
                'first_incident_event_included': not original or original[0] in selected_ids,
                'fresh_measurements_included': all(identifier in selected_ids for identifier in fresh_ids),
                'source_digest': hashlib.sha256(canonical([original, observations]).encode()).hexdigest()}
    return copy, kept, {identifier: all_events[identifier] for identifier in sorted(selected_ids)}, metadata


def capture_snapshot(service, incident_id):
    with service.store.read() as db:
        # BEGIN is deferred; the incident read establishes the SQLite snapshot.
        incident = service._load(db, "incidents", "incident_id", incident_id)
        if not incident:
            raise AgentError("not_found", "Происшествие не найдено.", 404)
        as_of = stamp(service.clock())
        subject = incident.get("asset_id") or incident.get("employee_id")
        subjects = list(dict.fromkeys(value for value in
                        (subject, incident.get("other_asset_id")) if value))
        observation_id = incident.get("details", {}).get("observation_id")
        observations = []
        if observation_id:
            observation = service._load(db, "model_observations", "observation_id", observation_id)
            if not observation or observation.get('observation_id') != observation_id:
                raise AgentError("snapshot_invalid", "Не найдена связанная оценка MLP.")
            observations.append(observation)
        if subject:
            row = db.execute("SELECT observation_id,body FROM model_observations WHERE asset_id=? "
                             "ORDER BY window_end DESC,observation_id DESC LIMIT 1", (subject,)).fetchone()
            if row:
                latest = json.loads(row['body'])
                if latest.get('observation_id') != row['observation_id']:
                    raise AgentError('snapshot_invalid', 'Идентификатор оценки не соответствует сохранённой записи.')
                if latest["observation_id"] != observation_id:
                    observations.append(latest)

        original = incident.get('evidence_event_ids', [])
        lists = [original] + [row.get('evidence_event_ids') for row in observations]
        if any(not isinstance(ids, list) or len(ids) > EVENT_LIMIT or
               any(not isinstance(identifier, str) or not identifier for identifier in ids) or
               len(ids) != len(set(ids)) for ids in lists):
            raise AgentError('snapshot_invalid', 'Некорректный список доказательств.')
        if any(row.get('asset_id') != subject for row in observations):
            raise AgentError('snapshot_invalid', 'Оценка движения относится к другому объекту.')
        try:
            canonical(observations)
        except (ValueError, TypeError) as error:
            raise AgentError('snapshot_invalid', 'Оценка содержит некорректные данные.') from error
        required = set(original)
        for observation in observations:
            required.update(observation["evidence_event_ids"])
        events = {}
        for event_id in sorted(required):
            event = service._load(db, "events", "event_id", event_id)
            if not event or event.get('event_id') != event_id:
                raise AgentError("snapshot_invalid", "Исходное событие отсутствует в базе.")
            try:
                parse_time(event['event_time'])
                if event.get('received_at') and parse_time(event['received_at']) > parse_time(as_of):
                    raise ValueError('Event received after snapshot')
            except (ValueError, KeyError, TypeError) as error:
                raise AgentError('snapshot_invalid', 'Некорректное время исходного доказательства.') from error
            events[event_id] = event
        for observation in observations:
            if any(events[identifier].get('payload', {}).get('asset_id') != subject
                   for identifier in observation['evidence_event_ids']):
                raise AgentError('snapshot_invalid', 'Доказательства оценки относятся к другому объекту.')
        recent = []
        for subject_id in subjects:
            for row in db.execute("SELECT event_id,body FROM events WHERE asset_id=? "
                                  "ORDER BY event_time DESC,event_id DESC LIMIT 100", (subject_id,)):
                event = json.loads(row['body'])
                payload = event.get('payload') or {}
                if (event.get('event_id') != row['event_id'] or
                        (payload.get('asset_id') or payload.get('employee_id')) != subject_id):
                    raise AgentError('snapshot_invalid', 'Запись истории не соответствует объекту или идентификатору события.')
                recent.append(event)
        incident, observations, events, selection = select_evidence(incident, observations, events, recent, observation_id)
        rows = sorted(events.values(), key=lambda event: (event["event_time"], event["event_id"]))
        policies = {subject_id: service.asset_policy(subject_id) for subject_id in subjects}
        sensor_id = incident.get("sensor_id")
        health = {}
        if sensor_id:
            sensor = service._sensor_health(db, sensor_id)
            # Age is derived from wall time, not a new measurement. Keep the stable
            # raw timestamp/threshold/status so polling does not invalidate every job.
            sensor.pop("age_seconds", None)
            health[sensor_id] = sensor
        bounds = {"since": rows[0]["event_time"] if rows else incident["detected_at"],
                  "until": rows[-1]["event_time"] if rows else incident["detected_at"],
                  "limit": 100, "bounded": True}
        if selection:
            bounds['evidence_selection'] = selection
        related = set(subjects + [incident.get('zone_id'), incident.get('building_id'), incident.get('site_area_id')])
        display_context = {
            group: {row['id']: row['name'] for row in service.site.get(group, [])
                    if row.get('id') in related and isinstance(row.get('name'), str)}
            for group in ('assets', 'zones', 'buildings', 'site_areas')}
        display_context.update(coordinate_system=dict(service.site.get('coordinate_system', {})),
                               zone_exit_confirm_samples=service.config.get('zone_exit_confirm_samples'),
                               model_normal_windows=service.config.get('model_normal_windows'))
        since = stamp((service.clock() - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0))
        related = [json.loads(row[0]) for row in db.execute(
            "SELECT body FROM incidents WHERE incident_id<>? AND json_extract(body,'$.type')=? "
            "AND json_extract(body,'$.asset_id') IS ? AND json_extract(body,'$.employee_id') IS ? "
            "AND json_extract(body,'$.other_asset_id') IS ? AND json_extract(body,'$.sensor_id') IS ? "
            "AND json_extract(body,'$.zone_id') IS ? AND json_extract(body,'$.building_id') IS ? "
            "AND json_extract(body,'$.site_area_id') IS ? AND json_extract(body,'$.detected_at')>=? "
            "AND json_extract(body,'$.detected_at')<=? ORDER BY json_extract(body,'$.detected_at') DESC LIMIT 21",
            (incident_id, incident['type'], incident.get('asset_id'), incident.get('employee_id'),
             incident.get('other_asset_id'), incident.get('sensor_id'), incident.get('zone_id'),
             incident.get('building_id'), incident.get('site_area_id'), since, as_of))]
        history_limited = len(related) > 20
        related = [{key: row.get(key) for key in ('incident_id', 'type', 'detected_at', 'status', 'asset_id',
                   'employee_id', 'zone_id', 'building_id', 'site_area_id')} for row in related[:20]]
        notes = []
        for row in db.execute("SELECT history_id,body FROM dispatch_history WHERE incident_id=? "
                              "ORDER BY rowid DESC LIMIT 30", (incident_id,)):
            item = json.loads(row['body'])
            if item.get('reason') and item.get('actor_operator_id'):
                notes.append({'note_id': row['history_id'], 'text': item['reason'],
                              'text_excerpt': item['reason'][:160], 'created_at': item['created_at'],
                              'operator_id': item.get('actor_operator_id'), 'action': item.get('action')})
                if len(notes) >= 5:
                    break
        epoch = db.execute("SELECT value FROM metadata WHERE key='incident_log_epoch'").fetchone()
        return {"as_of": as_of, "rule_version": service.rule_version,
                "model_version": service.model_version, "incident": incident,
                "events": rows, "observations": observations, "policies": policies,
                "sensor_health": health, "history_bounds": bounds,
                "display_context": display_context,
                'incident_context': {'related_incidents': related, 'dispatcher_notes': notes,
                    'since': since, 'until': as_of, 'limited': history_limited,
                    'similarity': 'same_type_object_and_place', 'log_epoch': int(epoch[0]) if epoch else 0}}
