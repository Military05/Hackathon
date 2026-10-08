"""Настоящие movement-v2 и Qwen на временной базе; рабочая база не открывается."""
import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core.service import Service, stamp
from src.ml.movement import MovementModel
from src.agent.backend import capture_snapshot
from src.agent.config import AgentConfig, load_env_file
from src.agent.model_client import LocalModelClient
from src.agent.providers import CaptureProvider, FrozenSnapshot
from src.agent.service import AgentService


async def check(env_file):
    load_env_file(env_file)
    with tempfile.TemporaryDirectory(prefix='contour-evidence-live-') as directory:
        current = datetime.now(timezone.utc).replace(microsecond=0)
        base = current-timedelta(seconds=300)
        clock = base
        backend = Service(Path(directory)/'test.db', ROOT/'data/demo/site.json', clock=lambda:clock)
        model = MovementModel(backend, ROOT/'models/movement-v1/movement.joblib')
        if model.health()['status'] != 'ready':
            raise RuntimeError('MLP недоступна: '+str(model.health()))
        statuses = []
        for n in range(260):
            clock = base+timedelta(seconds=n)
            x,y = (50+(n%2)*2,49) if n<200 else (30+(n-200)*.1,60)
            backend.ingest_event({'event_id':f'live-{n:03}','event_time':stamp(clock),
                'sensor_id':'POS-V1','type':'position','demo':True,'payload':{'asset_id':'V1','x':x,'y':y}})
            if n>=30 and int(clock.timestamp())%5==0:
                value=model.evaluate('V1',[],stamp(clock))
                backend.register_model_observation(value);statuses.append(value['status'])
        incident=next(row for row in backend.list_incidents() if row['type']=='model_anomaly')
        def domain():
            with backend.store.read() as db:
                return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
                        for table in ('events','incidents','model_observations','dispatch_history')}
        before=domain()
        frozen=FrozenSnapshot(capture_snapshot(backend,incident['incident_id']))
        if not frozen.data['history_bounds'].get('evidence_selection',{}).get('partial'):
            raise RuntimeError('Сценарий не воспроизвёл переполнение доказательств')
        config=replace(AgentConfig.from_env(),database=backend.store.path)
        replies=[]
        class RecordingClient(LocalModelClient):
            async def chat(self,*args,**kwargs):
                reply=await super().chat(*args,**kwargs);replies.append(reply);return reply
        agent=AgentService(config,CaptureProvider(lambda identifier:capture_snapshot(backend,identifier)),RecordingClient(config))
        await agent.start()
        try:
            admitted=await agent.request(incident['incident_id'],'dispatcher-1')
            deadline=time.monotonic()+65
            while time.monotonic()<deadline:
                job=await agent.get(admitted['job_id'])
                if job['status'] in ('completed','failed'):break
                await asyncio.sleep(.2)
            if job['status']!='completed' or job['stale']:
                return {'status':'FAIL','error':'Анализ не завершился достоверно','job':job,
                        'diagnostic_replies':replies,'mlp':model.health(),'snapshot':frozen.export()}
            repeated=await agent.request(incident['incident_id'],'dispatcher-1')
            if not repeated['cached'] or repeated['job_id']!=job['job_id'] or domain()!=before:
                raise RuntimeError('Нарушена неизменность данных или повторное использование результата')
            return {'status':'PASS','real_qwen':True,'mlp':model.health(),'anomaly_windows':statuses.count('anomaly'),
                    'normal_windows':statuses.count('normal'),'incident_state':incident['condition_state'],
                    'domain_unchanged':True,'cached':True,'job':job,
                    'selection':frozen.data['history_bounds']['evidence_selection']}
        finally:await agent.stop()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    started=time.monotonic()
    try:report=asyncio.run(check(args.env_file))
    except Exception as error:report={'status':'FAIL','error':str(error)}
    report['seconds']=round(time.monotonic()-started,3)
    destination=Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({key:report.get(key) for key in ('status','error','seconds','mlp','anomaly_windows','normal_windows')},ensure_ascii=False))
    return 0 if report['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
