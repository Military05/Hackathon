"""Authenticated scenario scope tests; only temporary SQLite, runner calls are mocks."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from tests.runtime.isolated_app import ADMIN_PASSWORD, create_app
from src.core.demo_access import scenario_sectors
from src.core.demo_traffic import SCENARIOS


class ScenarioAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"DISPATCH_ENABLE_AUTH": "1", "DISPATCH_ENABLE_AGENT": "0",
            "DISPATCH_ENABLE_ML": "0", "DISPATCH_ENABLE_SIMULATOR": "0", "DISPATCH_ENABLE_DEMO_TRAFFIC": "1", "DEMO_AUTOSTART": "0"})
        self.env.start()
        self.app = create_app(db_path=Path(self.temp.name) / 'scenarios.db', enable_scheduler=False)
        self.clients = []
        for number in range(1, 4):
            self.app.state.auth.create_user(f'dispatcher.{number}', f'Диспетчер {number}', 'Scenario-password-2026',
                                           operator_id=f'dispatcher-{number}', status='active')

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.env.stop()
        self.temp.cleanup()

    def login(self, number):
        client = TestClient(self.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50200))
        self.clients.append(client)
        result = client.post('/api/auth/login', json={'username': 'admin' if number == 0 else f'dispatcher.{number}',
            'password': ADMIN_PASSWORD if number == 0 else 'Scenario-password-2026'})
        self.assertEqual(result.status_code, 200, result.text)
        return client, {'X-CSRF-Token': result.json()['csrf_token']}

    def test_each_session_gets_only_its_scenarios_even_with_forged_header(self):
        expected = {
            1: {'normal','logistics','forbidden-zone','route-deviation','collision','safe-passing','service-zone',
                'red-zone','orange-zone','orange-authorized','anomaly-oscillation','anomaly-wall','anomaly-erratic'},
            2: {'normal','sensor-offline','production-zone'},
            3: {'normal','service','shift','unauthorized-access'},
        }
        for number in (1, 2, 3):
            client, _ = self.login(number)
            result = client.get('/api/demo/status', headers={'X-Demo-Operator':'admin'})
            self.assertEqual(result.status_code, 200)
            data = result.json()
            self.assertEqual(set(data['scenarios']), expected[number])
            self.assertEqual({option['id'] for option in data['scenario_options']}, expected[number])
            self.assertEqual(data['scenario_operator_id'], f'dispatcher-{number}')
        client, _ = self.login(0)
        self.assertEqual(set(client.get('/api/demo/status').json()['scenarios']), set(SCENARIOS))

    def test_foreign_start_and_stop_are_rejected_before_runner_changes(self):
        client, headers = self.login(2)
        before = self.app.state.demo.scenario
        with patch.object(self.app.state.demo, 'start', new_callable=AsyncMock) as start:
            for scenario in ('red-zone','unauthorized-access','simultaneous'):
                result = client.post('/api/demo/start', json={'scenario':scenario}, headers={**headers, 'X-Demo-Operator':'admin'})
                self.assertEqual(result.status_code, 403, result.text)
                self.assertEqual(result.json()['code'], 'scenario_sector_required')
            start.assert_not_awaited()
            self.assertEqual(self.app.state.demo.scenario, before)
        self.app.state.demo.scenario = 'red-zone'
        with patch.object(self.app.state.demo, 'stop', new_callable=AsyncMock) as stop:
            self.assertFalse(client.get('/api/demo/status').json()['can_stop'])
            self.assertEqual(client.post('/api/demo/stop', json={}, headers=headers).status_code, 403)
            stop.assert_not_awaited()
        self.app.state.demo.scenario = 'production-zone'
        with patch.object(self.app.state.demo, 'stop', new_callable=AsyncMock, return_value={'running':False}) as stop:
            self.assertTrue(client.get('/api/demo/status').json()['can_stop'])
            self.assertEqual(client.post('/api/demo/stop', json={}, headers=headers).status_code, 200)
            stop.assert_awaited_once()

    def test_own_start_is_allowed_but_csrf_still_required(self):
        for number, scenario in ((1,'red-zone'),(2,'production-zone'),(3,'unauthorized-access'),(0,'simultaneous')):
            client, headers = self.login(number)
            with patch.object(self.app.state.demo, 'start', new_callable=AsyncMock, return_value={'scenario':scenario}) as start:
                self.assertEqual(client.post('/api/demo/start', json={'scenario':scenario}).status_code, 403)
                start.assert_not_awaited()
                result = client.post('/api/demo/start', json={'scenario':scenario}, headers=headers)
                self.assertEqual(result.status_code, 200, result.text)
                start.assert_awaited_once_with(scenario)

    def test_zone_target_tracks_actual_map_owner_not_vehicle_owner(self):
        service = self.app.state.service
        self.assertEqual(scenario_sectors(service, 'service-zone'), ['logistics'])
        zone = next(row for row in service.site['zones'] if row['id'] == 'Z4')
        zone['site_area_id'] = 'workshop-1'
        self.assertEqual(scenario_sectors(service, 'service-zone'), ['production'])
        self.assertEqual(scenario_sectors(service, 'unknown-new-scenario'), [])
