"""Deployment preparation is exercised only on temporary databases, no processes stopped."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from tests.runtime.isolated_app import create_app

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/device_runtime.py'
spec = importlib.util.spec_from_file_location('device_runtime_for_tests', SCRIPT)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class DeviceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.database = self.base / 'existing.db'
        with patch.dict(os.environ, {'DISPATCH_ENABLE_AGENT':'0','DISPATCH_ENABLE_ML':'0','DISPATCH_ENABLE_DEMO_TRAFFIC':'0'}):
            create_app(db_path=self.database, enable_scheduler=False)
        self.report = self.base / 'diagnostics/report.json'
        self.environment = self.base / 'diagnostics/environment.bin'
        self.settings = patch.multiple(runtime, DATABASE=self.database, REPORT=self.report,
            ENVIRONMENT=self.environment, BACKUP_DIR=self.base / 'backups')
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        self.temp.cleanup()

    def test_backup_api_preserves_original_and_checks_accounts(self):
        original = self.database.read_bytes()
        # DPAPI is replaced only for a platform-independent preparation test.
        with patch.object(runtime, 'crypt', side_effect=lambda data, decrypt=False:data):
            runtime.prepare(None)
        result = json.loads(self.report.read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'prepared')
        self.assertEqual(result['backup_check'], 'ok')
        self.assertEqual(result['before']['active_admins'], 1)
        self.assertEqual(original, self.database.read_bytes())
        with closing(sqlite3.connect(result['backup'])) as db:
            self.assertEqual(runtime.inventory(db), result['before'])
        self.assertNotIn('password_hash', self.report.read_text(encoding='utf-8'))

    def test_pending_analysis_aborts_before_backup_or_report(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('CREATE TABLE b1_agent_jobs(status TEXT)')
            db.execute("INSERT INTO b1_agent_jobs VALUES ('running')")
            db.commit()
        with patch.object(runtime, 'process_settings', return_value={'cwd':str(self.base)}):
            with self.assertRaisesRegex(RuntimeError, 'незавершённые'):
                runtime.prepare(123)
        self.assertFalse(self.report.exists())
        self.assertFalse(list((self.base / 'backups').glob('*.db')))

    def test_missing_database_is_not_created(self):
        missing = self.base / 'missing.db'
        with patch.object(runtime, 'DATABASE', missing), patch.object(runtime, 'process_settings', return_value={}):
            with self.assertRaisesRegex(RuntimeError, 'не найдена'):
                runtime.prepare(123)
        self.assertFalse(missing.exists())

    def test_account_digest_detects_password_change_without_exposing_it(self):
        with closing(sqlite3.connect(self.database)) as db:
            before = runtime.inventory(db)
            db.execute("UPDATE auth_users SET password_hash='changed' WHERE role='admin'")
            after = runtime.inventory(db)
        self.assertNotEqual(before['accounts_digest'], after['accounts_digest'])
        self.assertNotIn('password_hash', after)

    @unittest.skipUnless(os.name == 'nt', 'DPAPI требует Windows')
    def test_windows_protected_environment_roundtrip(self):
        value = b'{"safe":"test"}'
        self.assertEqual(runtime.crypt(runtime.crypt(value), decrypt=True), value)
