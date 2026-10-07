"""Single-process local application. Run: python -m uvicorn src.core.main:app."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
import logging
import os
from pathlib import Path

from fastapi import Body, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.core.service import ApiError, Service, stamp

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("dispatch")


def create_app(db_path=None, site_path=None, enable_scheduler=True, clock=None):
    service_kwargs = {"clock": clock} if clock else {}
    service = Service(db_path or os.environ.get("DISPATCH_DB", str(ROOT / "data/runtime/dispatch.db")),
                      site_path or ROOT / "data/demo/site.json", **service_kwargs)
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
            model = MovementModel()
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
                    for asset in service.site["assets"]:
                        if asset["type"] != "vehicle":
                            continue
                        events = await asyncio.to_thread(service.event_history, asset["id"], stamp(now - timedelta(seconds=10)), stamp(now), 100)
                        observation = await asyncio.to_thread(model.evaluate, asset["id"], events, stamp(now))
                        await asyncio.to_thread(service.register_model_observation, observation)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Background monitoring cycle failed")
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(application):
        if manager:
            manager.start()
        timer = asyncio.create_task(scheduler()) if enable_scheduler else None
        if demo and enable_scheduler and os.environ.get("DEMO_AUTOSTART", "1") == "1":
            try:
                await demo.start("normal")
            except Exception:
                log.exception("Demo traffic could not start")
        try:
            yield
        finally:
            if demo:
                await demo.stop()
            if timer:
                timer.cancel()
                with suppress(asyncio.CancelledError):
                    await timer
            if manager:
                manager.stop()

    app = FastAPI(title="Enterprise dispatch demo", version="2.1", lifespan=lifespan)
    app.state.service = service
    app.state.agent = manager
    app.state.ml = model
    app.state.demo = demo
    service.agent = manager

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message, "details": exc.details})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"loc": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": "Request validation failed", "details": details})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "contract_version": 2, "contract_revision": "2.1-audit",
                "demo": True, "as_of": stamp(service.clock()),
                "rules": {"status": "ready", "version": service.rule_version},
                "ml": model.health() if model else {"status": "unavailable"},
                "agent": manager.health() if manager else {"status": "unavailable"},
                "storage": "sqlite", "single_worker_required": True}

    @app.get("/api/site")
    def site():
        return service.site

    @app.get("/api/demo/status")
    def demo_status():
        status = demo.status() if demo else {"running": False, "scenario": None, "source_count": 0,
                                            "error": "Simulator extension is not connected"}
        return {**status, "available": demo is not None, "scenarios": list(scenarios)}

    @app.post("/api/demo/start")
    async def demo_start(body: dict = Body(...), x_demo_operator: str | None = Header(default=None)):
        service.validate_operator(x_demo_operator)
        if not demo:
            raise ApiError(503, "simulator_unavailable", "Симулятор подключает Гриша / второй агент")
        if set(body) != {"scenario"} or not isinstance(body.get("scenario"), str) or body["scenario"] not in scenarios:
            raise ApiError(422, "invalid_scenario", "Укажите один поддерживаемый scenario")
        async with demo_lock:
            try:
                return await demo.start(body["scenario"])
            except ValueError as exc:
                raise ApiError(503, "demo_configuration_invalid", str(exc)) from exc

    @app.post("/api/demo/stop")
    async def demo_stop(body: dict = Body(default={}), x_demo_operator: str | None = Header(default=None)):
        service.validate_operator(x_demo_operator)
        if body:
            raise ApiError(422, "extra_parameters", "Операция stop не принимает параметры")
        if not demo:
            raise ApiError(503, "simulator_unavailable", "Симулятор ещё не подключён")
        async with demo_lock:
            return await demo.stop()

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
                  x_demo_operator: str | None = Header(default=None)):
        return service.list_incidents(status, scope, site_area_id, x_demo_operator)

    @app.get("/api/incidents/{incident_id}")
    def incident(incident_id: str):
        return service.get_incident(incident_id)

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
    def summary():
        return service.summary()

    @app.get("/api/site-areas/summary")
    def area_summary():
        return service.summary(by_area=True)

    @app.get("/api/dispatch-notifications")
    def notifications(after_seq: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
                      x_demo_operator: str | None = Header(default=None)):
        return service.notifications(x_demo_operator, after_seq, limit)

    @app.get("/api/model-observations")
    def model_observations(asset_id: str | None = None, limit: int = Query(default=100, ge=1, le=500)):
        return service.model_observations(asset_id, limit)

    if manager:
        app.include_router(make_router(manager, service.validate_operator))
    else:
        @app.post("/api/incidents/{incident_id}/analysis")
        def analysis_unavailable(incident_id: str, x_demo_operator: str | None = Header(default=None)):
            service.validate_operator(x_demo_operator)
            service.get_incident(incident_id)
            raise ApiError(503, "unavailable", "Local model provider is unavailable")

    web = ROOT / "src/interface/web"
    if web.exists():
        app.mount("/", StaticFiles(directory=str(web), html=True), name="interface")
    return app


app = create_app()
