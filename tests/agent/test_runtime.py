import asyncio
import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agent.api import build_router
from src.agent.config import AgentConfig
from src.agent.errors import AgentError
from src.agent.loop import run_analysis
from src.agent.model_client import LocalModelClient
from src.agent.providers import CaptureProvider, FrozenSnapshot
from src.agent.service import AgentService
from tests.agent.test_tools_and_result import answer, snapshot


def tool_call(name="get_incident", arguments=None, native=False):
    arguments = arguments or {"incident_id": "INC-ZONE-1"}
    return {"id": "call-1", "type": "function", "function": {"name": name,
            "arguments": arguments if native else json.dumps(arguments)}}


def model_message(messages, native=False):
    if any(m["role"] == "tool" for m in messages):
        return {"role": "assistant", "content": answer([{"source": "event", "id": "demo-position-0010",
                "field": "payload.x", "value": 20.0}])}
    return {"role": "assistant", "content": "", "tool_calls": [tool_call(native=native)]}


@pytest.mark.parametrize("provider", ["openai_compatible", "ollama"])
def test_http_adapter_executes_tools_and_returns_valid_evidence(provider):
    wire = []

    def transport(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "test-double"}]} if provider == "openai_compatible"
                                  else {"models": [{"name": "test-double"}]})
        body = json.loads(request.content)
        wire.append(body)
        message = model_message(body["messages"], native=provider == "ollama")
        return httpx.Response(200, json={"choices": [{"message": message}]} if provider == "openai_compatible"
                              else {"message": message})

    async def run():
        config = AgentConfig(provider=provider, base_url="http://127.0.0.1:1234/v1" if provider == "openai_compatible"
                             else "http://127.0.0.1:11434", model="test-double")
        client = LocalModelClient(config, httpx.MockTransport(transport))
        try:
            await client.ensure_available()
            result = await run_analysis(client, snapshot(), config)
            assert result["tool_trace"][0]["tool"] == "get_incident"
            assert result["evidence_event_ids"] == ["demo-position-0010"]
            assert wire[1]["messages"][-1]["role"] == "tool"
            if provider == "ollama":
                assert wire[1]["messages"][-1]["tool_name"] == "get_incident"
        finally:
            await client.close()
    asyncio.run(run())


def test_actual_loopback_http_protocol_with_model_test_double():
    """Socket-level protocol check, explicitly not a real Qwen acceptance test."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, value):
            content = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            assert self.path == "/v1/models"
            self.send({"data": [{"id": "protocol-test-double"}]})

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send({"choices": [{"message": model_message(body["messages"])}]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def run():
        config = AgentConfig(base_url=f"http://127.0.0.1:{server.server_port}/v1", model="protocol-test-double")
        client = LocalModelClient(config)
        try:
            await client.ensure_available()
            result = await run_analysis(client, snapshot(), config)
            assert result["facts"][0]["value"] == 20
        finally:
            await client.close()
    try:
        asyncio.run(run())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_whole_execution_timeout_and_tool_budget():
    class SlowClient:
        async def chat(self, *args):
            await asyncio.sleep(1)

    class ManyTools:
        async def chat(self, *args):
            return {"role": "assistant", "content": "", "tool_calls": [dict(tool_call(), id=f"c{i}") for i in range(7)]}

    async def run():
        with pytest.raises(AgentError) as exc:
            await run_analysis(SlowClient(), snapshot(), AgentConfig(max_execution_seconds=.01))
        assert exc.value.code == "execution_timeout"
        with pytest.raises(AgentError) as exc:
            await run_analysis(ManyTools(), snapshot(), AgentConfig())
        assert exc.value.code == "tool_budget_exceeded"
    asyncio.run(run())


def test_api_requires_profile_and_reports_unavailable(tmp_path):
    class Unavailable:
        async def ensure_available(self):
            raise AgentError("unavailable", "Local Qwen is offline.", 503)
        async def close(self):
            pass

    class StubService:
        async def request(self, *args):
            raise AgentError("unavailable", "Local Qwen is offline.", 503)
        async def get(self, *args):
            raise AgentError("not_found", "No job.", 404)

    app = FastAPI()
    app.include_router(build_router(StubService(), lambda x: x == "dispatcher-1"))
    with TestClient(app) as client:
        path = "/api/incidents/INC-ZONE-1/analysis"
        assert client.post(path).status_code == 422
        assert client.post(path, headers={"X-Demo-Operator": "unknown"}).status_code == 422
        result = client.post(path, headers={"X-Demo-Operator": "dispatcher-1"})
        assert result.status_code == 503 and result.json()["error"]["code"] == "unavailable"
        assert client.get("/api/agent-jobs/missing").status_code == 404


def test_service_snapshot_cache_stale_and_nonblocking_worker(tmp_path):
    source = snapshot().export()
    active = [0]
    maximum = [0]

    class Client:
        async def ensure_available(self):
            pass
        async def close(self):
            pass
        async def chat(self, messages, tools, deadline):
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
            await asyncio.sleep(.01)
            reply = model_message(messages)
            active[0] -= 1
            return reply

    async def run():
        service = AgentService(AgentConfig(database=str(tmp_path / "jobs.sqlite"), model="test-double"),
                               CaptureProvider(lambda _: source), Client())
        await service.start()
        try:
            first, second = await asyncio.gather(service.request("INC-ZONE-1", "dispatcher-1"),
                                                 service.request("INC-ZONE-1", "dispatcher-2"))
            assert first["job_id"] == second["job_id"]
            # Mutate live source while model awaits: its captured evidence must remain old.
            source["events"][0]["payload"]["x"] = 21
            for _ in range(100):
                job = await service.get(first["job_id"])
                if job["status"] == "completed":
                    break
                await asyncio.sleep(.01)
            assert job["status"] == "completed" and job["stale"]
            assert job["result"]["facts"][0]["value"] == 20
            assert maximum[0] == 1
        finally:
            await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize("url", ["https://cloud.example/v1", "http://example.com", "http://127.0.0.1:1234/v1?key=secret"])
def test_no_remote_model_fallback(url):
    with pytest.raises(AgentError):
        AgentConfig(base_url=url)
