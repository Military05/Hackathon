"""Attach the existing B1 service to v5 authentication, lifecycle and shared SQLite."""
import asyncio
from contextlib import suppress
from dataclasses import replace
import logging

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from .backend import capture_snapshot
from .config import AgentConfig
from .errors import AgentError
from .model_client import LocalModelClient
from .providers import CaptureProvider
from .service import AgentService

log = logging.getLogger(__name__)


class AgentManager(AgentService):
    availability_interval_seconds = 10

    def __init__(self, backend, db_path, client=None, config=None):
        self.backend = backend
        config = replace(config or AgentConfig.from_env(), database=str(db_path))

        async def capture(incident_id):
            return await asyncio.to_thread(capture_snapshot, backend, incident_id)

        super().__init__(config, CaptureProvider(capture), client or LocalModelClient(config))
        self.runtime_available = False
        self.availability_monitor = None

    def health(self):
        worker_running = self.worker is not None and not self.worker.done()
        return {"status": "ready" if self.runtime_available and worker_running else "unavailable",
                "worker_running": worker_running,
                "model_name": self.config.model, "provider": self.config.provider}

    async def check_availability(self):
        try:
            # Catalogue only: never perform inference from health checks.
            await asyncio.wait_for(self.client.ensure_available(), timeout=3)
        except (AgentError, TimeoutError):
            self.runtime_available = False
        except Exception:
            self.runtime_available = False
            log.exception("Local model availability check failed")
        else:
            self.runtime_available = True

    async def monitor_availability(self):
        while True:
            await asyncio.sleep(self.availability_interval_seconds)
            await self.check_availability()

    async def start(self):
        if self.availability_monitor is not None and not self.availability_monitor.done():
            return
        await super().start()
        try:
            await self.check_availability()
            self.availability_monitor = asyncio.create_task(
                self.monitor_availability(), name="local-model-availability")
        except BaseException:
            await super().stop()
            raise

    async def stop(self):
        monitor, self.availability_monitor = self.availability_monitor, None
        self.runtime_available = False
        try:
            if monitor is not None:
                monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor
        finally:
            # Cancel the probe before AgentService closes its shared HTTP client.
            await super().stop()

    async def request(self, incident_id, operator_id):
        try:
            result = await super().request(incident_id, operator_id)
        except AgentError as error:
            if error.code == "unavailable":
                self.runtime_available = False
            raise
        if not result.get("cached"):
            self.runtime_available = True
        return result


def make_router(manager, validate_operator):
    router = APIRouter()

    def failure(error):
        return JSONResponse(status_code=error.status, content=error.as_dict())

    @router.post("/api/incidents/{incident_id}/analysis", status_code=202)
    async def analysis(incident_id: str, x_demo_operator: str | None = Header(default=None)):
        # v5 middleware replaces this header with the authenticated session profile;
        # caller-supplied headers cannot switch the operator.
        operator = validate_operator(x_demo_operator)
        try:
            return await manager.request(incident_id, operator)
        except AgentError as error:
            return failure(error)

    @router.get("/api/agent-jobs/{job_id}")
    async def job(job_id: str, x_demo_operator: str | None = Header(default=None)):
        validate_operator(x_demo_operator)
        try:
            return await manager.get(job_id)
        except AgentError as error:
            return failure(error)

    return router
