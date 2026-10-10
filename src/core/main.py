"""Single-process local application. Run: python -m uvicorn src.core.main:app."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
import logging
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from src.core.service import ApiError, Service, stamp
from src.core.operations import Operations
from src.core.demo_access import allowed_scenarios, scenario_sectors

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("dispatch")


def create_app(db_path=None, site_path=None, enable_scheduler=True, clock=None, enable_auth=None):
    service_kwargs = {"clock": clock} if clock else {}
    service = Service(db_path or os.environ.get("DISPATCH_DB", str(ROOT / "data/runtime/dispatch.db")),
                      site_path or ROOT / "data/demo/site.json", **service_kwargs)
    operations = Operations(service)
    from src.core.logistics import LogisticsNetwork, make_logistics_router
    logistics = LogisticsNetwork(service)
    demo = None
    scenarios = ()
    demo_lock = asyncio.Lock()
    manager = None
    model = None
    # These modules have separate owners and are attached only after their handoff.
    if os.environ.get("DISPATCH_ENABLE_SIMULATOR") == "1":
        try:
            from src.simulator.run import DemoRunner, SCENARIOS
            demo, scenarios = DemoRunner(service), tuple(sorted(SCENARIOS))
        except Exception:
            log.exception("Simulator extension unavailable")
    if demo is None and os.environ.get("DISPATCH_ENABLE_DEMO_TRAFFIC", "1") != "0":
        from src.core.demo_traffic import DemoRunner, SCENARIOS
        demo, scenarios = DemoRunner(service), tuple(SCENARIOS)
    if os.environ.get("DISPATCH_ENABLE_AGENT") == "1":
        try:
            from src.agent.runtime import AgentManager, make_router
            manager = AgentManager(service, db_path=str(service.store.path))
        except Exception:
            log.exception("Local agent extension unavailable")
    if os.environ.get("DISPATCH_ENABLE_ML") == "1":
        try:
            from src.ml.movement import MovementModel
            model = MovementModel(service=service)
            service.model = model
            service.model_version = model.metadata.get("model_version", "not_loaded")
        except Exception:
            log.exception("Movement model extension unavailable")

    async def scheduler():
        next_ml = 0.0
        while True:
            try:
                await asyncio.to_thread(service.tick)
                if model and asyncio.get_running_loop().time() >= next_ml:
                    next_ml = asyncio.get_running_loop().time() + 5
                    now = service.clock()
                    now = now.fromtimestamp(int(now.timestamp()) // 5 * 5, tz=now.tzinfo)
                    states = {asset["id"]: asset for asset in await asyncio.to_thread(service.list_assets)}
                    for asset in service.site["assets"]:
                        if asset["type"] != "vehicle" or states.get(asset["id"], {}).get("monitoring_paused"):
                            continue
                        generation = service.demo_monitoring_generation
                        built_in = bool(service.demo_run_prefix and states.get(asset["id"], {}).get("event_id", "").startswith(service.demo_run_prefix))
                        events = await asyncio.to_thread(service.event_history, asset["id"], stamp(now - timedelta(seconds=10)), stamp(now), 100)
                        observation = await asyncio.to_thread(model.evaluate, asset["id"], events, stamp(now))
                        if built_in and generation != service.demo_monitoring_generation:
                            continue
                        latest = await asyncio.to_thread(service.list_assets)
                        if any(item["id"] == asset["id"] and item.get("monitoring_paused") for item in latest):
                            continue
                        await asyncio.to_thread(service.register_model_observation, observation, expected_demo_generation=generation if built_in else None)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Background monitoring cycle failed")
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(application):
        if manager:
            await manager.start()
        await logistics.start()
        timer = asyncio.create_task(scheduler()) if enable_scheduler else None
        if demo and enable_scheduler and os.environ.get("DEMO_AUTOSTART", "1") == "1":
            try:
                await demo.start("normal")
            except Exception:
                log.exception("Demo traffic could not start")
        try:
            yield
        finally:
            await logistics.shutdown()
            if demo:
                await getattr(demo, "shutdown", demo.stop)()
            if timer:
                timer.cancel()
                with suppress(asyncio.CancelledError):
                    await timer
            if manager:
                await manager.stop()

    app = FastAPI(title="Enterprise dispatch demo", version="2.1", lifespan=lifespan)
    app.state.service = service
    app.state.operations = operations
    app.state.agent = manager
    app.state.ml = model
    app.state.demo = demo
    app.state.logistics = logistics
    service.agent = manager
    from src.core.auth import add_auth
    auth_enabled = (os.environ.get("DISPATCH_ENABLE_AUTH", "1") != "0") if enable_auth is None else bool(enable_auth)
    auth = add_auth(app, service, enabled=auth_enabled)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message, "details": exc.details})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"loc": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": "Request validation failed", "details": details})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "contract_version": 2, "contract_revision": "2.2-factory-safety",
                "demo": True, "as_of": stamp(service.clock()),
                "rules": {"status": "ready", "version": service.rule_version},
                "ml": model.health() if model else {"status": "unavailable"},
                "agent": manager.health() if manager else {"status": "unavailable"},
                "storage": "sqlite", "single_worker_required": True,
                "auth": {"enabled": auth_enabled, "configured": auth.configured() if auth else False}}

    @app.get("/api/site")
    def site():
        return service.site

    @app.get("/api/demo/status")
    def demo_status(x_demo_operator: str | None = Header(default=None)):
        status = demo.status() if demo else {"running": False, "paused": False, "can_resume": False, "scenario": None, "source_count": 0,
                                            "error": "Simulator extension is not connected"}
        operator = service.validate_reader(x_demo_operator) if x_demo_operator else "admin"
        permitted = allowed_scenarios(service, operator, scenarios)
        options = [{**option, "responsible_sector_ids": scenario_sectors(service, option["id"])}
                   for option in status.get("scenario_options", []) if option["id"] in permitted]
        return {**status, "available": demo is not None, "scenarios": permitted,
                "scenario_options": options, "scenario_operator_id": operator,
                "can_stop": operator == "admin" or status.get("scenario") in permitted,
                "can_resume": bool(status.get("can_resume") and (operator == "admin" or status.get("scenario") in permitted))}

    @app.post("/api/demo/start")
    async def demo_start(body: dict = Body(...), x_demo_operator: str | None = Header(default=None)):
        operator = service.validate_reader(x_demo_operator)
        if not demo:
            raise ApiError(503, "simulator_unavailable", "Симулятор подключает Гриша / второй агент")
        if set(body) != {"scenario"} or not isinstance(body.get("scenario"), str) or body["scenario"] not in scenarios:
            raise ApiError(422, "invalid_scenario", "Укажите один поддерживаемый scenario")
        if body["scenario"] not in allowed_scenarios(service, operator, scenarios):
            raise ApiError(403, "scenario_sector_required", "Этот сценарий относится к другому сектору. Выберите сценарий своего рабочего места.")
        async with demo_lock:
            try:
                return await demo.start(body["scenario"])
            except ValueError as exc:
                raise ApiError(503, "demo_configuration_invalid", str(exc)) from exc

    @app.post("/api/demo/stop")
    async def demo_stop(body: dict = Body(default={}), x_demo_operator: str | None = Header(default=None)):
        operator = service.validate_reader(x_demo_operator)
        if body:
            raise ApiError(422, "extra_parameters", "Операция stop не принимает параметры")
        if not demo:
            raise ApiError(503, "simulator_unavailable", "Симулятор ещё не подключён")
        async with demo_lock:
            if operator != "admin" and demo.scenario and demo.scenario not in allowed_scenarios(service, operator, scenarios):
                raise ApiError(403, "scenario_sector_required", "Остановить сценарий другого сектора может администратор.")
            return await demo.stop()

    @app.post("/api/demo/resume")
    async def demo_resume(body: dict = Body(default={}), x_demo_operator: str | None = Header(default=None)):
        operator = service.validate_reader(x_demo_operator)
        if body:
            raise ApiError(422, "extra_parameters", "Операция resume не принимает параметры")
        if not demo or not hasattr(demo, "resume"):
            raise ApiError(503, "simulator_unavailable", "Продолжение поддерживает встроенный демонстрационный источник")
        async with demo_lock:
            if operator != "admin" and demo.scenario and demo.scenario not in allowed_scenarios(service, operator, scenarios):
                raise ApiError(403, "scenario_sector_required", "Продолжить сценарий другого сектора может администратор.")
            if not demo.status().get("can_resume"):
                raise ApiError(409, "demo_not_paused", "Нет приостановленного сценария для продолжения")
            try:
                return await demo.resume()
            except ValueError as exc:
                raise ApiError(503, "demo_configuration_invalid", str(exc)) from exc

    @app.post("/api/events")
    def post_event(body: dict = Body(...)):
        result = service.ingest_event(body)
        return JSONResponse(status_code=200 if result["duplicate"] else 201, content=result)

    @app.get("/api/events")
    def events(asset_id: str | None = None, since: str | None = None, until: str | None = None,
               limit: int = Query(default=100, ge=1, le=500)):
        return service.event_history(asset_id, since, until, limit)

    @app.get("/api/assets")
    def assets():
        return service.list_assets()

    @app.get("/api/sensors")
    def sensors(site_area_id: str | None = None):
        return service.list_sensors(site_area_id)

    @app.get("/api/incidents")
    def incidents(status: str | None = None, scope: str | None = None, site_area_id: str | None = None,
                  x_demo_operator: str | None = Header(default=None), include_closed: bool = False,
                  limit: int = Query(default=80, ge=1, le=200), offset: int = Query(default=0, ge=0, le=100000)):
        return service.list_incidents(status, scope, site_area_id, x_demo_operator, include_closed, limit, offset)

    @app.post("/api/incidents/clear")
    def clear_incidents(body: dict = Body(default={}), x_demo_operator: str | None = Header(default=None)):
        if body:
            raise ApiError(422, "extra_parameters", "Очистка списка не принимает параметры")
        return service.clear_incidents(x_demo_operator)

    def require_log_admin(request):
        if getattr(request.state, 'user', {}).get('role') != 'admin':
            raise ApiError(403, 'admin_required', 'Очистка общего журнала доступна администратору.')

    @app.get('/api/admin/incident-log/preview')
    def incident_log_preview(request: Request):
        require_log_admin(request)
        from src.core.incident_log import preview
        return preview(service)

    @app.post('/api/admin/incident-log/clear')
    def clear_incident_log(request: Request, body: dict = Body(...)):
        require_log_admin(request)
        from src.core.incident_log import clear
        return clear(service, body)

    @app.get("/api/incidents/{incident_id}")
    def incident(incident_id: str, x_demo_operator: str | None = Header(default=None)):
        return service.get_incident(incident_id, x_demo_operator)

    @app.patch("/api/incidents/{incident_id}")
    def patch_incident(incident_id: str, body: dict = Body(...), x_demo_operator: str | None = Header(default=None)):
        return service.action(incident_id, x_demo_operator, body)

    @app.get("/api/operator-profiles")
    def profiles():
        return service.operator_profiles()

    @app.post("/api/operator-presence")
    def presence(body: dict = Body(...), x_demo_operator: str | None = Header(default=None)):
        return service.operator_presence(x_demo_operator, body)

    @app.get("/api/dispatch-summary")
    def summary(x_demo_operator: str | None = Header(default=None)):
        return service.summary(operator=x_demo_operator)

    @app.get("/api/site-areas/summary")
    def area_summary(x_demo_operator: str | None = Header(default=None)):
        return service.summary(by_area=True, operator=x_demo_operator)

    @app.get("/api/dispatch-notifications")
    def notifications(after_seq: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
                      x_demo_operator: str | None = Header(default=None)):
        return service.notifications(x_demo_operator, after_seq, limit)

    @app.get("/api/model-observations")
    def model_observations(asset_id: str | None = None, limit: int = Query(default=100, ge=1, le=500)):
        return service.model_observations(asset_id, limit)

    @app.get("/api/checkpoint/journal")
    def checkpoint_journal(q: str | None = Query(default=None, max_length=100), direction: str | None = None,
                           permission: str | None = None, since: str | None = None, until: str | None = None,
                           sort: str = "newest", limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0, le=100000)):
        return operations.journal(q, direction, permission, since, until, limit, offset, sort)

    @app.get("/api/checkpoint/export.csv")
    def checkpoint_export(q: str | None = Query(default=None, max_length=100), direction: str | None = None,
                          permission: str | None = None, since: str | None = None, until: str | None = None,
                          sort: str = "newest", limit: int = Query(default=200, ge=1, le=200), offset: int = Query(default=0, ge=0, le=100000)):
        payload = operations.checkpoint_csv(q=q, direction=direction, permission=permission, since=since, until=until,
                                             limit=limit, offset=offset, sort=sort)
        return Response(payload, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=checkpoint.csv; filename*=UTF-8''" + quote("Журнал_проходов_КПП.csv"),
                        "X-Export-Limit": str(limit), "X-Export-Offset": str(offset)})

    @app.get("/api/shifts/current")
    def current_shift():
        return {"shift": operations.current_shift()}

    @app.get("/api/operator-activity")
    def operator_activity(x_demo_operator: str | None = Header(default=None)):
        return operations.dispatch_activity(service.validate_reader(x_demo_operator))

    @app.get("/api/dispatch-history/export.csv")
    def dispatch_export(request: Request, scope: str = "mine", limit: int = Query(default=1000, ge=1, le=2000),
                        x_demo_operator: str | None = Header(default=None)):
        if scope not in ("mine", "all"):
            raise ApiError(422, "invalid_scope", "Укажите mine или all")
        if scope == "all" and getattr(request.state, "user", {}).get("role") != "admin":
            raise ApiError(403, "admin_required", "Общий журнал доступен администратору")
        operator = service.validate_operator(x_demo_operator) if scope == "mine" and x_demo_operator != "admin" else None
        return Response(operations.dispatch_history_csv(operator, limit), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=dispatcher-actions.csv; filename*=UTF-8''" + quote("Журнал_действий_диспетчера.csv"), "X-Export-Limit": str(limit)})

    if manager:
        app.include_router(make_router(manager, service.validate_operator))
    else:
        @app.post("/api/incidents/{incident_id}/analysis")
        def analysis_unavailable(incident_id: str, x_demo_operator: str | None = Header(default=None)):
            service.validate_operator(x_demo_operator)
            service.get_incident(incident_id)
            raise ApiError(503, "unavailable", "Local model provider is unavailable")

    app.include_router(make_logistics_router(logistics))
    web = ROOT / "src/interface/web"
    if web.exists():
        app.mount("/", StaticFiles(directory=str(web), html=True), name="interface")
    return app


app = create_app()
