"""Regression uses only disposable SQLite and real Incident/Observation storage."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.service import Service, stamp
from src.agent.backend import capture_snapshot
from src.agent.providers import FrozenSnapshot, CaptureProvider
from src.agent.tools import ToolSession
from src.agent.result import validate_result
from src.agent.errors import AgentError
from src.agent.config import AgentConfig
from src.agent.service import AgentService


@pytest.fixture
def backend(tmp_path):
    base = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    service = Service(tmp_path / 'test.db', Path(__file__).parents[2] / 'data/demo/site.json',
                      clock=lambda: base + timedelta(seconds=400))
    for n in range(260):
        event = {'event_id': f'e{n:03}', 'event_time': stamp(base + timedelta(seconds=n)),
                 'sensor_id': 'POS-V1', 'type': 'position', 'demo': True,
                 'payload': {'asset_id': 'V1', 'x': 30, 'y': 60}}
        service.ingest_event(event)
    return service


def observation(service, start, count=10, status='anomaly'):
    with service.store.read() as db:
        events = [json.loads(db.execute('SELECT body FROM events WHERE event_id=?',
                                       (f'e{n:03}',)).fetchone()[0]) for n in range(start, start+count)]
    value = {'observation_id': f'o{start}', 'asset_id': 'V1', 'window_start': events[0]['event_time'],
             'window_end': events[-1]['event_time'], 'status': status,
             'score': .99 if status == 'anomaly' else .1, 'threshold': .8,
             'model_version': 'explicit-test-model-v2', 'feature_version': 'explicit-test-features',
             'evidence_event_ids': [event['event_id'] for event in events]}
    service.register_model_observation(value)
    return value


def incident(service):
    return next(row for row in service.list_incidents() if row['type'] == 'model_anomaly')


def snapshot(service):
    return FrozenSnapshot(capture_snapshot(service, incident(service)['incident_id']))


def populate(service, restored=False):
    first = observation(service, 0)
    for start in range(10, 200, 10):
        observation(service, start)
    if restored:
        for n in range(service.config['model_normal_windows']):
            observation(service, 200+n*10, status='normal')
    return first


def dump(service):
    with service.store.read() as db:
        return list(db.iterdump())


def test_small_snapshot_keeps_all_incident_and_observation_evidence(backend):
    obs = observation(backend, 0)
    frozen = snapshot(backend)
    assert frozen.data['incident']['evidence_event_ids'] == obs['evidence_event_ids']
    assert len(frozen.events) == 100
    assert 'evidence_selection' not in frozen.data['history_bounds']


def test_exactly_100_required_events_preserves_all_ids(backend):
    obs = observation(backend, 0, count=100)
    frozen = snapshot(backend)
    assert set(frozen.events) == set(obs['evidence_event_ids'])
    assert 'evidence_selection' not in frozen.data['history_bounds']


def test_overflow_preserves_historical_linked_observation_first_and_latest_without_db_changes(backend):
    first = populate(backend, restored=True)
    original = incident(backend)
    with backend.store.transaction() as db:
        original['details']['observation_id'] = first['observation_id']
        backend._save_incident(db, original)
    before = dump(backend)
    frozen = snapshot(backend)
    assert dump(backend) == before
    assert len(frozen.events) <= 100
    assert set(first['evidence_event_ids']) <= frozen.events.keys()
    assert 'e000' in frozen.events and 'e259' in frozen.events
    assert len(original['evidence_event_ids']) == 100
    assert set(frozen.data['incident']['evidence_event_ids']) <= frozen.events.keys()
    for obs in frozen.observations.values():
        assert set(obs['evidence_event_ids']) <= frozen.events.keys()
    metadata = frozen.data['history_bounds']['evidence_selection']
    assert metadata['partial'] and metadata['omitted_incident_count'] > 0
    assert metadata['fresh_measurements_included']
    tools = ToolSession(frozen)
    response = tools.execute('get_incident', json.dumps({'incident_id': frozen.incident_id}))
    assert response['history_bounds']['evidence_selection'] == metadata
    reply = {'facts': [{'source':'model_observation', 'id':first['observation_id'], 'field':field,
                        'value':first[field]} for field in ('status','score','threshold')],
             'hypotheses':[], 'recommendations':['Проверьте свежие показания объекта на карте.']}
    output = validate_result(json.dumps(reply), tools, 'explicit-test-double')
    assert 'часть сохранённой истории' in str(output['presentation'])
    omitted = next(identifier for identifier in original['evidence_event_ids'] if identifier not in frozen.events)
    reply['facts'].append({'source':'event','id':omitted,'field':'payload.x','value':30})
    with pytest.raises(AgentError) as error:
        validate_result(json.dumps(reply), tools, 'explicit-test-double')
    assert error.value.code == 'invalid_evidence'


def test_repeated_anomaly_then_normal_is_enqueued_cached_and_stale_on_new_data(backend):
    populate(backend, restored=True)
    current = incident(backend)
    assert current['condition_state'] == 'restored'
    assert len([row for row in backend.list_incidents() if row['type']=='model_anomaly']) == 1
    first_hash = snapshot(backend).snapshot_id
    oldclock = backend.clock()
    backend.clock = lambda: oldclock+timedelta(seconds=1)
    assert first_hash == snapshot(backend).snapshot_id
    class Client:
        async def ensure_available(self): pass
        async def close(self): pass
    async def run():
        agent = AgentService(AgentConfig(database=backend.store.path, model='explicit-test-double'),
                             CaptureProvider(lambda identifier:capture_snapshot(backend, identifier)), Client())
        # Keep worker idle while exercising actual admission/cache/staleness against SQLite.
        agent.worker = asyncio.create_task(asyncio.Event().wait())
        try:
            job = await agent.request(current['incident_id'], 'dispatcher-1')
            assert job['status']=='queued'
            again = await agent.request(current['incident_id'], 'dispatcher-1')
            assert again['job_id']==job['job_id'] and again['cached']
            assert not (await agent.get(job['job_id']))['stale']
            observation(backend, 250, status='normal')
            assert (await agent.get(job['job_id']))['stale']
        finally: await agent.stop()
    asyncio.run(run())


@pytest.mark.parametrize('corruption',['missing','wrong_id','wrong_asset','duplicate','invalid_id','invalid_time','wrong_observation_id'])
def test_invalid_evidence_is_rejected_even_when_it_would_be_omitted(backend, corruption):
    populate(backend, restored=True)
    with backend.store.transaction() as db:
        if corruption=='missing':
            db.execute("DELETE FROM events WHERE event_id='e101'")
        elif corruption=='wrong_id':
            db.execute("UPDATE events SET body=json_set(body,'$.event_id','conflict') WHERE event_id='e101'")
        elif corruption=='wrong_asset':
            db.execute("UPDATE events SET body=json_set(body,'$.payload.asset_id','V2') WHERE event_id='e199'")
        elif corruption=='invalid_time':
            db.execute("UPDATE events SET body=json_set(body,'$.event_time','invalid') WHERE event_id='e101'")
        elif corruption=='wrong_observation_id':
            db.execute("UPDATE model_observations SET body=json_set(body,'$.observation_id','conflict') "
                       "WHERE observation_id=(SELECT observation_id FROM model_observations ORDER BY window_end DESC LIMIT 1)")
        else:
            row=incident(backend)
            row['evidence_event_ids'].append(row['evidence_event_ids'][0] if corruption=='duplicate' else None)
            backend._save_incident(db,row)
    with pytest.raises(AgentError) as error: snapshot(backend)
    assert error.value.code=='snapshot_invalid'


def test_optional_latest_observation_is_omitted_atomically_when_it_cannot_fit(backend):
    linked=observation(backend,0,count=100)
    observation(backend,150,count=100,status='normal')
    frozen=snapshot(backend)
    assert set(frozen.observations)=={linked['observation_id']}
    selection=frozen.data['history_bounds']['evidence_selection']
    assert selection['omitted_observation_ids']==['o150']
    assert not selection['fresh_measurements_included']
    assert len(frozen.events)==100
