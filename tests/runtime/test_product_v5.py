"""V5 HTTP acceptance: authenticated checkpoint, shifts, activity and CSV projections."""
import csv
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.core.auth import COOKIE_NAME
from tests.runtime.isolated_app import ADMIN_PASSWORD, create_app
from src.core.service import stamp


SOURCE_KEY = "product-test-source-key-0123456789"
PASSWORD = "Product-v5-password-2026"


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds=1):
        self.now += timedelta(seconds=seconds)


class ProductV5Acceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.environment = patch.dict(os.environ, {"DISPATCH_ENABLE_AUTH": "1", "DISPATCH_SOURCE_KEY": SOURCE_KEY,
            "DISPATCH_TRUST_ENCRYPTED_TUNNEL": "0", "DISPATCH_ENABLE_SIMULATOR": "0", "DISPATCH_ENABLE_ML": "0",
            "DISPATCH_ENABLE_AGENT": "0", "DISPATCH_ENABLE_DEMO_TRAFFIC": "1", "DEMO_AUTOSTART": "0"})
        self.environment.start()
        self.app = create_app(db_path=Path(self.temp.name) / "product.db", enable_scheduler=False, clock=self.clock)
        self.auth = self.app.state.auth
        self.service = self.app.state.service
        for number in range(1, 4):
            self.auth.create_user("dispatcher." + str(number), "Диспетчер " + str(number), PASSWORD,
                                  operator_id="dispatcher-" + str(number), status="active")
        self.clients = []
        self.sequence = 0

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.environment.stop()
        self.temp.cleanup()

    def client(self, operator=None, admin=False):
        client = TestClient(self.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50200 + len(self.clients)))
        self.clients.append(client)
        if operator or admin:
            username = "admin" if admin else "dispatcher." + str(operator)
            response = client.post("/api/auth/login", json={"username": username, "password": ADMIN_PASSWORD if admin else PASSWORD})
            self.assertEqual(response.status_code, 200, response.text)
            return client, {"X-CSRF-Token": response.json()["csrf_token"], "X-Expected-User": response.json()["user"]["id"]}
        return client

    def passage(self, client, employee="U1", direction="in", event_id=None):
        self.sequence += 1
        event = {"event_id": event_id or f"product-gate-{self.sequence:04d}", "event_time": stamp(self.clock()),
                 "sensor_id": "ACCESS-G1", "type": "access", "demo": True,
                 "payload": {"employee_id": employee, "building_id": "G1", "direction": direction,
                             "access_kind": "passage_confirmed"}}
        response = client.post("/api/events", json=event, headers={"X-Source-Key": SOURCE_KEY})
        self.assertEqual(response.status_code, 201, response.text)
        return event

    def claim(self, client, headers):
        incident = self.service.list_incidents()[0]
        response = client.patch("/api/incidents/" + incident["incident_id"], headers=headers,
            json={"action": "claim", "expected_revision": incident["dispatch_revision"], "request_id": "product-claim"})
        return response, incident

    def test_new_operational_routes_require_real_session(self):
        client = self.client()
        for endpoint in ("/api/checkpoint/journal", "/api/checkpoint/export.csv", "/api/shifts/current",
                         "/api/operator-activity", "/api/dispatch-history/export.csv", "/api/dispatch-history/export.csv?scope=all"):
            response = client.get(endpoint, headers={"X-Demo-Operator": "dispatcher-3"})
            self.assertEqual(response.status_code, 401, endpoint)

    def test_three_cookie_sessions_keep_their_roles_despite_forged_profile_header(self):
        source = self.client()
        self.passage(source, "U4")
        clients = [self.client(number) for number in (1, 2, 3)]
        self.assertEqual(len({client.cookies.get(COOKIE_NAME) for client, _ in clients}), 3)
        one, one_headers = clients[0]
        incident_id = self.service.list_incidents()[0]["incident_id"]
        foreign = one.get("/api/incidents/" + incident_id, headers={"X-Demo-Operator": "dispatcher-3"})
        self.assertEqual(foreign.status_code, 403, foreign.text)
        self.assertEqual(foreign.json()["code"], "incident_sector_required")
        self.assertEqual(one.get("/api/incidents?scope=all", headers={"X-Demo-Operator": "dispatcher-3"}).json(), [])
        before = self.service.get_incident(incident_id)
        forbidden, _ = self.claim(one, {**one_headers, "X-Demo-Operator": "dispatcher-3"})
        # A valid session cannot gain sector access through a forged profile header.
        # Authorization fails before the command/revision conflict checks.
        self.assertEqual(forbidden.status_code, 403, forbidden.text)
        self.assertEqual(forbidden.json()["code"], "incident_sector_required")
        after = self.service.get_incident(incident_id)
        for field in ("assigned_operator_id", "dispatch_revision", "history"):
            self.assertEqual(after[field], before[field], field)
        three, three_headers = clients[2]
        own = three.get("/api/incidents/" + incident_id, headers={"X-Demo-Operator": "dispatcher-1"})
        self.assertTrue(own.json()["can_claim"])
        for endpoint in ("/api/checkpoint/journal", "/api/checkpoint/export.csv", "/api/operator-activity", "/api/dispatch-history/export.csv"):
            stale_tab = three.get(endpoint, headers={"X-Expected-User": one_headers["X-Expected-User"]})
            self.assertEqual(stale_tab.status_code, 409, endpoint)
            self.assertEqual(stale_tab.json()["code"], "session_identity_changed")
        accepted, incident = self.claim(three, {**three_headers, "X-Demo-Operator": "dispatcher-1"})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["assigned_operator_id"], "dispatcher-3")
        claim_history = [row for row in accepted.json()["history"] if row["action"] == "claim"]
        self.assertEqual(claim_history[0]["actor_operator_id"], "dispatcher-3")
        for number, (client, _) in enumerate(clients, 1):
            summary = client.get("/api/operator-activity", headers={"X-Demo-Operator": "dispatcher-" + str(4 - number)})
            self.assertEqual(summary.status_code, 200, summary.text)
            self.assertEqual(summary.json()["operator_id"], "dispatcher-" + str(number))
            self.assertEqual(summary.json()["claimed"], int(number == 3))
            exported = client.get("/api/dispatch-history/export.csv", headers={"X-Demo-Operator": "dispatcher-3"})
            self.assertEqual(exported.status_code, 200, exported.text)
            rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig")), delimiter=";"))
            self.assertTrue(all(row["Диспетчер"] == {1:"Диспетчер 1 — логистика",2:"Диспетчер 2 — производство",3:"Диспетчер 3 — КПП"}[number] for row in rows))
            self.assertEqual(len(rows), int(number == 3))

    def test_global_dispatch_csv_is_admin_only_and_includes_real_operator_history(self):
        source = self.client()
        self.passage(source, "U4")
        dispatcher, headers = self.client(3)
        accepted, incident = self.claim(dispatcher, headers)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(dispatcher.get("/api/dispatch-history/export.csv?scope=all").status_code, 403)
        admin, _ = self.client(admin=True)
        response = admin.get("/api/dispatch-history/export.csv?scope=all")
        self.assertEqual(response.status_code, 200, response.text)
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")), delimiter=";"))
        self.assertTrue(any(row["Действие"] == "Обнаружено" for row in rows))
        self.assertTrue(any(row["Действие"] == "Принята ответственность" and row["Диспетчер"] == "Диспетчер 3 — КПП" for row in rows))
        self.assertEqual(response.headers["x-export-limit"], "1000")

    def test_csrf_rejection_does_not_create_dispatch_history_and_correct_token_works(self):
        source = self.client()
        self.passage(source, "U4")
        dispatcher, headers = self.client(3)
        rejected, incident = self.claim(dispatcher, {"X-Demo-Operator": "dispatcher-3"})
        self.assertEqual(rejected.status_code, 403, rejected.text)
        self.assertEqual(rejected.json()["code"], "csrf_required")
        rejected, _ = self.claim(dispatcher, {"X-CSRF-Token": "wrong-session-token"})
        self.assertEqual(rejected.status_code, 403)
        self.assertFalse(any(row["action"] == "claim" for row in self.service.get_incident(incident["incident_id"])["history"]))
        accepted, _ = self.claim(dispatcher, headers)
        self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_checkpoint_search_direction_permission_time_and_bounded_export(self):
        source = self.client()
        first = self.passage(source, "U1")
        self.clock.advance()
        self.passage(source, "U2")
        self.clock.advance()
        self.passage(source, "U4")
        self.clock.advance()
        last = self.passage(source, "U1", "out")
        dispatcher, _ = self.client(3)
        response = dispatcher.get("/api/checkpoint/journal", params={"q": "U1", "limit": 1, "offset": 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 2)
        self.assertEqual(response.json()["items"][0]["event_id"], first["event_id"])
        self.assertEqual(response.json()["occupancy"]["observed_inside_count"], 2)
        self.assertFalse(response.json()["occupancy"]["initial_state_known"])
        by_name = dispatcher.get("/api/checkpoint/journal", params={"q": self.service.assets_by_id["U4"]["name"]})
        self.assertEqual(by_name.json()["total"], 1)
        literal = dispatcher.get("/api/checkpoint/journal", params={"q": "%_"})
        self.assertEqual(literal.json()["total"], 0)
        violation = dispatcher.get("/api/checkpoint/journal", params={"direction": "in", "permission": "violation"})
        self.assertEqual(violation.json()["items"][0]["employee_id"], "U4")
        since_last = dispatcher.get("/api/checkpoint/journal", params={"since": last["event_time"]})
        self.assertEqual(since_last.json()["total"], 1)
        exported = dispatcher.get("/api/checkpoint/export.csv", params={"q": "U1", "direction": "out"})
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertTrue(exported.content.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(exported.headers["content-type"].startswith("text/csv"))
        self.assertEqual(exported.headers["x-export-limit"], "200")
        self.assertEqual(exported.headers["x-export-offset"], "0")
        rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual([row["Номер события"] for row in rows], [last["event_id"]])
        for endpoint in ("/api/checkpoint/journal", "/api/checkpoint/export.csv"):
            for parameters in ({"limit": 201}, {"offset": 100001}, {"q": "x" * 101}, {"direction": "enter"},
                               {"permission": "denied"}, {"since": "bad"}):
                self.assertEqual(dispatcher.get(endpoint, params=parameters).status_code, 422, (endpoint, parameters))

    def test_csv_formula_escape_is_applied_to_real_history_reason(self):
        source = self.client()
        self.passage(source, "U4")
        dispatcher, headers = self.client(3)
        claimed, incident = self.claim(dispatcher, headers)
        self.assertEqual(claimed.status_code, 200, claimed.text)
        reason = '=HYPERLINK("https://example.test/", "text, with comma")'
        response = dispatcher.patch("/api/incidents/" + incident["incident_id"], headers=headers,
            json={"action": "record_response", "expected_revision": claimed.json()["dispatch_revision"],
                  "request_id": "product-reason", "response_code": "contacted", "reason": reason})
        self.assertEqual(response.status_code, 200, response.text)
        exported = dispatcher.get("/api/dispatch-history/export.csv")
        self.assertEqual(exported.status_code, 200, exported.text)
        rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(rows[0]["Комментарий"], "'" + reason)

    def test_shift_api_links_confirmed_people_to_actual_transport_without_early_motion(self):
        dispatcher, _ = self.client(3)
        runner = self.app.state.demo
        runner._prepare("shift")
        baseline = None
        for elapsed in range(8):
            self.clock.advance()
            runner.emit_frame(elapsed)
            response = dispatcher.get("/api/shifts/current")
            self.assertEqual(response.status_code, 200, response.text)
            shift = response.json()["shift"]
            assets = dispatcher.get("/api/assets").json()
            positions = {asset["id"]: (asset["x"], asset["y"]) for asset in assets if asset["type"] == "vehicle"}
            baseline = baseline or positions
            if elapsed < 5:
                self.assertEqual(positions, baseline)
                self.assertEqual(shift["phase"], "gate_checks")
                self.assertEqual(shift["transport_events_count"], 0)
            if elapsed == 7:
                self.assertEqual(shift["gate_count"], 3)
                self.assertEqual(shift["expected_gate_count"], 3)
                self.assertEqual(shift["phase"], "transport")
                self.assertNotEqual(positions, baseline)
                self.assertEqual(set(shift["transport_asset_ids"]), {"V1", "V2", "V3"})
                self.assertTrue(shift["recent_transport_events"])
        journal = dispatcher.get("/api/checkpoint/journal").json()
        self.assertEqual(journal["total"], 3)
        self.assertEqual({row["shift_id"] for row in journal["items"]}, {shift["shift_id"]})
        self.assertTrue(all(row["permission"] == "allowed" for row in journal["items"]))


if __name__ == "__main__":
    unittest.main()
