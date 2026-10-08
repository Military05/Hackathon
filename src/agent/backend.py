"""Bounded snapshot from one v5 SQLite read transaction; no network while reading."""
import json

from src.core.service import stamp
from .errors import AgentError


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
            if not observation:
                raise AgentError("snapshot_invalid", "Не найдена связанная оценка MLP.")
            observations.append(observation)
        if subject:
            row = db.execute("SELECT body FROM model_observations WHERE asset_id=? "
                             "ORDER BY window_end DESC,observation_id DESC LIMIT 1", (subject,)).fetchone()
            if row:
                latest = json.loads(row[0])
                if latest["observation_id"] != observation_id:
                    observations.append(latest)

        required = set(incident.get("evidence_event_ids", []))
        for observation in observations:
            required.update(observation["evidence_event_ids"])
        if len(required) > 100:
            raise AgentError("snapshot_invalid", "Доказательства превышают предел 100 событий.")
        events = {}
        for event_id in sorted(required):
            event = service._load(db, "events", "event_id", event_id)
            if not event:
                raise AgentError("snapshot_invalid", "Исходное событие отсутствует в базе.")
            events[event_id] = event
        for subject_id in subjects:
            for row in db.execute("SELECT body FROM events WHERE asset_id=? "
                                  "ORDER BY event_time DESC,event_id DESC LIMIT 100", (subject_id,)):
                event = json.loads(row[0])
                if len(events) >= 100:
                    break
                events.setdefault(event["event_id"], event)
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
        related = set(subjects + [incident.get('zone_id'), incident.get('building_id'), incident.get('site_area_id')])
        display_context = {
            group: {row['id']: row['name'] for row in service.site.get(group, [])
                    if row.get('id') in related and isinstance(row.get('name'), str)}
            for group in ('assets', 'zones', 'buildings', 'site_areas')}
        display_context.update(coordinate_system=dict(service.site.get('coordinate_system', {})),
                               zone_exit_confirm_samples=service.config.get('zone_exit_confirm_samples'))
        return {"as_of": as_of, "rule_version": service.rule_version,
                "model_version": service.model_version, "incident": incident,
                "events": rows, "observations": observations, "policies": policies,
                "sensor_health": health, "history_bounds": bounds,
                "display_context": display_context}
