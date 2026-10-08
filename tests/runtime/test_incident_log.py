"""Deletion is tested only against a newly created temporary SQLite database."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from src.core.main import create_app
from src.core.incident_log import preview, clear
from src.core.service import ApiError
from src.agent.backend import capture_snapshot
from src.agent.providers import FrozenSnapshot
from src.agent.config import AgentConfig
from src.agent.store import JobStore
from src.agent.errors import AgentError
from tests.agent.test_tools_and_result import snapshot

class IncidentLogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{"DISPATCH_ENABLE_AUTH":"1","DISPATCH_ENABLE_AGENT":"0","DISPATCH_ENABLE_ML":"0","DISPATCH_ENABLE_DEMO_TRAFFIC":"0"})
        self.env.start()
        self.path=Path(self.temp.name)/"test.db"
        self.app=create_app(db_path=self.path,enable_scheduler=False)
        self.service=self.app.state.service
        self.app.state.auth.create_user("admin","Администратор","Admin-password-2026",role="admin",status="active")
        self.app.state.auth.create_user("operator","Диспетчер","Operator-password-2026",operator_id="dispatcher-1",status="active")
        self.client=TestClient(self.app,base_url="http://127.0.0.1:8000",client=("127.0.0.1",50100));self.client.__enter__()
        current=snapshot().data['incident']
        with self.service.store.transaction() as db:
            db.execute("INSERT INTO events VALUES ('raw','{}','time','time','sensor','position','V1','{}')")
            for identifier,status in [('active','open'),('closed','closed')]:
                value=dict(current,incident_id=identifier,status=status)
                db.execute('INSERT INTO incidents VALUES (?,?,?)',(identifier,identifier,json.dumps(value)))
                db.execute('INSERT INTO dispatch_history VALUES (?,?,?)',('note-'+identifier,identifier,json.dumps({'reason':'Водитель сообщил о погрузке','actor_operator_id':'dispatcher-1','action':'record_response','created_at':current['detected_at']})))
                db.execute('INSERT INTO requests VALUES (?,?,?,?,?)',(identifier,'dispatcher-1','request','{}','{}'))
                db.execute('INSERT INTO transfers VALUES (?,?,?)',('transfer-'+identifier,identifier,'{}'))
                db.execute('INSERT INTO notifications(dedupe_key,incident_id,recipient_operator_id,body) VALUES (?,?,?,?)',(identifier,identifier,'dispatcher-1','{}'))
    def tearDown(self):
        self.client.__exit__(None,None,None);self.env.stop();self.temp.cleanup()
    def login(self,admin=True):
        r=self.client.post('/api/auth/login',json={'username':'admin' if admin else 'operator','password':'Admin-password-2026' if admin else 'Operator-password-2026'})
        self.assertEqual(r.status_code,200,r.text)
        return {'X-CSRF-Token':r.json()['csrf_token']}
    def body(self):
        return {'confirmation':'DELETE_INCIDENT_LOG','token':preview(self.service)['token']}
    def test_admin_confirmed_clear_deletes_active_and_closed_records_preserves_accounts(self):
        headers=self.login()
        p=self.client.get('/api/admin/incident-log/preview').json()
        self.assertEqual(p['count'],2);self.assertIn('активные',p['warning']);self.assertIn('ИИ',p['warning'])
        r=self.client.post('/api/admin/incident-log/clear',json=self.body(),headers=headers)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['deleted'],2)
        with self.service.store.read() as db:
            for table in ('incidents','dispatch_history','requests','transfers','notifications'):
                self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],0,table)
            self.assertEqual(db.execute('SELECT count(*) FROM auth_users').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
        self.assertTrue(self.path.exists())
    def test_permissions_csrf_confirmation_and_changed_log(self):
        self.assertEqual(self.client.get('/api/admin/incident-log/preview').status_code,401)
        self.login(False);self.assertEqual(self.client.get('/api/admin/incident-log/preview').status_code,403)
        headers=self.login()
        self.assertEqual(self.client.post('/api/admin/incident-log/clear',json=self.body()).status_code,403)
        self.assertEqual(self.client.post('/api/admin/incident-log/clear',json={'token':'x'},headers=headers).status_code,422)
        body=self.body()
        with self.service.store.transaction() as db:
            db.execute("INSERT INTO incidents VALUES ('new','new','{}')")
        self.assertEqual(self.client.post('/api/admin/incident-log/clear',json=body,headers=headers).status_code,409)
        self.assertEqual(preview(self.service)['count'],3)
    def test_busy_analysis_blocks_clear_and_old_snapshot_cannot_reappear(self):
        store=JobStore(self.path,AgentConfig(database=str(self.path),model='explicit-test-double'))
        data=snapshot().export();data['incident_context']={'log_epoch':0}
        frozen=FrozenSnapshot(data)
        job,_=store.enqueue(frozen,'key','dispatcher-1')
        with self.assertRaises(ApiError) as caught:clear(self.service,self.body())
        self.assertEqual(caught.exception.code,'analysis_in_progress')
        store.take_next();store.finish(job['job_id'],error={'code':'fixture'})
        clear(self.service,self.body())
        with self.service.store.read() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM b1_agent_jobs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM b1_agent_requesters').fetchone()[0],0)
        with self.assertRaises(AgentError) as caught:store.enqueue(frozen,'old','dispatcher-1')
        self.assertEqual(caught.exception.code,'analysis_data_changed')
    def test_preview_is_stable_during_sensor_updates(self):
        body=self.body()
        with self.service.store.transaction() as db:
            db.execute("UPDATE incidents SET body=json_set(body,'$.dispatch_revision',99) WHERE incident_id='active'")
        self.assertEqual(clear(self.service,body)['deleted'],2)

    def test_snapshot_history_filters_object_place_and_date_and_reads_dispatcher_notes(self):
        from datetime import datetime, timezone, timedelta
        from src.core.service import stamp
        self.service.clock=lambda:datetime(2026,10,8,12,tzinfo=timezone.utc)
        with self.service.store.transaction() as db:
            db.execute("DELETE FROM events WHERE event_id='raw'")
            current=json.loads(db.execute("SELECT body FROM incidents WHERE incident_id='active'").fetchone()[0])
            current['detected_at']=stamp(self.service.clock())
            current['evidence_event_ids']=[]
            db.execute("UPDATE incidents SET body=? WHERE incident_id='active'",(json.dumps(current),))
            for identifier,changes in [('closed',{}),('other',{'asset_id':'V2'}),('place',{'zone_id':'different'}),('old',{'detected_at':stamp(self.service.clock()-timedelta(days=40))})]:
                row=dict(current,incident_id=identifier,detected_at=stamp(self.service.clock()-timedelta(days=1)),**{})
                row.update(changes)
                db.execute('INSERT OR REPLACE INTO incidents VALUES (?,?,?)',(identifier,identifier,json.dumps(row)))
        value=capture_snapshot(self.service,'active')
        self.assertEqual([row['incident_id'] for row in value['incident_context']['related_incidents']],['closed'])
        self.assertEqual(len(value['incident_context']['dispatcher_notes']),1)
        frozen=FrozenSnapshot(value)
        oldclock=self.service.clock();self.service.clock=lambda:oldclock+timedelta(seconds=1)
        self.assertEqual(frozen.snapshot_id,FrozenSnapshot(capture_snapshot(self.service,'active')).snapshot_id)
