"""The base server must work without separately owned extensions."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from src.core.main import create_app


class ExtensionBoundaryTests(unittest.TestCase):
    def test_base_server_uses_builtin_traffic_without_simulator_ml_or_agent(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            'DISPATCH_ENABLE_SIMULATOR': '0', 'DISPATCH_ENABLE_AGENT': '0',
            'DISPATCH_ENABLE_ML': '0', 'DISPATCH_ENABLE_DEMO_TRAFFIC': '1',
            'DEMO_AUTOSTART': '1',
        }):
            with TestClient(create_app(enable_auth=False, db_path=Path(temp)/'demo.db')) as client:
                health = client.get('/api/health').json()
                self.assertEqual(health['rules']['status'], 'ready')
                self.assertEqual(health['agent']['status'], 'unavailable')
                self.assertEqual(health['ml']['status'], 'unavailable')
                status = client.get('/api/demo/status').json()
                self.assertTrue(status['available'])
                self.assertTrue(status['running'])
                self.assertEqual(status['source'], 'builtin_factory_traffic')
                self.assertEqual(status['source_count'], 6)
                self.assertIn('normal', status['scenarios'])
                self.assertEqual(len(client.get('/api/assets').json()), 7)
                response = client.post('/api/demo/start', json={'scenario': 'normal'},
                                       headers={'X-Demo-Operator':'dispatcher-1'})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['running'])
                self.assertEqual(client.get('/api/site').status_code, 200)
                self.assertEqual(client.get('/api/dispatch-summary').status_code, 200)

    def test_error_shapes_are_json_and_unknown_profiles_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with TestClient(create_app(enable_auth=False, db_path=Path(temp)/'demo.db', enable_scheduler=False)) as client:
                invalid = client.post('/api/events', json=[])
                self.assertEqual(invalid.status_code, 422)
                self.assertEqual(invalid.json()['code'], 'validation_error')
                unknown = client.post('/api/demo/start', json={'scenario': ['normal']},
                                      headers={'X-Demo-Operator':'unknown'})
                self.assertEqual(unknown.status_code, 404)


if __name__ == '__main__':
    unittest.main()
