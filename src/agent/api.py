import inspect

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from .errors import AgentError


def build_router(service, operator_exists):
    """A1 injects its registered demo-profile lookup; no hardcoded second profile catalog."""
    router = APIRouter()

    async def check_operator(operator_id):
        if not operator_id:
            raise AgentError("operator_required", "X-Demo-Operator is required.", 422)
        exists = operator_exists(operator_id)
        if inspect.isawaitable(exists):
            exists = await exists
        if not exists:
            raise AgentError("operator_unknown", "Demo operator is not registered.", 422)

    @router.post("/api/incidents/{incident_id}/analysis", status_code=202)
    async def analysis(incident_id: str, x_demo_operator: str | None = Header(default=None)):
        try:
            await check_operator(x_demo_operator)
            return await service.request(incident_id, x_demo_operator)
        except AgentError as exc:
            return JSONResponse(status_code=exc.status, content={"error": exc.as_dict()})

    @router.get("/api/agent-jobs/{job_id}")
    async def job(job_id: str):
        try:
            return await service.get(job_id)
        except AgentError as exc:
            return JSONResponse(status_code=exc.status, content={"error": exc.as_dict()})

    return router
