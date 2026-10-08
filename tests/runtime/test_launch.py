import argparse
import asyncio
import os
import tempfile
import sqlite3
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import patch

from src.core.launch import ROOT, configure, main


class LaunchTests(unittest.TestCase):
    def database(self, directory):
        path = Path(directory) / 'existing.db'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE auth_users(role TEXT,status TEXT)')
            db.execute("INSERT INTO auth_users VALUES ('admin','active')")
        db.close()
        return str(path)

    def test_ai_env_cannot_replace_database(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / '.env'
            env_file.write_text('DISPATCH_DB=wrong.db\nLOCAL_LLM_MODEL=local-model\n')
            database = self.database(directory)
            args = argparse.Namespace(db=database, with_ai=True, env_file=str(env_file))
            with patch.dict(os.environ, {}, clear=True):
                configure(args)
                self.assertEqual(os.environ['DISPATCH_DB'], database)
                self.assertEqual(os.environ['LOCAL_LLM_MODEL'], 'local-model')
                self.assertEqual(os.environ['DISPATCH_ENABLE_AUTH'], '1')

    def test_explicit_database_wins_and_basic_path_stays_lightweight(self):
        args = argparse.Namespace(db='selected.db', with_ai=False, env_file='missing')
        with patch.dict(os.environ, {'DISPATCH_DB': 'old.db'}, clear=True):
            configure(args)
            self.assertEqual(os.environ['DISPATCH_DB'], str(Path('selected.db').resolve()))
            self.assertNotIn('DISPATCH_ENABLE_AGENT', os.environ)
            self.assertNotIn('DISPATCH_ENABLE_ML', os.environ)

    def test_unsafe_lan_rejected(self):
        with patch.dict(os.environ, {}, clear=True), patch('src.core.launch.uvicorn.run') as run:
            with self.assertRaises(SystemExit):
                main(['--host', '0.0.0.0'])
            run.assert_not_called()

    def test_single_shared_server_defaults(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch('src.core.launch.uvicorn.run') as run:
            main(['--with-ai', '--db', self.database(directory), '--env-file', 'missing'])
            self.assertEqual(run.call_args.args, ('src.core.main:app',))
            self.assertEqual(run.call_args.kwargs['port'], 8000)
            self.assertEqual(run.call_args.kwargs['workers'], 1)
            self.assertFalse(run.call_args.kwargs['proxy_headers'])

    def test_ai_rejects_missing_relative_and_empty_databases(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            for path in (None, 'relative.db', str(Path(directory) / 'missing.db')):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    configure(argparse.Namespace(db=path, with_ai=True, env_file='missing'))
            path = Path(directory) / 'empty.db'
            with closing(sqlite3.connect(path)):
                pass
            with self.assertRaises(ValueError):
                configure(argparse.Namespace(db=str(path), with_ai=True, env_file='missing'))

    def test_check_only_never_starts_server(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), patch('src.core.launch.uvicorn.run') as run:
            main(['--with-ai', '--check-only', '--db', self.database(directory), '--env-file', 'missing'])
            run.assert_not_called()

    def test_qwen_availability_recovers_without_server_restart(self):
        from src.agent.errors import AgentError
        from src.agent.runtime import AgentManager

        class Client:
            available = False
            async def ensure_available(self):
                if not self.available:
                    raise AgentError('unavailable', 'offline', 503)

        async def check():
            manager = object.__new__(AgentManager)
            manager.client = Client()
            manager.runtime_available = True
            await manager.check_availability()
            self.assertFalse(manager.runtime_available)
            manager.client.available = True
            await manager.check_availability()
            self.assertTrue(manager.runtime_available)
        asyncio.run(check())
