import asyncio

import httpx
from fastapi import FastAPI

from src.agent.api import build_router
from src.agent.config import AgentConfig
from src.agent.providers import CaptureProvider
from src.agent.service import AgentService
from tests.agent.test_runtime import model_message
from tests.agent.test_tools_and_result import snapshot


def test_analysis_202_polling_and_shared_completed_cache(tmp_path):
    class Client:
        async def ensure_available(self):
            pass
        async def close(self):
            pass
        async def chat(self, messages, tools, deadline):
            return model_message(messages)

    async def run():
        service = AgentService(AgentConfig(database=str(tmp_path / "jobs.sqlite"), model="test-double"),
                               CaptureProvider(lambda _: snapshot()), Client())
        app = FastAPI()
        app.include_router(build_router(service, lambda p: p in {"dispatcher-1", "dispatcher-2", "dispatcher-3"}))
        await service.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
                admitted = await client.post("/api/incidents/INC-ZONE-1/analysis", headers={"X-Demo-Operator": "dispatcher-1"}, json={})
                assert admitted.status_code == 202
                job_id = admitted.json()["job_id"]
                for _ in range(100):
                    response = await client.get(f"/api/agent-jobs/{job_id}")
                    if response.json()["status"] == "completed":
                        break
                    await asyncio.sleep(.01)
                assert response.status_code == 200 and response.json()["status"] == "completed"
                assert response.json()["result"]["facts"][0]["value"] == 20.0
                assert response.json()["stale"] is False
                repeat = await client.post("/api/incidents/INC-ZONE-1/analysis", headers={"X-Demo-Operator": "dispatcher-3"})
                assert repeat.status_code == 202 and repeat.json()["cached"]
                assert repeat.json()["job_id"] == job_id
                assert repeat.json()["requested_by_operator_ids"] == ["dispatcher-1", "dispatcher-3"]
        finally:
            await service.stop()
    asyncio.run(run())
