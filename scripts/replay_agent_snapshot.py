"""Повтор AI-задания на сохранённом срезе и новой тестовой SQLite-базе."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent.config import AgentConfig
from src.agent.model_client import LocalModelClient
from src.agent.providers import CaptureProvider, FrozenSnapshot
from src.agent.service import AgentService


async def replay(args):
    database = Path(args.database)
    if not database.is_absolute() or database.exists():
        raise ValueError('Укажите абсолютный путь к новой тестовой базе; существующие базы запрещены')
    snapshot = FrozenSnapshot(json.loads(Path(args.snapshot).read_text(encoding='utf-8')))
    # Reproduce the captured journal epoch only in the new disposable database.
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('INSERT INTO metadata VALUES (?, ?)', ('incident_log_epoch',
                   str(snapshot.data.get('incident_context', {}).get('log_epoch', 0))))
    async def capture(incident_id):
        if incident_id != snapshot.incident_id:
            raise ValueError('Неверное происшествие')
        return snapshot
    config = replace(AgentConfig.from_env(), database=str(database), model=args.model)
    wire = []
    class RecordingClient(LocalModelClient):
        async def _json(self, *arguments, **keywords):
            data = await super()._json(*arguments, **keywords)
            if 'choices' in data:
                wire.append(data)
            return data
    service = AgentService(config, CaptureProvider(capture), RecordingClient(config))
    started = time.monotonic()
    await service.start()
    try:
        job = await service.request(snapshot.incident_id, 'dispatcher-1')
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            job = await service.get(job['job_id'])
            if job['status'] in ('completed', 'failed'):
                break
            await asyncio.sleep(.2)
        report = {'status': 'PASS' if job['status'] == 'completed' else 'FAIL',
                  'database': str(database), 'job_id': job['job_id'], 'job_status': job['status'],
                  'stale': job['stale'], 'error': job['error'],
                  'seconds': time.monotonic() - started,
                  'verified_facts': len((job.get('result') or {}).get('facts', [])),
                  'production_database_accessed': False}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['status'] == 'PASS' else 1
    finally:
        await service.stop()
        if args.diagnostics:
            Path(args.diagnostics).write_text(json.dumps(wire, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--model', default='hackathon-qwen3-4b')
    parser.add_argument('--diagnostics', help='Локальный файл ответов тестового провайдера; не публиковать в Git')
    try:
        raise SystemExit(asyncio.run(replay(parser.parse_args())))
    except Exception as error:
        raise SystemExit(f'Ошибка тестового повтора: {error}')
