"""HTTP test double for the QA runner, not the enterprise application."""

import contextlib
import copy
import io
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT))
from scripts import qa_run as qa


class DemoDouble:
    def __init__(self, fault=None):
        self.fault = fault
        self.events = {}
        self.incidents = []
        self.assets = [{"asset_id": "V1", "x": 5, "y": 5}]
        self.last_heartbeat = None
        self.threshold = 1
        self.agent = "ready" if fault == "agent-ready" else "unavailable"
        self.site = {
            "sensors": [{"id": key} for key in ("ACCESS-O1", "POS-V1", "HB-QA")],
            "assets": [{"id": key} for key in ("V1", "U1", "U2")],
            "buildings": [{"id": "O1"}], "zones": [{"id": "Z1"}],
        }

    def active(self, kind):
        return [row for row in self.incidents if row["type"] == kind and row["condition_active"]]

    def add(self, kind, evidence, **fields):
        row = {"incident_id": f"INC-{len(self.incidents) + 1}", "type": kind,
               "condition_active": True, "status": "open", "evidence_event_ids": evidence,
               "detected_at": datetime.now(timezone.utc).isoformat(), **fields}
        self.incidents.append(row)

    def tick(self):
        if self.last_heartbeat:
            received, started = self.last_heartbeat
            required = 0.1 if self.fault == "early-offline" else self.threshold
            if time.monotonic() - started >= required and not self.active("sensor_offline"):
                self.add("sensor_offline", [], sensor_id="HB-QA",
                         details={"last_received_at": received, "threshold_seconds": self.threshold})

    def handle(self, method, path, event):
        self.tick()
        if method == "GET":
            if path == "/api/health":
                return 200, {"rules": "ready", "agent": self.agent, "ml": "not_trained"}
            if path == "/api/site":
                return 200, self.site
            if path == "/api/incidents":
                if self.fault == "bad-list":
                    return 200, {"incidents": "wrong"}
                return 200, self.incidents
            if path == "/api/assets":
                return 200, self.assets
        if method == "POST" and path.endswith("/analysis"):
            return 503, {"code": "agent_unavailable", "message": "Ollama off", "details": {}}
        if method == "POST" and path == "/api/events":
            duplicate = event["event_id"] in self.events
            if duplicate and self.fault != "duplicate-alarm":
                return 200, {"event_id": event["event_id"], "duplicate": True,
                             "received_at": self.events[event["event_id"]]}
            received = datetime.now(timezone.utc).isoformat()
            self.events[event["event_id"]] = received
            kind, payload = event["type"], event["payload"]
            if kind == "access" and payload["employee_id"] == "U1" and self.fault != "missing-alarm":
                evidence = [] if self.fault == "missing-evidence" else [event["event_id"]]
                self.add("unauthorized_access", evidence, employee_id="U1",
                         building_id="W2" if self.fault == "wrong-building" else payload["building_id"])
            if kind == "position":
                self.assets[0].update({"x": payload["x"], "y": payload["y"]})
                inside = 20 <= payload["x"] <= 40 and 20 <= payload["y"] <= 40
                if inside and (not self.active("forbidden_zone") or self.fault == "zone-duplicates"):
                    self.add("forbidden_zone", [event["event_id"]], asset_id="V1", zone_id="Z1")
                elif not inside:
                    for row in self.active("forbidden_zone"):
                        row["condition_active"] = False
            if kind == "heartbeat":
                self.last_heartbeat = received, time.monotonic()
                for row in self.active("sensor_offline"):
                    row["condition_active"] = False
            return (200 if duplicate else 201), {"event_id": event["event_id"],
                                                  "duplicate": duplicate, "received_at": received}
        return 404, {"code": "not_found"}


@contextlib.contextmanager
def serve(double):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def call(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            status, value = double.handle(self.command, self.path, json.loads(body) if body else None)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        do_GET = call
        do_POST = call

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.config = qa.load_config(ROOT / "tests/fixtures/qa_config.example.json")
        self.config["heartbeat"]["threshold_seconds"] = 1

    def run_case(self, case, fault=None):
        with serve(DemoDouble(fault)) as url, contextlib.redirect_stdout(io.StringIO()):
            runner = qa.Suite(qa.Client(url), self.config, "test", wait=0.25, observe=0.15, poll=0.02)
            return next(row for row in runner.run({case}) if row.case_id == case)

    def test_all_scenarios_over_real_http(self):
        calls = []
        def simulator_factory(**event):
            calls.append(event["event_id"])
            return event
        with serve(DemoDouble()) as url, contextlib.redirect_stdout(io.StringIO()):
            suite = qa.Suite(qa.Client(url), self.config, "all", wait=0.4, observe=0.12, poll=0.02,
                             event_factory=simulator_factory)
            results = suite.run({row[0] for row in qa.CASES})
        self.assertEqual([row.status for row in results], ["PASS"] * 6)
        self.assertGreater(len(calls), 6)
        self.assertEqual(len(calls), len(set(calls)))
        self.assertTrue(all(row.requests for row in results))

    def test_wrong_building_is_not_pass(self):
        self.assertEqual(self.run_case("Q06", "wrong-building").status, "FAIL")

    def test_missing_evidence_is_not_pass(self):
        self.assertEqual(self.run_case("Q06", "missing-evidence").status, "FAIL")

    def test_missing_alarm_is_not_pass(self):
        self.assertEqual(self.run_case("Q06", "missing-alarm").status, "FAIL")

    def test_replayed_event_cannot_create_alarm(self):
        self.assertEqual(self.run_case("Q11", "duplicate-alarm").status, "FAIL")

    def test_continuous_zone_condition_is_not_multiple_incidents(self):
        self.assertEqual(self.run_case("Q03", "zone-duplicates").status, "FAIL")

    def test_offline_alarm_cannot_be_early(self):
        self.assertEqual(self.run_case("Q08", "early-offline").status, "FAIL")

    def test_running_qwen_is_blocked_not_pass(self):
        self.assertEqual(self.run_case("Q27", "agent-ready").status, "BLOCKED")

    def test_malformed_api_is_not_empty_success(self):
        self.assertEqual(self.run_case("Q06", "bad-list").status, "FAIL")

    def test_unknown_fixture_is_blocked(self):
        self.config["heartbeat"]["sensor_id"] = "UNKNOWN"
        with serve(DemoDouble()) as url, contextlib.redirect_stdout(io.StringIO()):
            results = qa.Suite(qa.Client(url), self.config, "unknown").run({"Q06"})
        self.assertEqual(results[1].status, "BLOCKED")

    def test_incomplete_list_is_blocked(self):
        with self.assertRaises(qa.Blocked):
            qa.records({"items": [], "has_more": True}, "incidents")
        with self.assertRaises(qa.Blocked):
            qa.records({"items": [], "total": 3}, "incidents")

    def test_cli_failure_returns_one_and_saves_report(self):
        with serve(DemoDouble("wrong-building")) as url, tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            report = Path(folder) / "result.html"
            code = qa.main(["--base-url", url, "--report", str(report), "--cases", "Q06", "--wait", "0.1", "--allow-demo-writes"])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(report.with_suffix(".json").read_text(encoding="utf-8"))["results"][1]["status"], "FAIL")

    def test_cli_unavailable_server_is_blocked(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            report = Path(folder) / "result.html"
            code = qa.main(["--base-url", "http://127.0.0.1:1", "--report", str(report), "--allow-demo-writes"])
            self.assertEqual(code, 2)
            self.assertEqual({r["status"] for r in json.loads(report.with_suffix(".json").read_text(encoding="utf-8"))["results"]}, {"BLOCKED"})

    def test_invalid_url_credentials_do_not_leak_to_report(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            report = Path(folder) / "result.html"
            code = qa.main(["--base-url", "http://user:secret-password@127.0.0.1:8000", "--report", str(report), "--allow-demo-writes"])
            self.assertEqual(code, 2)
            self.assertNotIn("secret-password", report.read_text(encoding="utf-8"))
            self.assertNotIn("secret-password", report.with_suffix(".json").read_text(encoding="utf-8"))

    def test_event_factory_cannot_change_facts(self):
        with serve(DemoDouble()) as url, contextlib.redirect_stdout(io.StringIO()):
            suite = qa.Suite(qa.Client(url), self.config, "bad-factory", event_factory=lambda **e: {**e, "type": "heartbeat"})
            results = suite.run({"Q06"})
        self.assertEqual(results[1].status, "FAIL")

    def test_default_requires_demo_write_opt_in_and_returns_two(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            report = Path(folder) / "result.html"
            code = qa.main(["--report", str(report)])
            parsed = json.loads(report.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(code, 2)
            self.assertEqual({row["status"] for row in parsed["results"]}, {"BLOCKED"})

    def test_report_escapes_server_content_and_preserves_statuses(self):
        result = qa.Result("Q06", "<script>bad</script>", "expected", "FAIL", '<img src=x onerror="bad">', 0.1, [], [], [])
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "report.html"
            qa.write_report(report, {"server_sha": "unknown"}, [result])
            content = report.read_text(encoding="utf-8")
            self.assertNotIn("<script>bad", content)
            self.assertIn("&lt;img", content)
            self.assertEqual(json.loads(report.with_suffix(".json").read_text(encoding="utf-8"))["results"][0]["status"], "FAIL")

    def test_configuration_rejects_non_finite_coordinates(self):
        bad = copy.deepcopy(self.config)
        bad["forbidden_zone"]["inside"]["x"] = float("nan")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError):
                qa.load_config(path)


if __name__ == "__main__":
    unittest.main()
