"""Explicit administrator-confirmed deletion of the incident log, including active cases."""
import hashlib
import json

from src.core.service import ApiError


WARNING = ('Происшествия, их заметки и сохранённые анализы будут удалены из SQLite. '
           'Восстановить их через интерфейс нельзя; ИИ больше не сможет анализировать эти случаи. '
           'Будут удалены в том числе активные происшествия. При продолжающемся нарушении новые сигналы '
           'могут снова создать тревогу. Аккаунты, исходные сигналы датчиков и сам файл базы сохраняются.')


def candidates(db):
    rows = db.execute('SELECT incident_id,body FROM incidents ORDER BY incident_id LIMIT 2001').fetchall()
    if len(rows) > 2000:
        raise ApiError(409, 'log_too_large', 'Для очистки большого журнала требуется отдельное обслуживание.')
    token = hashlib.sha256(json.dumps([row['incident_id'] for row in rows],
                                     ensure_ascii=False).encode()).hexdigest()
    return rows, token


def preview(service):
    with service.store.read() as db:
        rows, token = candidates(db)
    return {'count': len(rows), 'token': token, 'warning': WARNING, 'scope': 'all_incidents'}


def clear(service, body):
    if not isinstance(body, dict) or set(body) != {'confirmation', 'token'} or body['confirmation'] != 'DELETE_INCIDENT_LOG':
        raise ApiError(422, 'confirmation_required', 'Подтвердите предупреждение об окончательном удалении журнала.')
    with service.store.transaction() as db:
        rows, token = candidates(db)
        if body['token'] != token:
            raise ApiError(409, 'log_changed', 'Журнал изменился. Просмотрите предупреждение и подтвердите очистку заново.')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'b1_agent_jobs' in tables and db.execute("SELECT 1 FROM b1_agent_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone():
            raise ApiError(409, 'analysis_in_progress', 'Дождитесь завершения AI-анализа перед очисткой журнала.')
        db.execute('CREATE TEMP TABLE deleting_incidents (id TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO deleting_incidents VALUES (?)', [(row['incident_id'],) for row in rows])
        if 'b1_agent_jobs' in tables:
            db.execute('DELETE FROM b1_agent_requesters')
            db.execute('DELETE FROM b1_agent_jobs')
        for table in ('dispatch_history', 'requests', 'transfers', 'notifications', 'incidents'):
            db.execute(f'DELETE FROM {table} WHERE incident_id IN (SELECT id FROM deleting_incidents)')
        if rows:
            db.execute("INSERT INTO metadata VALUES ('incident_log_epoch','1') ON CONFLICT(key) "
                       "DO UPDATE SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT)")
    return {'deleted': len(rows), 'scope': 'all_incidents'}
