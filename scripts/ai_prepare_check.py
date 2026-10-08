"""Проверка интеграции только на отдельной копии резервной базы."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def state(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        users = list(db.execute('SELECT * FROM auth_users ORDER BY id'))
        protected = ('auth_users', 'auth_sessions', 'auth_audit', 'events', 'incidents',
                     'dispatch_history', 'requests', 'transfers', 'notifications',
                     'ops_shifts', 'ops_shift_events', 'model_observations')
        digest = hashlib.sha256()
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in protected:
            if table in tables:
                digest.update(table.encode())
                for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid'):
                    digest.update(repr(row).encode())
        return {'accounts': len(users),
                'active_admins': db.execute("SELECT COUNT(*) FROM auth_users WHERE role='admin' AND status='active'").fetchone()[0],
                'active_dispatchers': db.execute("SELECT COUNT(*) FROM auth_users WHERE role='dispatcher' AND status='active'").fetchone()[0],
                'events': db.execute('SELECT COUNT(*) FROM events').fetchone()[0],
                'incidents': db.execute('SELECT COUNT(*) FROM incidents').fetchone()[0],
                'protected_digest': digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--main-copy', required=True)
    parser.add_argument('--output', default=str(ROOT / 'artifacts/local/ai-preparation.json'))
    args = parser.parse_args()
    backup = Path(args.backup).resolve(strict=True)
    original_hash = sha(backup)
    if original_hash != args.expected_sha256.lower():
        raise RuntimeError('SHA256 резервной базы не совпадает')
    clone = ROOT / ('artifacts/local/preparation-validation-' + uuid.uuid4().hex + '.db')
    clone.parent.mkdir(parents=True, exist_ok=True)
    if clone.exists():
        raise RuntimeError('Проверочная база уже существует: укажите новый checkout для повторной проверки')
    with closing(sqlite3.connect(backup.as_uri() + '?mode=ro', uri=True)) as source:
        with closing(sqlite3.connect(clone)) as destination:
            source.backup(destination)
    before = state(clone)
    if before['active_admins'] < 1:
        raise RuntimeError('В резервной базе нет активного администратора')
    identical = []
    for relative in ('src/core/main.py', 'src/core/auth.py', 'src/core/service.py',
                     'src/core/operations.py', 'src/core/demo_traffic.py', 'src/storage/sqlite_store.py', 'data/demo/site.json',
                     'models/movement-v1/movement.joblib', 'models/movement-v1/movement.metadata.json'):
        if sha(ROOT / relative) != sha(Path(args.main_copy) / relative):
            raise RuntimeError('Несовместимая основная копия: ' + relative)
        identical.append(relative)
    from src.core.launch import configure
    configure(argparse.Namespace(db=str(clone), with_ai=True, env_file=str(ROOT / '.env')))
    os.environ['DEMO_AUTOSTART'] = '0'
    from src.core.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(db_path=clone, enable_scheduler=False)
    with TestClient(app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50100)) as client:
        health = client.get('/api/health').json()
        if health['ml']['status'] != 'ready' or health['agent']['status'] != 'ready':
            raise RuntimeError('MLP или Qwen недоступны: ' + json.dumps(health, ensure_ascii=False))
        if not health['auth']['enabled'] or not health['auth']['configured']:
            raise RuntimeError('Авторизация не сохранена')
        if client.get('/api/site').status_code != 401:
            raise RuntimeError('Чтение без сессии не заблокировано')
        if Path(app.state.service.store.path).resolve() != clone:
            raise RuntimeError('Приложение выбрало другую базу')
        events = app.state.service.event_history('V1', '2000-01-01T00:00:00Z', '2100-01-01T00:00:00Z', 100)
        model_type = type(app.state.ml.pipeline).__name__
        observation = app.state.ml.evaluate('V1', events, events[-1]['event_time']) if events else None
    after = state(clone)
    if before != after or sha(backup) != original_hash:
        raise RuntimeError('Аккаунты или история изменились во время проверки')
    with closing(sqlite3.connect(clone)) as db:
        quick_check = db.execute('PRAGMA quick_check').fetchone()[0]
    if quick_check != 'ok':
        raise RuntimeError('Проверочная база повреждена')
    report = {'status': 'PASS', 'backup_sha256': original_hash, 'clone': str(clone),
              'preserved': {key: value for key, value in after.items() if key != 'protected_digest'},
              'accounts_and_history_unchanged': True, 'quick_check': quick_check,
              'identical_to_main_copy': identical, 'health': health,
              'ml_runtime': model_type, 'ml_device': 'CPU', 'history_read_count': len(events),
              'inference_on_saved_history': ({key: observation.get(key) for key in ('status', 'score', 'threshold')}
                                             if observation else None),
              'real_llm_generation': False, 'production_servers_restarted': False}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit(f'Ошибка подготовки: {error}')
