"""Real temporary-database checks for service tours, pause/resume and shared CSV."""
import asyncio
import csv
from datetime import datetime, timedelta, timezone
import io
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from src.core.demo_traffic import ArcRoute, DemoRunner
from src.core.service import Service, stamp
from tests.runtime.isolated_app import ADMIN_PASSWORD, create_app

ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class ScenarioRefinements(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.service = Service(Path(self.temp.name) / 'scenarios.db', ROOT / 'data/demo/site.json', clock=self.clock)
        self.runner = DemoRunner(self.service, interval=3600)

    def tearDown(self):
        self.temp.cleanup()

    def test_service_moves_only_v3_on_extended_shared_route_with_fresh_parked_forklifts(self):
        self.runner._prepare('service')
        positions = {asset: set() for asset in ('V1', 'V2', 'V3')}
        for elapsed in range(95):
            self.runner.emit_frame(elapsed)
            self.service.tick()
            for asset in self.service.list_assets():
                if asset['id'] in positions:
                    positions[asset['id']].add((asset['x'], asset['y']))
                    self.assertEqual(asset['position_state'], 'fresh')
            self.clock.advance(1)
        self.assertEqual(len(positions['V1']), 1)
        self.assertEqual(len(positions['V2']), 1)
        self.assertGreater(len(positions['V3']), 70)
        self.assertEqual(self.runner.status()['vehicle_states']['V1']['state'], 'parked')
        self.assertEqual(self.runner.status()['vehicle_states']['V2']['state'], 'parked')
        self.assertTrue(all(sensor['status'] == 'online' for sensor in self.service.list_sensors()))
        self.assertEqual(self.service.list_incidents(), [])
        route = self.service.site['demo_routes']['V3']
        self.assertEqual(route, self.service.site['routes']['V3'])
        self.assertEqual(route, self.service.site['safety_routes']['V3'])
        self.assertGreater(ArcRoute(route).length, 140)
        for point in ((58,69), (57,79), (52,87), (22,91)):
            self.assertIn(list(point), route)
        self.assertLess(math.dist(self.runner._position('V3', 95)[0], self.runner._position('V3', 96)[0]), 1.901)

    def test_normal_long_tour_yields_and_measured_segments_stay_apart_for_twenty_minutes(self):
        from src.core.vehicle_safety import swept_distance
        self.runner._prepare('normal')
        previous, visited, minimum, yielded = None, [], 100.0, 0
        for elapsed in range(1201):
            frame = {asset: self.runner._position(asset, elapsed)[0] for asset in ('V1','V2','V3')}
            visited.append(frame['V3'])
            yielded += int(not self.runner._normal_v3_departed)
            if previous:
                self.assertLessEqual(math.dist(previous['V3'], frame['V3']), 1.701)
                for peer in ('V1','V2'):
                    separation = swept_distance((*previous['V3'],0), (*frame['V3'],1),
                                                (*previous[peer],0), (*frame[peer],1))
                    if separation is not None:
                        minimum = min(minimum, separation)
            previous = frame
        self.assertGreater(minimum, 1.0)
        self.assertGreater(yielded, 0)
        for destination in ((58,69),(57,79),(52,87),(22,91)):
            self.assertLess(min(math.dist(destination, point) for point in visited), 1.0)

    def test_pause_freezes_episode_and_shift_then_resume_does_not_duplicate_gate_passages(self):
        async def check():
            self.runner._prepare('shift')
            for elapsed in range(4):
                self.runner.emit_frame(elapsed)
                self.clock.advance(1)
            before = self.runner.status()
            run_id, sequence = self.runner._run_id, self.runner._sequence
            coordinates = [(item['id'], item.get('x'), item.get('y'), item['last_seen']) for item in self.service.list_assets() if item['type'] == 'vehicle']
            paused = await self.runner.stop()
            self.assertTrue(paused['paused'])
            self.assertTrue(paused['can_resume'])
            self.clock.advance(60)
            self.runner.emit_frame(80)
            self.service.tick()
            self.assertEqual(self.runner.event_count, before['event_count'])
            self.assertEqual(self.runner.last_elapsed, before['elapsed_seconds'])
            self.assertEqual(coordinates, [(item['id'], item.get('x'), item.get('y'), item['last_seen']) for item in self.service.list_assets() if item['type'] == 'vehicle'])
            self.assertTrue(all(item['monitoring_paused'] for item in self.service.list_sensors()))
            self.assertEqual(self.service.list_incidents(), [])
            resumed = await self.runner.resume()
            self.assertTrue(resumed['running'])
            self.assertFalse(resumed['paused'])
            self.assertEqual(self.runner._run_id, run_id)
            self.assertGreater(self.runner._sequence, sequence)
            self.assertEqual(resumed['elapsed_seconds'], before['elapsed_seconds'])
            self.assertEqual(resumed['shift']['shift_id'], before['shift']['shift_id'])
            self.assertEqual(resumed['shift']['gate_count'], 2)
            self.clock.advance(2)
            self.runner.emit_frame(5)
            self.assertEqual(self.runner.status()['shift']['gate_count'], 3)
            self.assertEqual(self.runner.status()['shift']['phase'], 'transport')
            await self.runner.stop()
            await self.runner.resume()
            self.assertEqual(self.runner.status()['shift']['gate_count'], 3)
            self.assertEqual(sum(event['type'] == 'access' for event in self.service.event_history(limit=200)), 3)
            await self.runner.start('service')
            self.assertNotEqual(self.runner._run_id, run_id)
            self.assertEqual(self.runner.last_elapsed, 0)
            self.assertEqual(self.runner.operations.shift(before['shift']['shift_id'])['phase'], 'stopped')
            await self.runner.shutdown()
        asyncio.run(check())

    def test_resume_during_outage_excludes_planned_pause_from_monitoring_age(self):
        async def check():
            self.runner._prepare('sensor-offline')
            for elapsed in range(7):
                if elapsed:
                    self.clock.advance(1)
                self.runner.emit_frame(elapsed)
                self.service.tick()
            original = self.service.sensor_health('HB-QA')
            await self.runner.stop()
            self.clock.advance(60)
            self.service.tick()
            self.assertEqual(self.service.list_incidents(), [])
            paused = self.service.sensor_health('HB-QA')
            self.assertEqual(paused['last_received_at'], original['last_received_at'])
            self.assertAlmostEqual(paused['age_seconds'], 62, delta=0.001)
            self.assertAlmostEqual(paused['monitoring_age_seconds'], 2, delta=0.001)
            await self.runner.resume()
            self.service.tick()
            self.assertEqual(self.service.sensor_health('HB-QA')['status'], 'online')
            self.assertEqual(self.service.list_incidents(), [])
            self.clock.advance(3)
            self.runner.emit_frame(9)
            self.service.tick()
            self.assertAlmostEqual(self.service.sensor_health('HB-QA')['monitoring_age_seconds'], 5, delta=0.001)
            incidents = self.service.list_incidents()
            self.assertEqual(len(incidents), 1)
            self.assertEqual(incidents[0]['sensor_id'], 'HB-QA')
            await self.runner.stop()
            self.clock.advance(30)
            self.service.tick()
            self.assertEqual(self.service.list_incidents()[0]['condition_state'], incidents[0]['condition_state'])
            await self.runner.resume()
            self.clock.advance(8)
            self.runner.emit_frame(17)
            self.service.tick()
            self.assertEqual(self.service.sensor_health('HB-QA')['status'], 'online')
            self.assertFalse(self.service.list_incidents()[0]['condition_active'])
            await self.runner.shutdown()
        asyncio.run(check())

    def test_late_builtin_model_result_cannot_create_incident_after_pause_resume_or_restart(self):
        async def check():
            self.runner._prepare('normal')
            self.runner.emit_frame(0)
            generation = self.service.demo_monitoring_generation
            evidence = self.service.event_history('V3')[0]['event_id']
            observation = {'observation_id':'late-demo-model', 'asset_id':'V3', 'window_start':stamp(self.clock()),
                'window_end':stamp(self.clock()), 'status':'anomaly', 'model_version':'test-v1',
                'feature_version':'test-v1', 'evidence_event_ids':[evidence], 'score':1.0, 'threshold':0.5}
            await self.runner.stop()
            self.assertIsNone(self.service.register_model_observation(observation, expected_demo_generation=generation))
            await self.runner.resume()
            self.assertIsNone(self.service.register_model_observation(observation, expected_demo_generation=generation))
            await self.runner.start('service')
            self.assertIsNone(self.service.register_model_observation(observation, expected_demo_generation=generation))
            self.assertEqual(self.service.model_observations(), [])
            self.assertEqual(self.service.list_incidents(), [])
            await self.runner.shutdown()
        asyncio.run(check())

    def test_external_samples_are_monitored_while_builtin_demo_is_paused(self):
        self.runner._prepare('normal')
        self.runner.emit_frame(0)
        asyncio.run(self.runner.stop())
        event = {'event_id':'external-heartbeat', 'event_time':stamp(self.clock()), 'sensor_id':'HB-QA',
                 'type':'heartbeat', 'demo':True, 'payload':{}}
        self.service.ingest_event(event)
        self.clock.advance(6)
        self.service.tick()
        health = self.service.sensor_health('HB-QA')
        self.assertFalse(health['monitoring_paused'])
        self.assertEqual(health['status'], 'offline')
        self.assertEqual([item['sensor_id'] for item in self.service.list_incidents()], ['HB-QA'])


class ResumeSecurityAndCsv(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'DISPATCH_ENABLE_AUTH':'1', 'DISPATCH_ENABLE_AGENT':'0',
            'DISPATCH_ENABLE_ML':'0', 'DISPATCH_ENABLE_SIMULATOR':'0', 'DISPATCH_ENABLE_DEMO_TRAFFIC':'1', 'DEMO_AUTOSTART':'0'})
        self.env.start()
        self.app = create_app(db_path=Path(self.temp.name) / 'http.db', enable_scheduler=False)
        self.clients = []
        for number in (1,2,3):
            self.app.state.auth.create_user(f'refinements.{number}', f'Диспетчер {number}', 'Refinements-password-2026',
                operator_id=f'dispatcher-{number}', status='active')

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.env.stop()
        self.temp.cleanup()

    def login(self, number):
        client = TestClient(self.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50200))
        self.clients.append(client)
        response = client.post('/api/auth/login', json={'username':'admin' if number == 0 else f'refinements.{number}',
            'password':ADMIN_PASSWORD if number == 0 else 'Refinements-password-2026'})
        self.assertEqual(response.status_code, 200, response.text)
        return client, {'X-CSRF-Token':response.json()['csrf_token'], 'X-Expected-User':response.json()['user']['id']}

    def test_resume_requires_sector_csrf_expected_user_and_allows_admin(self):
        runner = self.app.state.demo
        runner._prepare('service')
        runner.paused = True
        foreign, foreign_headers = self.login(2)
        own, own_headers = self.login(3)
        admin, admin_headers = self.login(0)
        with patch.object(runner, 'resume', new_callable=AsyncMock, return_value={'running':True,'paused':False}) as resume:
            self.assertFalse(foreign.get('/api/demo/status').json()['can_resume'])
            self.assertEqual(foreign.post('/api/demo/resume', json={}, headers={**foreign_headers,'X-Demo-Operator':'admin'}).status_code, 403)
            self.assertEqual(own.post('/api/demo/resume', json={}).status_code, 403)
            self.assertEqual(own.post('/api/demo/resume', json={}, headers={**own_headers,'X-Expected-User':'stale-tab'}).status_code, 409)
            resume.assert_not_awaited()
            self.assertTrue(own.get('/api/demo/status').json()['can_resume'])
            self.assertEqual(own.post('/api/demo/resume', json={'scenario':'service'}, headers=own_headers).status_code, 422)
            self.assertEqual(own.post('/api/demo/resume', json={}, headers=own_headers).status_code, 200)
            self.assertEqual(admin.post('/api/demo/resume', json={}, headers=admin_headers).status_code, 200)
            self.assertEqual(resume.await_count, 2)
        runner.paused = False
        self.assertEqual(admin.post('/api/demo/resume', json={}, headers=admin_headers).status_code, 409)

    def test_authenticated_http_pause_resume_keeps_same_episode(self):
        with TestClient(self.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1',50210)) as client:
            login = client.post('/api/auth/login', json={'username':'admin','password':ADMIN_PASSWORD})
            self.assertEqual(login.status_code, 200, login.text)
            headers = {'X-CSRF-Token':login.json()['csrf_token'], 'X-Expected-User':login.json()['user']['id']}
            started = client.post('/api/demo/start', json={'scenario':'service'}, headers=headers)
            self.assertEqual(started.status_code, 200, started.text)
            original_run = self.app.state.demo._run_id
            paused = client.post('/api/demo/stop', json={}, headers=headers)
            self.assertEqual(paused.status_code, 200, paused.text)
            self.assertTrue(paused.json()['paused'])
            resumed = client.post('/api/demo/resume', json={}, headers=headers)
            self.assertEqual(resumed.status_code, 200, resumed.text)
            self.assertTrue(resumed.json()['running'])
            self.assertFalse(resumed.json()['paused'])
            self.assertEqual(resumed.json()['elapsed_seconds'], paused.json()['elapsed_seconds'])
            self.assertEqual(self.app.state.demo._run_id, original_run)
            self.assertGreater(resumed.json()['event_count'], paused.json()['event_count'])
            self.assertEqual(client.post('/api/demo/resume', json={}, headers=headers).status_code, 409)

    def test_one_admin_csv_contains_actions_of_all_three_dispatchers(self):
        service = self.app.state.service
        events = [
            {'event_id':'report-logistics', 'event_time':stamp(service.clock()), 'sensor_id':'POS-V1','type':'position','demo':True,
             'payload':{'asset_id':'V1','x':25,'y':50}},
            {'event_id':'report-production', 'event_time':stamp(service.clock()), 'sensor_id':'POS-V2','type':'position','demo':True,
             'payload':{'asset_id':'V2','x':66.5,'y':30}},
            {'event_id':'report-gate', 'event_time':stamp(service.clock()), 'sensor_id':'ACCESS-G1','type':'access','demo':True,
             'payload':{'employee_id':'U4','building_id':'G1','direction':'in','access_kind':'passage_confirmed'}},
        ]
        for event in events:
            service.ingest_event(event)
        for number, kind, asset in ((1,'forbidden_zone','V1'),(2,'forbidden_zone','V2'),(3,'unauthorized_access',None)):
            client, headers = self.login(number)
            incident = next(item for item in service.list_incidents() if item['type'] == kind and (asset is None or item.get('asset_id') == asset))
            response = client.patch('/api/incidents/' + incident['incident_id'], headers=headers,
                json={'action':'claim','expected_revision':incident['dispatch_revision'],'request_id':f'report-claim-{number}'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(client.get('/api/dispatch-history/export.csv?scope=all').status_code, 403)
        admin, headers = self.login(0)
        response = admin.get('/api/dispatch-history/export.csv?scope=all&limit=2000', headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        claims = {row['Диспетчер'] for row in rows if row['Действие'] == 'Принята ответственность'}
        self.assertEqual(claims, {'Диспетчер 1 — логистика','Диспетчер 2 — производство','Диспетчер 3 — КПП'})
        self.assertEqual(response.headers['x-export-limit'], '2000')
        self.assertEqual(admin.get('/api/dispatch-history/export.csv?scope=all&limit=2001').status_code, 422)


if __name__ == '__main__':
    unittest.main()
