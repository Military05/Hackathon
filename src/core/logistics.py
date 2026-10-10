"""Bounded regional logistics demo; geographic roads stay separate from factory x/y."""
from __future__ import annotations
import asyncio
import copy
import csv
import io
import json
import logging
import math
import time
from collections import OrderedDict
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response

log = logging.getLogger('dispatch.logistics')
ROUTING_URL = 'https://valhalla1.openstreetmap.de/route'
SCENARIOS = [
    {'id':'normal','name':'Обычная доставка','description':'Проверка, очередь, погрузка, движение и приёмка.'},
    {'id':'dock_queue','name':'Очередь к рампе','description':'Пять машин обслуживаются одной рампой завода.'},
    {'id':'overload','name':'Перегруз','description':'Вес первой машины превышает грузоподъёмность.'},
    {'id':'wrong_cargo','name':'Неверный груз','description':'На КПП обнаружено несоответствие накладной.'},
    {'id':'road_closure','name':'Перекрытие дороги','description':'Участок действующего маршрута закрывается; требуется объезд.'},
    {'id':'no_detour','name':'Объезд отсутствует','description':'Демонстрационная недоступность объезда независимо от провайдера.'},
    {'id':'route_deviation','name':'Отклонение от маршрута','description':'Машина выбирает другое дорожное направление; контроль останавливает её.'},
    {'id':'gps_loss','name':'Потеря GPS','description':'GPS первой машины пропадает; движение останавливается.'},
]
STATUS_NAMES = {'gate_check':'проверка КПП','queued':'очередь','loading':'погрузка','in_transit':'в пути','unloading':'разгрузка','delivered':'доставлен','blocked':'заблокирован','awaiting_route':'ожидание дорожного маршрута'}
ACTION_NAMES = {'start':'запуск','pause':'пауза','resume':'продолжение','reset':'сброс','speed':'изменение скорости'}
EVENT_NAMES = {'control':'Управление','trip_status':'Этап рейса','loaded':'Погрузка','delivered':'Доставка','route_ready':'Маршрут рассчитан','route_recalculation':'Пересчёт маршрута','trip_created':'Создание рейса','facility_added':'Новый объект','road_closure':'Перекрытие дороги','road_reopened':'Открытие дороги','gps_loss':'Потеря GPS','gps_restored':'Восстановление GPS','gps_disabled':'Отключение GPS','overload':'Перегруз','wrong_cargo':'Несоответствие груза','dock_queue':'Очередь к рампе','no_detour':'Нет объезда','route_unavailable':'Маршрут недоступен','route_deviation':'Отклонение от маршрута','incident_resolved':'Проверка завершена','inspection':'Осмотр','restored':'Восстановление состояния','wrong_turn':'Неверное направление','stock_received':'Поступление товара','stock_relocated':'Перенос товара'}

def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00','Z')

def identifier(prefix):
    return prefix + '-' + uuid4().hex[:12]

def distance(a,b):
    lon1,lat1,lon2,lat2 = map(math.radians,(a[0],a[1],b[0],b[1]))
    value = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742*math.asin(min(1.0,math.sqrt(value)))

def path_lengths(points):
    lengths = [0.0]
    for a,b in zip(points,points[1:]):
        lengths.append(lengths[-1]+distance(a,b))
    return lengths

def path_point(points,progress):
    lengths = path_lengths(points)
    target = lengths[-1]*min(1.0,max(0.0,progress))
    lo,hi = 0,len(lengths)-1
    while lo+1 < hi:
        mid = (lo+hi)//2
        if lengths[mid] <= target:
            lo = mid
        else:
            hi = mid
    a,b = points[lo],points[hi]
    part = (target-lengths[lo])/max(1e-9,lengths[hi]-lengths[lo])
    position = [a[0]+(b[0]-a[0])*part,a[1]+(b[1]-a[1])*part]
    heading = math.degrees(math.atan2((b[0]-a[0])*math.cos(math.radians(a[1])),b[1]-a[1]))%360
    return position,heading

def near_path(position,points):
    scale,best = math.cos(math.radians(position[1])),float('inf')
    for a,b in zip(points,points[1:]):
        ax,ay = (a[0]-position[0])*scale,a[1]-position[1]
        bx,by = (b[0]-position[0])*scale,b[1]-position[1]
        dx,dy = bx-ax,by-ay
        ratio = max(0.0,min(1.0,-(ax*dx+ay*dy)/max(1e-16,dx*dx+dy*dy)))
        best = min(best,math.hypot(ax+ratio*dx,ay+ratio*dy)*111.2)
    return best

def inside(point,polygon):
    x,y = point
    result = False
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        if (a[1]>y)!=(b[1]>y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            result = not result
    return result

def cross(a,b,c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])

def intersects(a,b,c,d):
    if max(a[0],b[0])<min(c[0],d[0]) or max(c[0],d[0])<min(a[0],b[0]) or max(a[1],b[1])<min(c[1],d[1]) or max(c[1],d[1])<min(a[1],b[1]):
        return False
    return cross(a,b,c)*cross(a,b,d)<=0 and cross(c,d,a)*cross(c,d,b)<=0

def blocked(points,polygons):
    for polygon in polygons:
        if any(inside(point,polygon) for point in points):
            return True
        for a,b in zip(points,points[1:]):
            if any(intersects(a,b,c,d) for c,d in zip(polygon,polygon[1:]+polygon[:1])):
                return True
    return False

def decode_polyline(value):
    points,index,lat,lon = [],0,0,0
    while index < len(value):
        delta = []
        for _ in range(2):
            number,shift = 0,0
            while True:
                if index>=len(value) or shift>35:
                    raise ValueError('Некорректная дорожная геометрия')
                byte = ord(value[index])-63
                index += 1
                number |= (byte&31)<<shift
                shift += 5
                if byte<32:
                    break
            delta.append(~(number>>1) if number&1 else number>>1)
        lat += delta[0]
        lon += delta[1]
        points.append([lon/1e6,lat/1e6])
        if len(points)>12000:
            raise ValueError('Маршрут слишком велик для региональной демонстрации')
    if len(points)<2:
        raise ValueError('Нет дорожной геометрии')
    return points

def number(value,name,low,high):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise HTTPException(422,detail=f'{name}: диапазон {low}–{high}')
    return float(value)

class LogisticsNetwork:
    def __init__(self,service):
        self.service = service
        self.lock = asyncio.Lock()
        self.routing_lock = asyncio.Lock()
        self.client = None
        self.timer = None
        self.tasks = set()
        self.route_tasks = {}
        self.cache = OrderedDict()
        self.next_request = 0.0
        self.generation = 0
        self.last_save = 0.0
        self.data = self.seed('normal')

    def seed(self,scenario):
        points = [('factory','Автозавод · Подольск','factory',55.4185,37.5671,2),('moscow','Москва · центральный склад','warehouse',55.7427,37.6256,2),('podolsk','Подольск · поставщик комплектующих','supplier',55.4317,37.5468,1),('obninsk','Обнинск · распределительный центр','warehouse',55.1068,36.6116,1),('kaluga','Калуга · склад поставщика','supplier',54.5293,36.2725,2)]
        facilities = [{'id':fid,'name':name,'kind':kind,'lat':lat,'lon':lon,'docks':(1 if scenario=='dock_queue' and fid=='factory' else docks),'capacity':1500,'stock':500 if fid=='factory' else 150,'queue':[]} for fid,name,kind,lat,lon,docks in points]
        vehicles,trips = [],[]
        for index,destination in enumerate(('moscow','obninsk','kaluga','podolsk','moscow'),1):
            vid,tid = f'truck-{index}',f'trip-{index}'
            vehicles.append({'id':vid,'name':f'Грузовик {index}','plate':f'А{100+index}КТ 77','type':'truck','capacity':20,'load':0,'position':[facilities[0]['lon'],facilities[0]['lat']],'heading':0,'speed_kmh':0,'trip_id':tid,'status':'gate_check','gps_age':0,'gps_available':True})
            trips.append(self.new_trip(tid,vid,'factory',destination,{'name':'Автокомплектующие','quantity':20,'weight_tonnes':12}))
        if scenario=='overload':
            trips[0]['cargo']['weight_tonnes'] = 26
        if scenario=='wrong_cargo':
            trips[0]['cargo']['name'] = 'Груз без согласованной накладной'
            trips[0]['cargo_valid'] = False
        now = time.time()
        return {'demo':True,'running':False,'paused':False,'scenario':scenario,'speed':60,'revision':1,'clock':stamp(now),'facilities':facilities,'vehicles':vehicles,'trips':trips,'incidents':[],'events':[],'closures':[],'scenarios':copy.deepcopy(SCENARIOS),'routing_status':{},'clock_seconds':now,'scenario_injected':False}

    @staticmethod
    def new_trip(tid,vid,origin,destination,cargo):
        return {'id':tid,'vehicle_id':vid,'origin_id':origin,'destination_id':destination,'route':{'coordinates':[],'distance_km':0,'duration_minutes':0,'provider':'Valhalla','quality':'unavailable'},'progress':0.0,'status':'gate_check','eta':None,'route_revision':0,'cargo':cargo,'loading_progress':0.0,'unloading_progress':0.0,'stage_elapsed':0.0,'cargo_valid':True,'stock_loaded':False,'stock_delivered':False,'routing_pending':False,'blocked_reason':None,'resume_status':'gate_check','simulated_no_detour':False,'movement_route':None,'deviation_confirmations':0}

    async def start(self):
        if self.timer:
            return
        with self.service.store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS logistics_network_snapshot (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, body TEXT NOT NULL)')
            row = db.execute('SELECT version,body FROM logistics_network_snapshot WHERE id=1').fetchone()
        if row and row['version']==1:
            try:
                restored = json.loads(row['body'])
                if not isinstance(restored,dict) or not all(k in restored for k in ('trips','vehicles','facilities','clock_seconds')):
                    raise ValueError('Неполный снимок')
                self.data = restored
                self.data['scenarios'] = copy.deepcopy(SCENARIOS)
                for trip in self.data['trips']:
                    trip['routing_pending'] = False
                self.event('restored','Состояние восстановлено; время простоя не ускоряет доставку')
            except (ValueError,TypeError,KeyError):
                log.exception('Cannot restore regional logistics snapshot')
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(9.0,connect=4.0),follow_redirects=False,headers={'User-Agent':'Kontur-Regional-Logistics-Demo/1.0'})
        async with self.lock:
            for trip in self.data['trips']:
                if trip['status']!='delivered' and trip['route'].get('quality')!='road':
                    self.schedule(trip)
            self.save()
        self.timer = asyncio.create_task(self.run(),name='regional-logistics-clock')

    async def shutdown(self):
        if self.timer:
            self.timer.cancel()
            with suppress(asyncio.CancelledError):
                await self.timer
            self.timer = None
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending,return_exceptions=True)
        async with self.lock:
            for trip in self.data['trips']:
                trip['routing_pending'] = False
            self.save()
        if self.client:
            await self.client.aclose()
            self.client = None

    def save(self):
        body = json.dumps(self.data,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        with self.service.store.transaction() as db:
            db.execute('INSERT INTO logistics_network_snapshot(id,version,body) VALUES (1,1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,version=excluded.version',(body,))
        self.last_save = time.monotonic()

    def event(self,kind,message,vehicle=None,facility=None):
        self.data['events'].append({'id':identifier('event'),'time':self.data['clock'],'type':kind,'message':message,'vehicle_id':vehicle,'facility_id':facility})
        self.data['events'] = self.data['events'][-200:]

    def incident(self,kind,title,vehicle=None,facility=None,trip=None):
        key = f'{kind}:{vehicle or facility}:{trip or ""}'
        previous = next((i for i in self.data['incidents'] if i.get('episode_key')==key and i['state']!='resolved'),None)
        if previous:
            return previous
        item = {'id':identifier('incident'),'kind':kind,'title':title,'vehicle_id':vehicle,'facility_id':facility,'state':'active','created_at':self.data['clock'],'episode_key':key,'trip_id':trip}
        self.data['incidents'].append(item)
        if len(self.data['incidents'])>200:
            old = next((i for i in self.data['incidents'] if i['state']=='resolved'),self.data['incidents'][0])
            self.data['incidents'].remove(old)
        self.event(kind,title,vehicle,facility)
        return item

    def find(self,category,key):
        item = next((value for value in self.data[category] if value['id']==key),None)
        if item is None:
            raise HTTPException(404,detail='Объект логистики не найден')
        return item

    def status(self,trip,value):
        if trip['status']!=value:
            trip['status'] = value
            trip['stage_elapsed'] = 0.0
            self.event('trip_status',f"Рейс {trip['id']}: {STATUS_NAMES.get(value,value)}",trip['vehicle_id'])
        self.find('vehicles',trip['vehicle_id'])['status'] = value

    def stop(self,trip,cause,title,facility=None):
        if trip['status']!='blocked':
            trip['resume_status'] = trip['status']
        trip['blocked_reason'] = cause
        self.status(trip,'blocked')
        self.find('vehicles',trip['vehicle_id'])['speed_kmh'] = 0
        self.incident(cause,title,trip['vehicle_id'],facility,trip['id'])

    def remaining(self,trip):
        points = (trip.get('movement_route') or trip['route'])['coordinates']
        if trip['status'] in ('in_transit','blocked') and trip.get('stock_loaded') and points:
            position = self.find('vehicles',trip['vehicle_id'])['position']
            closest = min(range(len(points)),key=lambda i: distance(position,points[i]))
            return [position]+points[closest:]
        return points

    def schedule(self,trip):
        if trip['status'] in ('delivered','unloading'):
            return
        old = self.route_tasks.get(trip['id'])
        if old and not old.done():
            old.cancel()
        trip['route_revision'] += 1
        trip['routing_pending'] = True
        self.find('vehicles',trip['vehicle_id'])['speed_kmh'] = 0
        task = asyncio.create_task(self.route_trip(trip['id'],self.generation,trip['route_revision']),name='logistics-road-route')
        self.route_tasks[trip['id']] = task
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def route_trip(self,key,generation,token):
        async with self.lock:
            if generation!=self.generation:
                return
            trip = self.find('trips',key)
            if token!=trip['route_revision']:
                return
            vehicle,destination = self.find('vehicles',trip['vehicle_id']),self.find('facilities',trip['destination_id'])
            start,finish = list(vehicle['position']),[destination['lon'],destination['lat']]
            polygons = [copy.deepcopy(c['polygon']) for c in self.data['closures'] if c['active']]
            simulated = trip['simulated_no_detour']
            active_position = (trip['stock_loaded'] and trip.get('resume_status')=='in_transit') or trip['status']=='in_transit'
        route,error = None,None
        if simulated:
            error = 'По условиям сценария доступного объезда нет'
        else:
            try:
                route = await self.road_route(start,finish,polygons)
                if active_position and distance(start,route['coordinates'][0])>0.03:
                    raise ValueError('Объезд не начинается у фактической позиции; движение остаётся остановленным')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error,route = str(exc)[:200],None
        async with self.lock:
            if generation!=self.generation:
                return
            trip = self.find('trips',key)
            if trip['route_revision']!=token:
                return
            trip['routing_pending'] = False
            if route is None:
                trip['route']['quality'] = 'blocked' if polygons or simulated else 'unavailable'
                trip['route_error'] = error or 'Маршрут недоступен'
                self.stop(trip,'no_detour' if simulated else 'route_unavailable',f"Движение остановлено: {trip['route_error']}")
            else:
                trip['route'],trip['progress'],trip['movement_route'] = route,0.0,None
                trip.pop('route_error',None)
                if not active_position and not trip['stock_loaded']:
                    self.find('vehicles',trip['vehicle_id'])['position'] = list(route['coordinates'][0])
                if trip['blocked_reason'] in ('route_unavailable','road_closure','no_detour'):
                    trip['blocked_reason'] = None
                    self.status(trip,trip.get('resume_status','gate_check'))
                    for item in self.data['incidents']:
                        if item.get('trip_id')==key and item['kind'] in ('route_unavailable','road_closure','no_detour') and item['state']!='resolved':
                            item['state'] = 'restored'
                self.event('route_ready',f"Дорожный маршрут рассчитан: {route['distance_km']:.1f} км",trip['vehicle_id'])
            self.data['revision'] += 1
            self.save()

    async def road_route(self,start,finish,polygons):
        key = json.dumps([start,finish,polygons],separators=(',',':'))
        async with self.routing_lock:
            cached = self.cache.get(key)
            if cached and time.monotonic()-cached[0] < (3600 if cached[1] else 60):
                self.cache.move_to_end(key)
                if cached[1] is None:
                    raise ValueError(cached[2])
                return copy.deepcopy(cached[1])
            await asyncio.sleep(max(0.0,self.next_request-time.monotonic()))
            self.next_request = time.monotonic()+1.2
            body = {'locations':[{'lon':start[0],'lat':start[1]},{'lon':finish[0],'lat':finish[1]}],'costing':'truck','units':'kilometers','exclude_polygons':polygons,'costing_options':{'truck':{'height':4.0,'width':2.5,'length':12.0,'weight':32.0}}}
            try:
                if self.client is None:
                    raise ValueError('Дорожный провайдер ещё не подключён')
                response = await self.client.post(ROUTING_URL,json=body)
                response.raise_for_status()
                if len(response.content)>3_000_000:
                    raise ValueError('Ответ дорожного провайдера слишком велик')
                data = response.json()['trip']
                points = []
                for leg in data['legs']:
                    decoded = decode_polyline(leg['shape'])
                    points.extend(decoded if not points else decoded[1:])
                if len(points)<2 or len(points)>12000 or blocked(points,polygons):
                    raise ValueError('Провайдер не предложил допустимого дорожного объезда')
                if distance(start,points[0])>0.3 or distance(finish,points[-1])>0.5:
                    raise ValueError('Маршрут не достигает выбранных объектов')
                summary = data['summary']
                route = {'coordinates':points,'distance_km':float(summary['length']),'duration_minutes':max(0.1,float(summary['time'])/60),'provider':'Valhalla','quality':'road','road_start':points[0],'road_end':points[-1],'access_note':'Объекты привязаны к ближайшему дорожному подъезду'}
                self.cache[key] = (time.monotonic(),route,None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.cache[key] = (time.monotonic(),None,f'Valhalla недоступна: {str(exc)[:140]}')
                raise ValueError(self.cache[key][2]) from exc
            finally:
                while len(self.cache)>48:
                    self.cache.popitem(last=False)
            return copy.deepcopy(route)

    async def run(self):
        last_tick = time.monotonic()
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            elapsed,last_tick = min(2.0,max(0.0,now-last_tick)),now
            try:
                async with self.lock:
                    for vehicle in self.data['vehicles']:
                        vehicle['gps_age'] = 0 if vehicle['gps_available'] else vehicle['gps_age']+elapsed
                    if self.data['running'] and not self.data['paused']:
                        self.tick(elapsed*self.data['speed'])
                    if now-self.last_save>=5:
                        self.save()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('Regional logistics tick failed')

    def tick(self,seconds):
        self.data['clock_seconds'] += seconds
        self.data['clock'] = stamp(self.data['clock_seconds'])
        self.data['revision'] += 1
        for trip in self.data['trips']:
            vehicle = self.find('vehicles',trip['vehicle_id'])
            origin,destination = self.find('facilities',trip['origin_id']),self.find('facilities',trip['destination_id'])
            vehicle['speed_kmh'] = 0
            if trip['status'] in ('delivered','blocked'):
                continue
            if not vehicle['gps_available']:
                self.stop(trip,'gps_loss','Потеря GPS: положение неизвестно, движение остановлено')
                continue
            trip['stage_elapsed'] += seconds
            if trip['status']=='gate_check':
                if trip['cargo']['weight_tonnes']>vehicle['capacity']:
                    self.stop(trip,'overload','КПП: превышена грузоподъёмность; требуется разгрузка',origin['id'])
                elif not trip['cargo_valid']:
                    self.stop(trip,'wrong_cargo','КПП: груз не соответствует накладной; требуется проверка',origin['id'])
                elif trip['stage_elapsed']>=30:
                    self.status(trip,'queued')
            elif trip['status']=='loading':
                trip['loading_progress'] = min(1.0,trip['loading_progress']+seconds/240)
                if trip['loading_progress']>=1:
                    if not trip['stock_loaded']:
                        quantity = trip['cargo']['quantity']
                        if origin['stock']<quantity:
                            self.stop(trip,'stock_shortage','Недостаточно товара на складе отправления',origin['id'])
                            continue
                        origin['stock'] -= quantity
                        trip['stock_loaded'] = True
                        vehicle['load'] = trip['cargo']['weight_tonnes']
                        self.event('loaded',f"Погружено {quantity} единиц; остаток склада {origin['stock']}",vehicle['id'],origin['id'])
                    self.status(trip,'awaiting_route' if trip['routing_pending'] or trip['route']['quality']!='road' else 'in_transit')
            elif trip['status']=='awaiting_route':
                if not trip['routing_pending'] and trip['route']['quality']=='road':
                    self.status(trip,'in_transit')
            elif trip['status']=='in_transit':
                if trip['routing_pending'] or trip['route']['quality']!='road':
                    continue
                self.inject(trip)
                if trip['status']!='in_transit' or trip['routing_pending']:
                    continue
                if self.data['scenario']=='route_deviation' and trip['vehicle_id']=='truck-1' and not self.data['scenario_injected']:
                    continue
                route = trip.get('movement_route') or trip['route']
                trip['progress'] = min(1.0,trip['progress']+seconds/max(6.0,route['duration_minutes']*60))
                vehicle['position'],vehicle['heading'] = path_point(route['coordinates'],trip['progress'])
                vehicle['speed_kmh'] = min(90,route['distance_km']/max(1e-3,route['duration_minutes']/60))
                if trip.get('movement_route'):
                    trip['deviation_confirmations'] = trip['deviation_confirmations']+1 if near_path(vehicle['position'],trip['route']['coordinates'])>0.15 else 0
                    if trip['deviation_confirmations']>=2:
                        self.stop(trip,'route_deviation','Подтверждено отклонение от назначенного маршрута; машина остановлена для проверки')
                        continue
                trip['eta'] = stamp(self.data['clock_seconds']+(1-trip['progress'])*route['duration_minutes']*60)
                if trip['progress']>=1:
                    self.status(trip,'queued')
                    trip['arrival_queue'] = True
                    trip['eta'] = self.data['clock']
            elif trip['status']=='unloading':
                trip['unloading_progress'] = min(1.0,trip['unloading_progress']+seconds/180)
                if trip['unloading_progress']>=1:
                    quantity = trip['cargo']['quantity']
                    if destination['stock']+quantity>destination['capacity']:
                        self.stop(trip,'warehouse_full','Склад приёмки заполнен; требуется освободить место',destination['id'])
                        continue
                    if not trip['stock_delivered']:
                        destination['stock'] += quantity
                        trip['stock_delivered'] = True
                        vehicle['load'] = 0
                        self.event('delivered',f"Доставлено {quantity} единиц; остаток склада {destination['stock']}",vehicle['id'],destination['id'])
                    self.status(trip,'delivered')
        self.allocate_docks()

    def allocate_docks(self):
        for facility in self.data['facilities']:
            occupied = sum(1 for t in self.data['trips'] if (t['status']=='loading' and t['origin_id']==facility['id']) or (t['status']=='unloading' and t['destination_id']==facility['id']))
            waiting = [t for t in self.data['trips'] if t['status']=='queued' and (t['destination_id'] if t.get('arrival_queue') else t['origin_id'])==facility['id']]
            facility['queue'] = [t['vehicle_id'] for t in waiting]
            for trip in waiting[:max(0,facility['docks']-occupied)]:
                facility['queue'].remove(trip['vehicle_id'])
                self.status(trip,'unloading' if trip.get('arrival_queue') else 'loading')
            facility['occupied_docks'] = occupied+min(len(waiting),max(0,facility['docks']-occupied))
            if self.data['scenario']=='dock_queue' and len(facility['queue'])>=2:
                self.incident('dock_queue','Очередь к рампе: ожидают несколько грузовиков',facility=facility['id'],trip='demo-queue')
            elif not facility['queue']:
                for item in self.data['incidents']:
                    if item['kind']=='dock_queue' and item['facility_id']==facility['id'] and item['state']=='active':
                        item['state'] = 'restored'

    def closure(self,lon,lat,radius,reason,simulated=False):
        polygon = [[lon+radius/(111200*math.cos(math.radians(lat)))*math.cos(i*math.pi/6),lat+radius/111200*math.sin(i*math.pi/6)] for i in range(12)]
        item = {'id':identifier('closure'),'polygon':polygon,'reason':reason,'active':True,'simulated':simulated}
        self.data['closures'].append(item)
        self.event('road_closure',reason)
        for trip in self.data['trips']:
            if trip['status'] in ('delivered','unloading'):
                continue
            points = self.remaining(trip)
            if points and blocked(points,[polygon]):
                self.stop(trip,'road_closure','Участок маршрута перекрыт; движение остановлено до расчёта объезда')
                trip['route']['quality'] = 'blocked'
                self.schedule(trip)
        return item

    def inject(self,trip):
        if self.data['scenario_injected'] or trip['vehicle_id']!='truck-1':
            return
        scenario = self.data['scenario']
        if scenario=='route_deviation' and trip['progress']==0:
            alternative = next((t['route'] for t in self.data['trips'] if t['id']!=trip['id'] and t['origin_id']==trip['origin_id'] and t['destination_id']!=trip['destination_id'] and t['route']['quality']=='road'),None)
            if alternative:
                trip['movement_route'] = copy.deepcopy(alternative)
                self.data['scenario_injected'] = True
                self.event('wrong_turn','Демо: водитель выбрал другое направление по дорожной сети',trip['vehicle_id'])
            elif not any(t['routing_pending'] for t in self.data['trips']):
                self.stop(trip,'route_unavailable','Сценарий отклонения ожидает доступного альтернативного дорожного маршрута')
            return
        if trip['progress']<0.12:
            return
        if scenario in ('road_closure','no_detour'):
            self.data['scenario_injected'] = True
            if scenario=='no_detour':
                trip['simulated_no_detour'] = True
            point,_ = path_point(trip['route']['coordinates'],min(0.8,trip['progress']+0.2))
            self.closure(point[0],point[1],700,'Демонстрационное перекрытие дороги',simulated=True)
            if scenario=='no_detour':
                self.stop(trip,'no_detour','Демо: объезд отсутствует; требуется снять ограничение или проверить ситуацию')
        elif scenario=='gps_loss':
            self.data['scenario_injected'] = True
            self.find('vehicles',trip['vehicle_id'])['gps_available'] = False
            self.stop(trip,'gps_loss','Демо: потеря GPS; последняя позиция сохранена, движение остановлено')

    async def snapshot(self):
        async with self.lock:
            result = copy.deepcopy(self.data)
        pending = sum(t['routing_pending'] for t in result['trips'])
        available = sum(t['route']['quality']=='road' for t in result['trips'])
        result['routing_status'] = {'provider':'Valhalla','state':'pending' if pending else 'ready' if available==len(result['trips']) else 'partial' if available else 'unavailable','pending':pending,'available':available,'message':'Дорожная сеть OSM; без допустимого маршрута движение запрещено'}
        result['as_of'] = stamp(time.time())
        result['permissions'] = {'write':['admin','dispatcher-1'],'resolve':['dispatcher-1']}
        return result

    async def control(self,body):
        action = body.get('action')
        if action not in ACTION_NAMES:
            raise HTTPException(422,detail='Неизвестное действие управления')
        scenario = body.get('scenario',self.data['scenario'])
        if scenario not in {s['id'] for s in SCENARIOS}:
            raise HTTPException(422,detail='Неизвестный сценарий')
        speed = number(body.get('speed',self.data['speed']),'Скорость',1,120)
        async with self.lock:
            if action=='reset' or (action=='start' and scenario!=self.data['scenario']):
                self.generation += 1
                for task in list(self.tasks):
                    task.cancel()
                self.data = self.seed(scenario)
                self.data['speed'] = speed
                for trip in self.data['trips']:
                    self.schedule(trip)
            if action=='start':
                self.data['running'],self.data['paused'] = True,False
            elif action=='pause':
                self.data['paused'] = True
                for vehicle in self.data['vehicles']:
                    vehicle['speed_kmh'] = 0
            elif action=='resume':
                self.data['running'],self.data['paused'] = True,False
            elif action=='speed':
                self.data['speed'] = speed
            self.data['revision'] += 1
            self.event('control',f'Управление демонстрацией: {ACTION_NAMES[action]}')
            self.save()
        return await self.snapshot()

    async def add_closure(self,body):
        lon,lat = number(body.get('lon'),'Долгота',35,39),number(body.get('lat'),'Широта',53,57)
        radius = number(body.get('radius_m',500),'Радиус',50,5000)
        reason = str(body.get('reason','Перекрытие дороги')).strip()[:200]
        async with self.lock:
            if len(self.data['closures'])>=50:
                inactive = next((c for c in self.data['closures'] if not c['active']),None)
                if inactive:
                    self.data['closures'].remove(inactive)
                else:
                    raise HTTPException(409,detail='Достигнут лимит 50 перекрытий')
            item = self.closure(lon,lat,radius,reason or 'Перекрытие дороги')
            self.data['revision'] += 1
            self.save()
            return copy.deepcopy(item)

    async def reopen(self,key):
        async with self.lock:
            item = self.find('closures',key)
            if item['active']:
                item['active'] = False
                self.event('road_reopened',f"Дорога открыта: {item['reason']}")
                for trip in self.data['trips']:
                    if item.get('simulated'):
                        trip['simulated_no_detour'] = False
                    if trip['status']!='delivered' and trip['blocked_reason'] in ('road_closure','route_unavailable','no_detour'):
                        self.schedule(trip)
                self.data['revision'] += 1
                self.save()
            return copy.deepcopy(item)

    async def add_facility(self,body):
        name,kind = str(body.get('name','')).strip(),body.get('kind','warehouse')
        if not name or len(name)>120 or kind not in ('factory','warehouse','supplier'):
            raise HTTPException(422,detail='Нужны имя до 120 символов и тип factory/warehouse/supplier')
        lat,lon = number(body.get('lat'),'Широта',53,57),number(body.get('lon'),'Долгота',35,39)
        async with self.lock:
            if len(self.data['facilities'])>=30:
                raise HTTPException(409,detail='Достигнут лимит 30 объектов')
            item = {'id':identifier('facility'),'name':name,'kind':kind,'lat':lat,'lon':lon,'docks':1,'capacity':1500,'stock':100,'queue':[]}
            self.data['facilities'].append(item)
            self.data['revision'] += 1
            self.event('facility_added',f'Добавлен объект: {name}',facility=item['id'])
            self.save()
            return copy.deepcopy(item)

    async def add_trip(self,body):
        cargo = body.get('cargo') or {'name':'Автокомплектующие','quantity':20,'weight_tonnes':12}
        if not isinstance(cargo,dict):
            raise HTTPException(422,detail='cargo должен быть объектом')
        name = str(cargo.get('name','Автокомплектующие')).strip()[:120]
        quantity = number(cargo.get('quantity',20),'Количество',1,1000)
        weight = number(cargo.get('weight_tonnes',12),'Масса',0.1,100)
        if not quantity.is_integer() or not name:
            raise HTTPException(422,detail='Количество должно быть целым, груз должен иметь имя')
        async with self.lock:
            vehicle = self.find('vehicles',body.get('vehicle_id'))
            origin,destination = self.find('facilities',body.get('origin_id')),self.find('facilities',body.get('destination_id'))
            if origin['id']==destination['id']:
                raise HTTPException(422,detail='Отправление и назначение должны различаться')
            if any(t['vehicle_id']==vehicle['id'] and t['status']!='delivered' for t in self.data['trips']):
                raise HTTPException(409,detail='У машины уже есть незавершённый рейс')
            if distance(vehicle['position'],[origin['lon'],origin['lat']])>0.6:
                raise HTTPException(409,detail='Машина находится у другого объекта; выберите текущее место отправления')
            while len(self.data['trips'])>=100:
                completed = next((t for t in self.data['trips'] if t['status']=='delivered'),None)
                if completed is None:
                    raise HTTPException(409,detail='Достигнут лимит рейсов')
                self.data['trips'].remove(completed)
            item = self.new_trip(identifier('trip'),vehicle['id'],origin['id'],destination['id'],{'name':name,'quantity':int(quantity),'weight_tonnes':weight})
            self.data['trips'].append(item)
            vehicle['trip_id'],vehicle['status'] = item['id'],'gate_check'
            self.schedule(item)
            self.event('trip_created',f"Новый рейс: {origin['name']} → {destination['name']}",vehicle['id'])
            self.data['revision'] += 1
            self.save()
            return copy.deepcopy(item)

    async def recalculate(self):
        async with self.lock:
            for trip in self.data['trips']:
                if not trip['routing_pending'] and trip['status'] not in ('delivered','unloading'):
                    self.schedule(trip)
            self.data['revision'] += 1
            self.event('route_recalculation','Запрошен пересчёт маршрутов из фактических позиций')
            self.save()
        return await self.snapshot()

    async def gps(self,key,body):
        if not isinstance(body.get('available'),bool):
            raise HTTPException(422,detail='available должен быть логическим значением')
        async with self.lock:
            vehicle = self.find('vehicles',key)
            vehicle['gps_available'] = body['available']
            if body['available']:
                vehicle['gps_age'] = 0
                for item in self.data['incidents']:
                    if item['vehicle_id']==key and item['kind']=='gps_loss' and item['state']=='active':
                        item['state'] = 'restored'
            else:
                vehicle['speed_kmh'] = 0
                for trip in self.data['trips']:
                    if trip['vehicle_id']==key and trip['status']!='delivered':
                        self.stop(trip,'gps_loss','GPS отключён: движение остановлено')
            self.data['revision'] += 1
            self.event('gps_restored' if body['available'] else 'gps_disabled','GPS восстановлен; требуется проверка диспетчера' if body['available'] else 'GPS отключён',key)
            self.save()
            return copy.deepcopy(vehicle)

    async def resolve(self,key):
        async with self.lock:
            item = self.find('incidents',key)
            if item['state']=='resolved':
                return copy.deepcopy(item)
            trip = next((t for t in self.data['trips'] if t['id']==item.get('trip_id')),None)
            kind = item['kind']
            if kind=='dock_queue':
                facility = self.find('facilities',item['facility_id'])
                facility['docks'] = min(5,facility['docks']+1)
                self.allocate_docks()
                self.event('inspection','После проверки открыта дополнительная рампа',facility=facility['id'])
            elif trip:
                vehicle = self.find('vehicles',trip['vehicle_id'])
                if kind=='overload':
                    ratio = vehicle['capacity']/trip['cargo']['weight_tonnes']
                    trip['cargo']['quantity'] = max(1,int(trip['cargo']['quantity']*ratio))
                    trip['cargo']['weight_tonnes'] = vehicle['capacity']
                elif kind=='wrong_cargo':
                    trip['cargo_valid'] = True
                    trip['cargo']['name'] = 'Автокомплектующие · накладная проверена'
                elif kind=='gps_loss':
                    vehicle['gps_available'],vehicle['gps_age'] = True,0
                elif kind=='stock_shortage':
                    self.find('facilities',trip['origin_id'])['stock'] += trip['cargo']['quantity']
                    self.event('stock_received','Демо: подтверждено поступление недостающего товара',vehicle['id'],trip['origin_id'])
                elif kind=='warehouse_full':
                    facility = self.find('facilities',trip['destination_id'])
                    facility['stock'] = max(0,facility['capacity']-trip['cargo']['quantity'])
                    self.event('stock_relocated','Демо: подтверждён перенос товара и освобождение места',vehicle['id'],facility['id'])
                elif kind=='no_detour':
                    trip['simulated_no_detour'] = False
                    for closure in self.data['closures']:
                        if closure.get('simulated'):
                            closure['active'] = False
                    for affected in self.data['trips']:
                        if affected['id']!=trip['id'] and affected['blocked_reason'] in ('road_closure','route_unavailable'):
                            self.schedule(affected)
                    self.event('inspection','Демо: осмотр завершён, ограничение отсутствия объезда снято',vehicle['id'])
                elif kind=='road_closure':
                    if any(c['active'] and blocked(self.remaining(trip),[c['polygon']]) for c in self.data['closures']):
                        raise HTTPException(409,detail='Участок остаётся закрытым; откройте дорогу или дождитесь объезда')
                if trip['blocked_reason']==kind:
                    trip['blocked_reason'] = None
                    trip['movement_route'] = None
                    self.status(trip,trip.get('resume_status','gate_check'))
                    if kind in ('route_deviation','route_unavailable','no_detour','road_closure'):
                        self.schedule(trip)
            item['state'],item['resolved_at'] = 'resolved',self.data['clock']
            self.event('incident_resolved',f"Проверка причины завершена: {item['title']}",item['vehicle_id'],item['facility_id'])
            self.data['revision'] += 1
            self.save()
            return copy.deepcopy(item)

    async def export(self):
        data = await self.snapshot()
        output = io.StringIO(newline='')
        writer = csv.writer(output,delimiter=';',lineterminator='\r\n')
        writer.writerow(['Время МСК','Тип события','Сообщение','Транспорт','Объект'])
        vehicles = {v['id']:v['name'] for v in data['vehicles']}
        facilities = {f['id']:f['name'] for f in data['facilities']}
        for event in data['events']:
            moment = datetime.fromisoformat(event['time'].replace('Z','+00:00')).astimezone(timezone(timedelta(hours=3)))
            writer.writerow([safe_cell(v) for v in (moment.strftime('%d.%m.%Y %H:%M:%S'),EVENT_NAMES.get(event['type'],'Логистика'),event['message'],vehicles.get(event['vehicle_id'],''),facilities.get(event['facility_id'],''))])
        return ('\ufeff'+output.getvalue()).encode('utf-8')


def safe_cell(value):
    value = str(value).replace('\x00','').replace('\r',' ').replace('\n',' ')
    return "'"+value if value.lstrip().startswith(('=','+','-','@','\t')) else value


def write_access(request,resolve=False):
    user = getattr(request.state,'user',None)
    if not isinstance(user,dict):
        if getattr(request.app.state,'auth_enabled',True) is False:
            return
        raise HTTPException(401,detail='Требуется вход в систему')
    if user.get('operator_id')=='dispatcher-1' and user.get('role')=='dispatcher':
        return
    if user.get('role')=='admin' and not resolve:
        return
    raise HTTPException(403,detail='Разбор происшествий доступен диспетчеру логистики' if resolve else 'Операция доступна администратору и диспетчеру логистики')


def make_logistics_router(network):
    router = APIRouter(prefix='/api/logistics',tags=['logistics'])

    @router.get('/state')
    async def state():
        return await network.snapshot()

    @router.post('/control')
    async def control(request:Request,body:dict=Body(...)):
        write_access(request)
        return await network.control(body)

    @router.post('/closures')
    async def closure(request:Request,body:dict=Body(...)):
        write_access(request)
        return await network.add_closure(body)

    @router.post('/closures/{key}/reopen')
    async def reopen(key:str,request:Request):
        write_access(request)
        return await network.reopen(key)

    @router.post('/incidents/{key}/resolve')
    async def resolve(key:str,request:Request):
        write_access(request,resolve=True)
        return await network.resolve(key)

    @router.post('/facilities')
    async def facility(request:Request,body:dict=Body(...)):
        write_access(request)
        return await network.add_facility(body)

    @router.post('/trips')
    async def trip(request:Request,body:dict=Body(...)):
        write_access(request)
        return await network.add_trip(body)

    @router.post('/routes/recalculate')
    async def recalculate(request:Request):
        write_access(request)
        return await network.recalculate()

    @router.post('/vehicles/{key}/gps')
    async def gps(key:str,request:Request,body:dict=Body(...)):
        write_access(request)
        return await network.gps(key,body)

    @router.get('/export.csv')
    async def export():
        return Response(content=await network.export(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':"attachment; filename=logistics.csv; filename*=UTF-8''%D0%9B%D0%BE%D0%B3%D0%B8%D1%81%D1%82%D0%B8%D0%BA%D0%B0.csv"})

    return router
