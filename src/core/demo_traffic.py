"""Small server-side demonstration source; independent of ML/agent extensions.

Every visible position is an ordinary demo Event ingested by Service. Route
progress uses elapsed monotonic time and arc length, never frame accumulation.
"""
import asyncio
from bisect import bisect_right
import math
import threading
import time
import uuid

from src.core.geometry import rectangle_contains
from src.core.service import stamp
from src.core.operations import Operations


SCENARIOS = {
    "normal": "Обычная работа завода",
    "logistics": "Доставка сырья и отгрузка",
    "shift": "Смена персонала",
    "service": "Объезд технической службы",
    "forbidden-zone": "Въезд в закрытую зону",
    "unauthorized-access": "Проход без допуска",
    "sensor-offline": "Потеря связи с датчиком",
    "simultaneous": "Несколько одновременных происшествий",
    "route-deviation": "Отклонение транспорта от маршрута",
    "collision": "Пересечение транспорта на проезде",
    "safe-passing": "Безопасный разъезд транспорта",
    "production-zone": "Погрузчик в производственной зоне",
    "service-zone": "Служебный транспорт в закрытой зоне",
    "red-zone": "Красная зона: въезд запрещён всем",
    "orange-zone": "Оранжевая зона: въезд без допуска",
    "orange-authorized": "Оранжевая зона: въезд с допуском",
    "anomaly-oscillation": "Аномалия: движение туда-сюда",
    "anomaly-wall": "Аномалия: упор в стену",
    "anomaly-erratic": "Аномалия: резкие смены движения",
}

SCENARIO_INFO = {
    "normal": ("Три машины следуют личным маршрутам с остановками у назначения.", "Нарушений не ожидается."),
    "logistics": ("Погрузка, движение по складскому маршруту и отгрузка.", "Нарушений не ожидается."),
    "shift": ("Сначала три подтверждённых прохода через КПП, затем движение транспорта.", "Разрешённые проходы; транспорт ждёт проверки людей."),
    "service": ("Служебная машина объезжает энергоцентр, производство, насосную, медпункт и офис по связанным дорогам. Погрузчики стоят.", "Нарушений не ожидается."),
    "forbidden-zone": ("Машина въезжает в зону без допуска, затем выезжает.", "Въезд без допуска; возможно отклонение от личного маршрута."),
    "unauthorized-access": ("Датчик КПП фиксирует вход U4 без допуска и выход.", "Подтверждённый проход без допуска."),
    "sensor-offline": ("Источник HB-QA прекращает передачу с 5-й до 17-й секунды.", "Потеря сигнала датчика; восстановление после настоящего heartbeat."),
    "simultaneous": ("Нарушение зоны, проход КПП и потеря источника в одном запуске.", "Несколько независимых правил, включая контроль личного маршрута."),
    "route-deviation": ("Машина движется по дороге за пределами своего личного маршрута и возвращается.", "Отклонение после двух полученных точек."),
    "collision": ("Две машины сближаются на проезде и затем расходятся.", "Пересечение транспорта по свежим позициям; возможное отклонение от маршрута."),
    "safe-passing": ("Две машины разъезжаются с достаточным расстоянием.", "Пересечение транспорта не должно фиксироваться."),
    "production-zone": ("Погрузчик входит в красную зону запрета для своего типа и выходит.", "Запрет для типа транспорта; возможно отклонение от личного маршрута."),
    "service-zone": ("Служебная машина входит в запрещённый для неё участок и возвращается.", "Запрет для служебного транспорта и отклонение от личного маршрута."),
    "red-zone": ("V1 по отдельному подъезду входит в красную Z6, выдерживает остановку и возвращается.", "Въезд в зону общего запрета; отдельное правило отклонения маршрута."),
    "orange-zone": ("V1 входит по погрузочному подъезду в оранжевую Z1, не имея допуска, затем возвращается.", "Нарушение допуска в оранжевой зоне; отклонение от личного маршрута."),
    "orange-authorized": ("V3 следует по связанным дорогам к погрузочному подъезду Z1 и возвращается; допуск у V3 есть.", "Тревоги допуска нет. Отклонение от личного маршрута проверяется независимо."),
    "anomaly-oscillation": ("V1 трижды меняет направление на одном участке дороги, затем возвращается к обычному движению.", "MLP оценивает реальные точки; модельная тревога зависит от её вывода."),
    "anomaly-wall": ("V1 подъезжает к стене W4 и многократно пытается продвинуться у границы, затем отъезжает.", "MLP получает длительную остановку и попытки движения; тревога зависит от вывода модели."),
    "anomaly-erratic": ("V1 нерегулярно ускоряется, останавливается и меняет направление вдоль складского проезда.", "Реальные позиции для MLP; результат модели не подменяется сценарием."),
}


def scenario_paths(scenario):
    """Bounded demonstrations on existing roads; the shared site/model hash is unchanged."""
    def path(*waypoints):
        return [{"at": at, "point": list(point)} for at, point in waypoints]

    if scenario == "red-zone":
        return {"duration_seconds": 24, "asset_paths": {"V1": path((0, (3, 49.5)), (5, (8.5, 49.5)), (13, (8.5, 49.5)), (19, (3, 49.5)), (24, (3, 40)))}}
    if scenario == "orange-authorized":
        return {"duration_seconds": 70, "asset_paths": {"V3": path((0, (43, 74)), (3, (40, 74)), (12, (40, 60)), (22, (25, 60)), (29, (25, 50)), (39, (25, 50)), (46, (25, 60)), (56, (40, 60)), (65, (40, 74)), (70, (43, 74)))}}
    if scenario == "anomaly-oscillation":
        return {"duration_seconds": 38, "asset_paths": {"V1": path((0, (40, 30)), (6, (40, 38)), (12, (40, 30)), (18, (40, 38)), (24, (40, 30)), (30, (40, 38)), (36, (40, 30)), (38, (40, 28.5)))}}
    if scenario == "anomaly-wall":
        return {"duration_seconds": 36, "asset_paths": {"V1": path((0, (40, 33)), (5, (36.2, 33)), (9, (36.2, 33)), (11, (36.8, 33)), (13, (36.2, 33)), (17, (36.2, 33)), (19, (36.8, 33)), (21, (36.2, 33)), (25, (36.2, 33)), (27, (36.8, 33)), (29, (36.2, 33)), (32, (36.2, 33)), (36, (40, 33)))}}
    if scenario == "anomaly-erratic":
        return {"duration_seconds": 38, "asset_paths": {"V1": path((0, (40, 16)), (2, (40, 23)), (5, (40, 19)), (7, (40, 28.5)), (12, (40, 28.5)), (14, (40, 21.5)), (17, (40, 33)), (19, (40, 25)), (24, (40, 25)), (26, (40, 38)), (29, (40, 30)), (33, (40, 39)), (38, (40, 40)))}}
    return None


class ArcRoute:
    """Polyline interpolation in plan units, including exact road junctions."""

    def __init__(self, points):
        self.points = []
        self.distances = [0.0]
        for raw in points:
            point = tuple(float(value) for value in raw)
            if len(point) != 2 or not all(math.isfinite(v) and 0 <= v <= 100 for v in point):
                raise ValueError("Traffic route coordinates must be finite and within 0..100")
            if self.points and point == self.points[-1]:
                continue
            if self.points:
                self.distances.append(self.distances[-1] + math.dist(self.points[-1], point))
            self.points.append(point)
        if len(self.points) < 2 or self.distances[-1] <= 0:
            raise ValueError("Traffic route needs at least two distinct points")
        self.length = self.distances[-1]

    def point(self, distance, loop=True):
        distance = distance % self.length if loop else min(self.length, max(0.0, distance))
        if distance >= self.length:
            return self.points[-1]
        index = min(len(self.points) - 2, bisect_right(self.distances, distance) - 1)
        fraction = (distance - self.distances[index]) / (self.distances[index + 1] - self.distances[index])
        a, b = self.points[index], self.points[index + 1]
        return tuple(a[n] + fraction * (b[n] - a[n]) for n in (0, 1))

    def nearest_distance(self, point):
        best = (math.inf, 0.0)
        for index, (a, b) in enumerate(zip(self.points, self.points[1:])):
            dx, dy = b[0] - a[0], b[1] - a[1]
            fraction = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy)))
            projection = (a[0] + fraction * dx, a[1] + fraction * dy)
            candidate = (math.dist(point, projection), self.distances[index] + fraction * math.dist(a, b))
            best = min(best, candidate)
        return best[1]


class Journey:
    def __init__(self, route, anchor, speed, pause, phase=0.0):
        self.route, self.anchor, self.speed, self.pause, self.phase = route, anchor, speed, pause, phase
        self.period = route.length / speed + pause

    def position(self, elapsed):
        phase = (max(0.0, elapsed) + self.phase) % self.period
        driving = max(0.0, phase - self.pause)
        return self.route.point(self.anchor + driving * self.speed)

    def state(self, elapsed):
        return "loading" if (max(0.0, elapsed) + self.phase) % self.period < self.pause else "moving"


class DemoRunner:
    """One bounded-memory source and at most one active asyncio task per server."""

    source = "builtin_factory_traffic"

    def __init__(self, service, interval=1.0, monotonic=time.monotonic):
        self.service = service
        self.interval = interval
        self.monotonic = monotonic
        self._task = None
        self._stop_requested = threading.Event()
        self._wake = asyncio.Event()
        self._ready = asyncio.Event()
        self.running = False
        self.paused = False
        self.paused_at = None
        self.scenario = None
        self.started_at = None
        self.error = None
        self.event_count = 0
        self.last_elapsed = 0.0
        self._once = set()
        self._vehicles = {}
        self._journeys = {}
        self._fault_route = None
        self._fault_journey = None
        self._sequence = 0
        self._run_id = ""
        self.operations = Operations(service)
        self._shift_id = None
        self._shift_employees = []
        self._transport_elapsed = None
        self._gate_sensor = None
        self._safety_paths = {}
        self._safety_returns = {}
        self._demonstration_end = None
        self._normal_v3_elapsed = 0.0
        self._normal_v3_last_frame = 0.0
        self._normal_v3_departed = False

    def status(self):
        return {
            "running": self.running,
            "paused": self.paused,
            "paused_at": self.paused_at,
            "can_resume": self.paused and self.scenario is not None and self.error is None,
            "demonstration_complete": self._demonstration_end is not None and self.last_elapsed >= self._demonstration_end,
            "demonstration_duration_seconds": self._demonstration_end,
            "phase": "normal" if self._demonstration_end is not None and self.last_elapsed >= self._demonstration_end else "demonstration",
            "scenario": self.scenario,
            "scenario_label": SCENARIOS.get(self.scenario),
            "source": self.source,
            "source_count": len(self.service.sensors_by_id),
            "event_count": self.event_count,
            "events_sent": self.event_count,
            "started_at": self.started_at,
            "elapsed_seconds": round(self.last_elapsed, 1),
            "update_interval_seconds": self.interval,
            "error": self.error,
            "vehicle_states": {key: {**value, "paused": self.paused} for key, value in self._vehicles.items()},
            "scenario_options": [{"id": key, "name": name, "description": SCENARIO_INFO[key][0], "expected_alarm": SCENARIO_INFO[key][1]} for key, name in SCENARIOS.items()],
            "scenario_description": SCENARIO_INFO.get(self.scenario, (None, None))[0],
            "expected_alarm": SCENARIO_INFO.get(self.scenario, (None, None))[1],
            "shift": self.operations.shift(self._shift_id) if self._shift_id else None,
        }

    def _prepare(self, scenario):
        if scenario not in SCENARIOS:
            raise ValueError("Unsupported traffic scenario")
        with self.service.store.lock:
            self.service.demo_monitoring_generation += 1
        self.operations.finish_shift(self._shift_id)
        self._shift_id = None
        self._transport_elapsed = None
        self._shift_employees = []
        self._safety_paths = {}
        self._safety_returns = {}
        self._gate_sensor = next((sid for sid, sensor in self.service.sensors_by_id.items()
                                  if sensor["type"] == "access" and sensor.get("building_id") == "G1"), None)
        self.scenario = scenario
        self.paused = False
        self.paused_at = None
        self.started_at = stamp(self.service.clock())
        self.error = None
        self.event_count = self._sequence = 0
        self.last_elapsed = 0.0
        self._once = set()
        self._vehicles = {}
        self._journeys = {}
        self._normal_v3_elapsed = 0.0
        self._normal_v3_last_frame = 0.0
        self._normal_v3_departed = False
        self._fault_route = self._fault_journey = None
        self._demonstration_end = None if scenario in ("normal", "logistics", "service") else 35.0
        self._run_id = uuid.uuid4().hex[:12]
        self.service.demo_run_prefix = "factory-" + self._run_id + "-"
        self.service.demo_segment_start_event_id = self.service.demo_run_prefix + "000000001"
        self.service.demo_pause_allowances = {}
        routes = self.service.site.get("demo_routes", self.service.site.get("routes", {}))
        vehicles = [asset for asset in self.service.site["assets"] if asset["type"] == "vehicle"]
        for index, asset in enumerate(vehicles):
            route = ArcRoute(routes[asset["id"]])
            if route.points[0] != route.points[-1]:
                raise ValueError("Normal traffic routes must form closed road loops")
            building = self.service.buildings.get(asset.get("destination"))
            rect = building["rectangle"] if building else None
            destination = (rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2) if rect else route.points[0]
            speed = {"V1": 1.6, "V2": 1.4, "V3": 1.7}.get(asset["id"], 1.2)
            pause = 3.0
            if scenario == "logistics":
                speed, pause = (1.9 if asset["id"] == "V1" else 0.9), 6.0
            elif scenario == "shift":
                speed, pause = (2.3 if asset["id"] == "V3" else 1.0), 4.0
            elif scenario == "service":
                speed, pause = (1.9 if asset["id"] == "V3" else 1.2), 7.0
            phase = 0.0 if scenario in ("normal", "logistics") and asset["id"] == "V3" else pause + 1 + index * route.length / speed / 3
            self._journeys[asset["id"]] = Journey(route, route.nearest_distance(destination), speed, pause, phase)
        if scenario in ("forbidden-zone", "simultaneous", "orange-zone"):
            definition = self.service.site.get("demo_fault_routes", {}).get("V1")
            if not definition:
                raise ValueError("The site needs a demo_fault_routes.V1 approach to the closed zone")
            self._fault_route = ArcRoute(definition["points"])
            zone = next(zone for zone in self.service.site["zones"] if zone["id"] == definition["zone_id"])
            if not rectangle_contains(*self._fault_route.points[-1], zone["rectangle"]):
                raise ValueError("The fault approach must finish inside its closed zone")
            route = self._journeys["V1"].route
            anchor = route.nearest_distance(self._fault_route.points[0])
            if math.dist(route.point(anchor), self._fault_route.points[0]) > 0.001:
                raise ValueError("The closed-zone approach must connect exactly to the V1 road route")
            self._fault_journey = Journey(route, anchor, 1.6, 0.0)
            self._demonstration_end = 2 * self._fault_route.length / 1.8 + 18.0
        if scenario in ("unauthorized-access", "simultaneous"):
            if not self._gate_sensor or "U4" not in self.service.assets_by_id:
                raise ValueError("The checkpoint scenario requires a G1 access sensor and employee U4")
            if "G1" in self.service.asset_policy("U4")["allowed_building_ids"]:
                raise ValueError("Employee U4 must not have a G1 admission in the unauthorized demo")
        if scenario == "shift":
            self._shift_employees = self.service.site.get("shift_employees", ["U1", "U2", "U3"])
            self._shift_id = self.operations.start_shift(self._shift_employees)
        definition = scenario_paths(scenario) or self.service.site.get("demo_safety_scenarios", {}).get(scenario)
        if scenario in ("route-deviation", "collision", "safe-passing", "production-zone", "service-zone") and not definition:
            raise ValueError("The site needs demo_safety_scenarios." + scenario)
        if definition:
            definition = {**definition, "asset_paths": dict(definition.get("asset_paths", {}))}
            self._demonstration_end = float(definition.get("duration_seconds", 30)) + 10.0
            # Park unrelated sources safely: one demonstration has no accidental extra traffic episodes.
            duration = float(definition.get("duration_seconds", 30))
            for asset_id, point in {"V1": (3, 16), "V2": (96, 60), "V3": (43, 74)}.items():
                if asset_id not in definition["asset_paths"]:
                    definition["asset_paths"][asset_id] = [{"at": 0, "point": list(point)}, {"at": duration, "point": list(point)}]
            for asset_id, waypoints in definition.get("asset_paths", {}).items():
                if asset_id not in self._journeys or len(waypoints) < 2:
                    raise ValueError("Safety paths need a known vehicle and at least two timestamped positions")
                path = []
                for waypoint in waypoints:
                    when, position = float(waypoint["at"]), tuple(float(value) for value in waypoint["point"])
                    if not math.isfinite(when) or when < 0 or len(position) != 2 or not all(math.isfinite(value) and 0 <= value <= 100 for value in position):
                        raise ValueError("Safety waypoints must have finite time and coordinates within the plan")
                    if path and when <= path[-1][0]:
                        raise ValueError("Safety waypoint times must strictly increase")
                    path.append((when, position))
                journey = self._journeys[asset_id]
                anchor = journey.route.nearest_distance(path[-1][1])
                if math.dist(journey.route.point(anchor), path[-1][1]) > 0.01:
                    raise ValueError("Each safety path must finish on its own normal route for a continuous recovery")
                self._safety_paths[asset_id] = path
                self._safety_returns[asset_id] = Journey(journey.route, anchor, journey.speed, 0.0)

    async def start(self, scenario):
        await self.shutdown()
        try:
            self._prepare(scenario)
        except Exception as exc:
            self.error = str(exc)
            raise
        return await self._launch(0.0)

    async def _launch(self, elapsed):
        self._stop_requested.clear()
        self._wake = asyncio.Event()
        self._ready = asyncio.Event()
        self.running = True
        self._task = asyncio.create_task(self._run(elapsed), name="factory-demo-traffic")
        await self._ready.wait()
        if self.error:
            await self.shutdown()
            raise ValueError("Demo traffic could not emit its first frame: " + self.error)
        return self.status()

    async def _halt(self):
        self._stop_requested.set()
        self._wake.set()
        # Await the worker frame too: after this returns no late event can leak.
        if self._task:
            await self._task
            self._task = None
        self.running = False

    async def stop(self):
        """Pause a complete measured frame without ending the scenario or shift."""
        await self._halt()
        if self.scenario is not None and self.error is None:
            self.paused = True
            self.paused_at = self.paused_at or stamp(self.service.clock())
            self.service.pause_demo_monitoring("factory-" + self._run_id + "-")
        return self.status()

    async def resume(self):
        if not self.paused or self.scenario is None or self.error:
            raise ValueError("Нет приостановленного сценария для продолжения")
        elapsed = self.last_elapsed
        self.paused = False
        self.paused_at = None
        self.service.demo_segment_start_event_id = self.service.demo_run_prefix + f"{self._sequence + 1:09d}"
        result = await self._launch(elapsed)
        self.service.resume_demo_monitoring()
        return result

    async def shutdown(self):
        """Finish only for an explicit new start, source failure or server shutdown."""
        await self._halt()
        self.paused = False
        self.paused_at = None
        self.service.resume_demo_monitoring()
        self.operations.finish_shift(self._shift_id, self.error)
        return self.status()

    async def _run(self, offset=0.0):
        epoch = self.monotonic()
        next_frame = epoch
        first_frame = True
        try:
            while not self._stop_requested.is_set():
                elapsed = offset if first_frame else offset + max(0.0, self.monotonic() - epoch)
                await asyncio.to_thread(self.emit_frame, elapsed)
                first_frame = False
                self._ready.set()
                next_frame += self.interval
                delay = max(0.0, next_frame - self.monotonic())
                if delay == 0:
                    # Skip missed frames instead of emitting a catch-up burst.
                    next_frame = self.monotonic() + self.interval
                    delay = self.interval
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.running = False
            if self.error:
                self.operations.finish_shift(self._shift_id, self.error)
            self._ready.set()

    def _emit(self, sensor_id, kind, payload):
        # Pause awaits the whole in-flight frame; no partial or late frame leaks.
        self._sequence += 1
        event = {"event_id": f"factory-{self._run_id}-{self._sequence:09d}",
                 "event_time": stamp(self.service.clock()), "sensor_id": sensor_id,
                 "type": kind, "demo": True, "payload": payload}
        response = self.service.ingest_event(event, shift_id=self._shift_id) if self._shift_id else self.service.ingest_event(event)
        self.event_count += 1
        return response

    def _normal_service_departure_safe(self, elapsed):
        """Plan only built-in normal/logistics traffic; safety checks actual Events.

        V3 waits at its off-road-loop service approach before a circuit. A small
        conservative clearance covers interpolation between the 1 Hz samples.
        """
        from src.core.vehicle_safety import swept_distance
        journey = self._journeys["V3"]
        future = 0.0
        while future < journey.period:
            finish = min(future + 0.5, journey.period)
            start, end = journey.position(future), journey.position(finish)
            for asset_id in ("V1", "V2"):
                peer = self._journeys.get(asset_id)
                if not peer:
                    continue
                peer_start, peer_end = peer.position(elapsed + future), peer.position(elapsed + finish)
                if min(math.dist(start, peer_start), math.dist(end, peer_end)) <= 3.0:
                    return False
                separation = swept_distance((*start, 0.0), (*end, finish - future),
                                            (*peer_start, 0.0), (*peer_end, finish - future))
                if separation is not None and separation <= 3.0:
                    return False
            future = finish
        return True

    def _normal_service_position(self, elapsed):
        journey = self._journeys["V3"]
        delta = max(0.0, elapsed - self._normal_v3_last_frame)
        self._normal_v3_last_frame = elapsed
        if self._normal_v3_departed:
            self._normal_v3_elapsed += delta
            if self._normal_v3_elapsed >= journey.period:
                self._normal_v3_elapsed = 0.0
                self._normal_v3_departed = False
        if not self._normal_v3_departed:
            if not self._normal_service_departure_safe(elapsed):
                return journey.position(0.0), "yielding"
            self._normal_v3_departed = True
        return journey.position(self._normal_v3_elapsed), journey.state(self._normal_v3_elapsed)

    def _position(self, asset_id, elapsed):
        journey = self._journeys[asset_id]
        if self.scenario in ("normal", "logistics") and asset_id == "V3":
            return self._normal_service_position(elapsed)
        if self.scenario == "service" and asset_id != "V3":
            return {"V1": (3, 16), "V2": (96, 60)}.get(asset_id, journey.position(0)), "parked"
        path = self._safety_paths.get(asset_id)
        if path and elapsed >= path[0][0]:
            if elapsed >= path[-1][0]:
                return self._safety_returns[asset_id].position(elapsed - path[-1][0]), "moving"
            index = max(0, bisect_right([waypoint[0] for waypoint in path], elapsed) - 1)
            start, end = path[index], path[index + 1]
            fraction = (elapsed - start[0]) / (end[0] - start[0])
            point = tuple(start[1][n] + (end[1][n] - start[1][n]) * fraction for n in (0, 1))
            return point, self.scenario
        if asset_id == "V1" and self._fault_route:
            speed, hold = 1.8, 8.0
            travel = self._fault_route.length / speed
            if elapsed <= travel:
                return self._fault_route.point(elapsed * speed, loop=False), "zone_approach"
            if elapsed <= travel + hold:
                return self._fault_route.points[-1], "zone_violation"
            if elapsed < 2 * travel + hold:
                return self._fault_route.point((2 * travel + hold - elapsed) * speed, loop=False), "zone_exit"
            return self._fault_journey.position(elapsed - 2 * travel - hold), "moving"
        return journey.position(elapsed), journey.state(elapsed)

    def emit_frame(self, elapsed):
        """Emit one measured frame; demonstration faults occur once per explicit start."""
        if self._stop_requested.is_set() or self.paused:
            return
        self.last_elapsed = max(0.0, elapsed)
        if self.scenario == "shift":
            for index, employee in enumerate(self._shift_employees):
                key = "gate:" + employee
                if elapsed >= 1 + index * 2 and key not in self._once:
                    received = self._emit(self._gate_sensor, "access", {"employee_id": employee, "building_id": "G1", "direction": "in",
                                                                       "access_kind": "passage_confirmed"})
                    if received:
                        self._once.add(key)
            if self._transport_elapsed is None and self.operations.release_transport(self._shift_id):
                self._transport_elapsed = elapsed
        offline = self.scenario in ("sensor-offline", "simultaneous") and 5 <= elapsed < 17
        states = {}
        for sensor_id, sensor in self.service.sensors_by_id.items():
            if offline and sensor_id == "HB-QA":
                continue
            asset_id = sensor.get("asset_id")
            if sensor["type"] == "position" and asset_id in self._journeys:
                movement_elapsed = elapsed
                if self.scenario == "shift":
                    movement_elapsed = max(0.0, elapsed - self._transport_elapsed) if self._transport_elapsed is not None else 0.0
                point, state = self._position(asset_id, movement_elapsed)
                if self.scenario == "shift" and self._transport_elapsed is None:
                    state = "awaiting_gate_checks"
                self._emit(sensor_id, "position", {"asset_id": asset_id, "x": round(point[0], 4), "y": round(point[1], 4)})
                states[asset_id] = {"state": state, "destination": self.service.assets_by_id[asset_id].get("destination")}
            else:
                self._emit(sensor_id, "heartbeat", {})
        self._vehicles = states
        if self.scenario in ("unauthorized-access", "simultaneous"):
            employee = "U4"
            for threshold, direction in ((6, "in"), (13, "out")):
                if elapsed >= threshold and direction not in self._once:
                    self._emit(self._gate_sensor, "access", {"employee_id": employee, "building_id": "G1", "direction": direction,
                                                         "access_kind": "passage_confirmed"})
                    self._once.add(direction)
