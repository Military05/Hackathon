"""Attach the existing B1 service to v5 authentication, lifecycle and shared SQLite."""
import asyncio
from dataclasses import replace

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from .backend import capture_snapshot
from .config import AgentConfig
from .errors import AgentError
from .model_client import LocalModelClient
from .providers import CaptureProvider
from .service import AgentService


class AgentManager(AgentService):
    def __init__(self, backend, db_path, client=None, config=None):
        self.backend = backend
        config = replace(config or AgentConfig.from_env(), database=str(db_path))

        async def capture(incident_id):
            return await asyncio.to_thread(capture_snapshot, backend, incident_id)

        super().__init__(config, CaptureProvider(capture), client or LocalModelClient(config))
        self.runtime_available = False

    def health(self):
        return {"status": "ready" if self.runtime_available else "unavailable",
                "worker_running": self.worker is not None and not self.worker.done(),
                "model_name": self.config.model, "provider": self.config.provider}

    async def start(self):
        await super().start()
        try:
            await self.client.ensure_available()
        except AgentError:
            self.runtime_available = False
        else:
            self.runtime_available = True

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
