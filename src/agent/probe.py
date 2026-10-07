"""Verify the actual local runtime, ordinary reply, tool execution and evidence result."""
import argparse
import asyncio
import json
import platform
import os
import time
from pathlib import Path

from .config import AgentConfig, load_env_file
from .errors import AgentError
from .loop import run_analysis
from .model_client import LocalModelClient
from .providers import FixtureProvider, utc_now


async def probe(config, fixture, incident_id):
    client = LocalModelClient(config)
    started = time.perf_counter()
    report = {"status": "BLOCKED", "checked_at": utc_now(), "provider": config.provider,
              "base_url": config.base_url, "configured_model": config.model,
              "python": platform.python_version(), "machine": platform.machine(),
              "data_provider": "explicit fixture", "live_backend": False}
    report["hardware"] = {"cpu": platform.processor() or platform.machine(), "logical_cpus": os.cpu_count(),
                          "ram_bytes": None, "gpu_vram": "record separately on target machine"}
    if hasattr(os, "sysconf"):
        try:
            report["hardware"]["ram_bytes"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError):
            pass
    try:
        report["available_models"] = await client.list_models()
        await client.ensure_available()
        greeting = await client.chat([{"role": "user", "content": "Ответь одним словом: готов. /no_think"}], [],
                                     time.monotonic() + config.max_execution_seconds)
        if not greeting.get("content") or greeting.get("tool_calls"):
            raise AgentError("model_reply_invalid", "Ordinary model reply failed.")
        snapshot = await FixtureProvider(fixture).snapshot(incident_id)
        result = await run_analysis(client, snapshot, config)
        if not result["tool_trace"]:
            raise AgentError("tool_support_missing", "No actual tool was executed.")
        report.update(status="PASS", result=result, real_model_requests=True)
    except AgentError as exc:
        report["error"] = exc.as_dict()
    finally:
        report["seconds"] = time.perf_counter() - started
        await client.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file")
    parser.add_argument("--fixture", default="tests/fixtures/agent_v2.json")
    parser.add_argument("--incident-id", default="INC-ZONE-1")
    parser.add_argument("--output", default="artifacts/local/runtime-probe.json")
    args = parser.parse_args()
    if args.env_file:
        load_env_file(args.env_file)
    report = asyncio.run(probe(AgentConfig.from_env(), args.fixture, args.incident_id))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
