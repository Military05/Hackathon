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
from src.agent.runtime import AgentManager
from src.agent.service import AgentService
from tests.agent.test_tools_and_result import answer, snapshot


def test_attached_provider_recovers_and_stops_without_a_user_request(tmp_path):
    class Client:
        available = False
        closed = False
        calls = 0

        async def ensure_available(self):
            assert not self.closed
            self.calls += 1
            if not self.available:
                raise AgentError("unavailable", "Explicit offline provider fixture.", 503)

        async def close(self):
            self.closed = True

    async def run():
        client = Client()
        manager = AgentManager(None, str(tmp_path / "jobs.sqlite"), client=client,
                               config=AgentConfig(model="test-double"))
        manager.availability_interval_seconds = .01

        async def wait_status(status):
            async def changed():
                while manager.health()["status"] != status:
                    await asyncio.sleep(.001)
            await asyncio.wait_for(changed(), timeout=1)

        await manager.start()
        try:
            assert manager.health()["status"] == "unavailable"
            assert manager.health()["worker_running"]
            monitor = manager.availability_monitor
            await manager.start()
            assert manager.availability_monitor is monitor and client.calls == 1
            client.available = True
            await wait_status("ready")
            client.available = False
            await wait_status("unavailable")
            client.available = True
            await wait_status("ready")
            manager.worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await manager.worker
            assert manager.health()["status"] == "unavailable"
        finally:
            await manager.stop()
        assert monitor.done() and manager.availability_monitor is None
        assert manager.worker is None and client.closed
        assert manager.health()["status"] == "unavailable"

    asyncio.run(run())


def test_attached_provider_probe_is_cancelled_before_client_close(tmp_path):
    class Client:
        def __init__(self):
            self.calls = 0
            self.probing = asyncio.Event()
            self.cancelled = False

        async def ensure_available(self):
            self.calls += 1
            if self.calls == 1:
                return
            self.probing.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

        async def close(self):
            assert self.cancelled

    async def run():
        client = Client()
        manager = AgentManager(None, str(tmp_path / "jobs.sqlite"), client=client,
                               config=AgentConfig(model="test-double"))
        manager.availability_interval_seconds = .001
        await manager.start()
        try:
            await asyncio.wait_for(client.probing.wait(), timeout=1)
        finally:
            await manager.stop()
        assert client.cancelled and manager.availability_monitor is None

    asyncio.run(run())


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


@pytest.mark.parametrize("provider", ["openai_compatible", "ollama"])
@pytest.mark.parametrize("early_invalid", [False, True])
def test_schema_final_after_tools_and_bounded_format_retry(provider, early_invalid):
    """The real laptop failure must not be repaired by rewriting evidence in Python."""
    wire = []
    claim = {"source": "event", "id": "demo-position-0010", "field": "payload.x", "value": 20.0}

    def transport(request):
        body = json.loads(request.content)
        wire.append(body)
        schema = body.get("format") if provider == "ollama" else body.get("response_format", {}).get("json_schema", {}).get("schema")
        budget = body["options"]["num_predict"] if provider == "ollama" else body["max_tokens"]
        assert budget == (1024 if schema is not None else 450)
        if len(wire) == 1:
            assert schema is None and body["tools"]
            message = {"role": "assistant", "content": "", "tool_calls": [tool_call(native=provider == "ollama")]}
        elif len(wire) == 2:
            assert schema is None and body["tools"]
            if early_invalid:
                invalid = json.loads(answer([{**claim, "source": "event|policy|sensor_health|model_observation"}]))
                invalid["hypotheses"][0]["limitations"] = []
                message = {"role": "assistant", "content": json.dumps(invalid)}
            else:
                message = {"role": "assistant", "content": "", "tool_calls": [
                    tool_call("get_asset_policy", {"asset_id": "V1"}, native=provider == "ollama")]}
        else:
            assert len(wire) == 3 and not body["tools"]
            assert schema["$defs"]["FactClaim"]["properties"]["source"]["enum"] == [
                "event", "policy", "sensor_health", "model_observation"]
            assert schema["$defs"]["Hypothesis"]["properties"]["limitations"]["minItems"] == 1
            assert schema["properties"]["facts"]["maxItems"] == 4
            assert schema["properties"]["hypotheses"]["maxItems"] == 1
            assert schema["properties"]["recommendations"]["maxItems"] == 3
            assert schema["additionalProperties"] is False
            if provider == "openai_compatible":
                assert body["response_format"]["type"] == "json_schema"
                assert body["response_format"]["json_schema"]["strict"] is True
            message = {"role": "assistant", "content": answer([claim])}
        return httpx.Response(200, json={"choices": [{"message": message}]} if provider == "openai_compatible"
                              else {"message": message})

    async def run():
        config = AgentConfig(provider=provider, base_url="http://127.0.0.1:1234/v1" if provider == "openai_compatible"
                             else "http://127.0.0.1:11434", model="test-double")
        client = LocalModelClient(config, httpx.MockTransport(transport))
        try:
            result = await run_analysis(client, snapshot(), config)
            assert result["facts"][0]["source"] == "event"
            assert result["facts"][0]["value"] == 20.0
            assert len(wire) == config.max_model_requests == 3
            assert [row["tool"] for row in result["tool_trace"]] == (
                ["get_incident"] if early_invalid else ["get_incident", "get_asset_policy"])
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("provider", ["openai_compatible", "ollama"])
@pytest.mark.parametrize("final_truncated", [False, True])
def test_truncated_answer_uses_bounded_structured_retry_and_never_partial_success(provider, final_truncated):
    wire = []
    claim = {"source": "event", "id": "demo-position-0010", "field": "payload.x", "value": 20.0}

    def transport(request):
        body = json.loads(request.content)
        wire.append(body)
        if len(wire) == 1:
            message = {"role": "assistant", "content": "", "tool_calls": [tool_call(native=provider == "ollama")]}
            reason = "tool_calls"
        else:
            # Even parseable content must not become a result when the provider
            # reports it was cut short; no Python repair or partial acceptance.
            message = {"role": "assistant", "content": answer([claim])}
            reason = "length" if len(wire) == 2 or final_truncated else "stop"
        if len(wire) == 3:
            assert not body["tools"]
            schema = body["format"] if provider == "ollama" else body["response_format"]["json_schema"]["schema"]
            assert schema["properties"]["facts"]["maxItems"] == 4
            assert (body["options"]["num_predict"] if provider == "ollama" else body["max_tokens"]) == 1024
        payload = ({"message": message, "done_reason": reason} if provider == "ollama" else
                   {"choices": [{"message": message, "finish_reason": reason}]})
        return httpx.Response(200, json=payload)

    async def run():
        config = AgentConfig(provider=provider, model="test-double",
                             base_url="http://127.0.0.1:11434" if provider == "ollama" else "http://127.0.0.1:1234/v1")
        client = LocalModelClient(config, httpx.MockTransport(transport))
        try:
            if final_truncated:
                with pytest.raises(AgentError) as error:
                    await run_analysis(client, snapshot(), config)
                assert error.value.code == "model_reply_invalid"
                assert error.value.details == {"finish_reason": "length", "output_token_limit": 1024}
            else:
                result = await run_analysis(client, snapshot(), config)
                assert result["facts"][0]["value"] == 20.0
                assert [row["tool"] for row in result["tool_trace"]] == ["get_incident"]
            assert len(wire) == config.max_model_requests == 3
        finally:
            await client.close()

    asyncio.run(run())


def test_model_anomaly_requires_linked_mlp_schema_immediately_after_incident_tool():
    data = snapshot().export()
    observation = {"observation_id": "observation-test", "status": "anomaly", "score": .91,
                   "threshold": .8, "evidence_event_ids": ["demo-position-0010"]}
    data["incident"]["type"] = "model_anomaly"
    data["incident"]["details"] = {"observation_id": observation["observation_id"]}
    data["observations"] = [observation]
    snap = FrozenSnapshot(data)

    class Client:
        def __init__(self):
            self.deadlines = []

        async def chat(self, messages, tools, deadline, response_schema=None):
            self.deadlines.append(deadline)
            if len(self.deadlines) == 1:
                assert response_schema is None and tools[0]["function"]["name"] == "get_incident"
                return {"role": "assistant", "content": "", "tool_calls": [tool_call()]}
            assert len(self.deadlines) == 2 and not tools and response_schema is not None
            claim = response_schema["$defs"]["FactClaim"]["properties"]
            assert claim["source"]["enum"] == ["model_observation"]
            assert claim["id"]["enum"] == [observation["observation_id"]]
            assert claim["field"]["enum"] == ["status", "score", "threshold"]
            assert claim["value"]["enum"] == [observation[field] for field in ("status", "score", "threshold")]
            assert response_schema["properties"]["facts"]["minItems"] == response_schema["properties"]["facts"]["maxItems"] == 3
            return {"role": "assistant", "content": answer([
                {"source": "model_observation", "id": observation["observation_id"],
                 "field": field, "value": observation[field]} for field in ("status", "score", "threshold")])}

    async def run():
        client = Client()
        result = await run_analysis(client, snap, AgentConfig(model="test-double"))
        assert len(client.deadlines) == 2 and len(set(client.deadlines)) == 1
        assert {fact["field"] for fact in result["facts"]} == {"status", "score", "threshold"}
        assert result["evidence_event_ids"] == ["demo-position-0010"]
        assert [item["tool"] for item in result["tool_trace"]] == ["get_incident"]

    asyncio.run(run())


@pytest.mark.parametrize("final_value,expected_code", [(None, "model_reply_invalid"), (999, "invalid_evidence")])
def test_structured_final_cannot_bypass_validation_or_request_budget(final_value, expected_code):
    wire = []

    def transport(request):
        body = json.loads(request.content)
        wire.append(body)
        if len(wire) < 3:
            message = {"role": "assistant", "content": "", "tool_calls": [tool_call()]}
        else:
            content = "{}" if final_value is None else answer([
                {"source": "event", "id": "demo-position-0010", "field": "payload.x", "value": final_value}])
            message = {"role": "assistant", "content": content}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    async def run():
        config = AgentConfig(model="test-double")
        client = LocalModelClient(config, httpx.MockTransport(transport))
        try:
            with pytest.raises(AgentError) as exc:
                await run_analysis(client, snapshot(), config)
            assert exc.value.code == expected_code
            assert len(wire) == 3
        finally:
            await client.close()
    asyncio.run(run())


def test_ordinary_reply_is_not_forced_to_json_and_schema_is_not_combined_with_tools():
    wire = []

    def transport(request):
        body = json.loads(request.content)
        wire.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": "READY"}}]})

    async def run():
        client = LocalModelClient(AgentConfig(model="test-double"), httpx.MockTransport(transport))
        try:
            reply = await client.chat([{"role": "user", "content": "READY"}], [], time.monotonic() + 1)
            assert reply["content"] == "READY" and "response_format" not in wire[0]
            with pytest.raises(AgentError) as exc:
                await client.chat([], [{}], time.monotonic() + 1, response_schema={"type": "object"})
            assert exc.value.code == "invalid_config" and len(wire) == 1
        finally:
            await client.close()
    asyncio.run(run())


def test_early_false_evidence_is_rejected_without_format_retry():
    wire = []

    def transport(request):
        wire.append(json.loads(request.content))
        message = {"role": "assistant", "content": "", "tool_calls": [tool_call()]} if len(wire) == 1 else {
            "role": "assistant", "content": answer([
                {"source": "event", "id": "demo-position-0010", "field": "payload.x", "value": 999}])}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    async def run():
        config = AgentConfig(model="test-double")
        client = LocalModelClient(config, httpx.MockTransport(transport))
        try:
            with pytest.raises(AgentError) as exc:
                await run_analysis(client, snapshot(), config)
            assert exc.value.code == "invalid_evidence" and len(wire) == 2
        finally:
            await client.close()
    asyncio.run(run())


def test_format_retry_keeps_the_original_whole_analysis_deadline():
    class SlowFinal:
        def __init__(self):
            self.deadlines = []

        async def chat(self, messages, tools, deadline, response_schema=None):
            self.deadlines.append(deadline)
            if len(self.deadlines) == 1:
                return {"role": "assistant", "content": "", "tool_calls": [tool_call()]}
            if len(self.deadlines) == 2:
                return {"role": "assistant", "content": "{}"}
            assert response_schema is not None and not tools
            await asyncio.sleep(1)

    async def run():
        client = SlowFinal()
        with pytest.raises(AgentError) as exc:
            await run_analysis(client, snapshot(), AgentConfig(max_execution_seconds=.1))
        assert exc.value.code == "execution_timeout"
        assert len(client.deadlines) == 3 and len(set(client.deadlines)) == 1
    asyncio.run(run())


@pytest.mark.parametrize("provider", ["openai_compatible", "ollama"])
@pytest.mark.parametrize("tool_name", ["get_event_history", "get_sensor_health"])
def test_compact_tool_messages_keep_history_bounds_and_sensor_null(provider, tool_name):
    from src.agent.tools import ToolSession
    is_history = tool_name == "get_event_history"
    snap = snapshot("INC-ZONE-1" if is_history else "INC-NEVER-STARTED")
    args = ({"asset_id": "V1", "since": "2026-10-07T09:00:00Z", "until": snap.as_of, "limit": 1}
            if is_history else {"sensor_id": "POS-V2"})
    full_session = ToolSession(snap)
    full_session.execute("get_incident", json.dumps({"incident_id": snap.incident_id}))
    full_result = full_session.execute(tool_name, json.dumps(args))
    if is_history:
        row = full_result["events"][0]
        claim = {"source": "event", "id": row["event_id"], "field": "payload.x", "value": row["payload"]["x"]}
    else:
        claim = {"source": "sensor_health", "id": "POS-V2", "field": "last_received_at", "value": None}
    wire = []

    def transport(request):
        body = json.loads(request.content)
        wire.append(body)
        if len(wire) < 3:
            message = {"role": "assistant", "content": "", "tool_calls": [
                tool_call("get_incident" if len(wire) == 1 else tool_name,
                          {"incident_id": snap.incident_id} if len(wire) == 1 else args,
                          native=provider == "ollama")]}
        else:
            message = {"role": "assistant", "content": answer([claim])}
        return httpx.Response(200, json={"message": message} if provider == "ollama"
                              else {"choices": [{"message": message}]})

    async def run():
        config = AgentConfig(provider=provider, model="test-double",
                             base_url="http://127.0.0.1:11434" if provider == "ollama" else "http://127.0.0.1:1234/v1")
        client = LocalModelClient(config, httpx.MockTransport(transport))
        try:
            return await run_analysis(client, snap, config)
        finally:
            await client.close()

    result = asyncio.run(run())
    assert len(wire) == 3
    tool_messages = [m for m in wire[-1]["messages"] if m["role"] == "tool"]
    payload = json.loads(tool_messages[-1]["content"])
    if is_history:
        assert payload["events"] == full_result["events"] and len(payload["events"]) == 1
        assert payload["truncated"] == full_result["truncated"]
        assert payload["history_bounds"] == full_result["history_bounds"]
        assert payload["snapshot_bounded"] is True
        assert result["evidence_event_ids"] == [claim["id"]]
    else:
        assert payload["sensor_health"] == snap.health["POS-V2"]
        assert payload["sensor_health"]["last_received_at"] is None
        assert result["evidence_event_ids"] == []
    assert result["facts"][0]["value"] == claim["value"]
    assert result["evidence_refs"] == [full_session.ref(claim["source"], claim["id"])]
