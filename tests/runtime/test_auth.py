"""Real default-on authentication and SQLite security acceptance."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.core.auth import COOKIE_NAME, AuthManager, PASSWORD_ITERATIONS, password_matches
from src.core.main import create_app
from src.core.service import stamp


class AuthAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"DISPATCH_ENABLE_AUTH": "1", "DISPATCH_TRUST_ENCRYPTED_TUNNEL": "0", "DISPATCH_SOURCE_KEY": ""})
        self.env.start()
        self.app = create_app(db_path=Path(self.temp.name) / "dispatch.db", enable_scheduler=False)
        self.auth = self.app.state.auth
        self.admin = self.auth.create_user("admin", "Администратор", "Admin-password-2026", role="admin", status="active")
        self.dispatcher = self.auth.create_user("dispatcher.one", "Диспетчер 1", "Dispatcher-password-2026", operator_id="dispatcher-1", status="active")
        self.client = self.new_client()
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def new_client(self, secure=False, remote=False):
        return TestClient(self.app, base_url=("https" if secure else "http") + "://127.0.0.1:8000",
                          client=("198.51.100.12" if remote else "127.0.0.1", 50100))

    def login(self, client=None, admin=False):
        client = client or self.client
        response = client.post("/api/auth/login", json={"username": "admin" if admin else "dispatcher.one",
                               "password": "Admin-password-2026" if admin else "Dispatcher-password-2026"})
        self.assertEqual(response.status_code, 200, response.text)
        return {"X-CSRF-Token": response.json()["csrf_token"]}, response

    def event(self):
        return {"event_id": "auth-source-test", "event_time": stamp(), "sensor_id": "POS-V1", "type": "position",
                "demo": True, "payload": {"asset_id": "V1", "x": 30, "y": 60}}

    def test_default_protects_read_write_and_exposes_only_safe_status(self):
        self.assertTrue(self.client.get("/api/auth/status").json()["configured"])
        self.assertTrue(self.client.get("/api/health").json()["auth"]["enabled"])
        self.assertEqual(self.client.get("/").status_code, 200)
        for endpoint in ("/api/site", "/api/assets", "/api/sensors", "/api/incidents", "/api/admin/users", "/api/demo/status"):
            self.assertEqual(self.client.get(endpoint).status_code, 401)
        response = self.client.post("/api/operator-presence", json={"session_id": "unauth"}, headers={"X-Demo-Operator": "dispatcher-3"})
        self.assertEqual(response.status_code, 401)

    def test_passwords_salted_and_session_tokens_stored_only_as_hash(self):
        headers, response = self.login()
        token = self.client.cookies.get(COOKIE_NAME)
        with self.auth.store.read() as db:
            row = db.execute("SELECT password_hash FROM auth_users WHERE id=?", (self.dispatcher["id"],)).fetchone()
            session = dict(db.execute("SELECT * FROM auth_sessions").fetchone())
        self.assertIn(str(PASSWORD_ITERATIONS), row[0])
        self.assertNotIn("Dispatcher-password", row[0])
        self.assertTrue(password_matches("Dispatcher-password-2026", row[0]))
        self.assertNotIn(token, str(session))
        self.assertEqual(session["token_hash"], self.auth.token_hash(token))
        self.assertEqual(self.client.get("/api/auth/me").json()["csrf_token"], headers["X-CSRF-Token"])
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)

    def test_registration_is_pending_and_admin_approval_assigns_profile(self):
        response = self.client.post("/api/auth/register", json={"username": "New.User", "name": "Новый диспетчер", "password": "New-password-2026"})
        self.assertEqual(response.status_code, 201)
        user = response.json()["user"]
        self.assertEqual(user["username"], "new.user")
        self.assertEqual(user["status"], "pending")
        self.assertIsNone(user["operator_id"])
        rejected = self.client.post("/api/auth/login", json={"username": "new.user", "password": "New-password-2026"})
        self.assertEqual(rejected.status_code, 403)
        headers, _ = self.login(admin=True)
        missing = self.client.patch("/api/admin/users/" + user["id"], json={"status": "active"}, headers=headers)
        self.assertEqual(missing.status_code, 422)
        approved = self.client.patch("/api/admin/users/" + user["id"], json={"status": "active", "operator_id": "dispatcher-3"}, headers=headers)
        self.assertEqual(approved.status_code, 200)
        with self.new_client() as other:
            login = other.post("/api/auth/login", json={"username": "NEW.USER", "password": "New-password-2026"})
            self.assertEqual(login.status_code, 200)
            self.assertEqual(login.json()["user"]["operator_id"], "dispatcher-3")

    def test_duplicate_username_and_registration_cannot_request_admin(self):
        response = self.client.post("/api/auth/register", json={"username": "ADMIN", "name": "Новый", "password": "New-password-2026"})
        self.assertEqual(response.status_code, 409)
        response = self.client.post("/api/auth/register", json={"username": "privileged", "name": "Новый", "password": "New-password-2026", "role": "admin"})
        self.assertEqual(response.status_code, 422)

    def test_session_identity_overrides_header_and_url(self):
        headers, _ = self.login()
        response = self.client.post("/api/operator-presence?operator=dispatcher-3", headers={**headers, "X-Demo-Operator": "dispatcher-3"},
                                    json={"session_id": "identity-check", "availability": "ready"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["operator_id"], "dispatcher-1")
        profiles = self.client.get("/api/operator-profiles").json()
        self.assertTrue(next(p for p in profiles if p["operator_id"] == "dispatcher-1")["operator_ready"])
        self.assertFalse(next(p for p in profiles if p["operator_id"] == "dispatcher-3")["operator_ready"])

    def test_dispatcher_cannot_list_or_modify_admin_accounts(self):
        headers, _ = self.login()
        for endpoint in ("/api/admin/users", "/api/admin/audit"):
            self.assertEqual(self.client.get(endpoint).status_code, 403)
        response = self.client.patch("/api/admin/users/" + self.admin["id"], json={"status": "blocked"}, headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_csrf_and_cross_origin_requests_rejected(self):
        headers, _ = self.login()
        body = {"session_id": "csrf-check", "availability": "ready"}
        self.assertEqual(self.client.post("/api/operator-presence", json=body).status_code, 403)
        self.assertEqual(self.client.post("/api/operator-presence", json=body, headers={"X-CSRF-Token": "bad"}).status_code, 403)
        self.assertEqual(self.client.post("/api/operator-presence", json=body, headers={**headers, "Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/operator-presence", json=body, headers={**headers, "Origin": "http://127.0.0.1:8000"}).status_code, 200)
        with self.new_client() as other:
            response = other.post("/api/auth/login", json={"username": "admin", "password": "Admin-password-2026"}, headers={"Origin": "https://evil.example"})
            self.assertEqual(response.status_code, 403)

    def test_failed_login_and_signup_limits_are_enforced(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/api/auth/login", json={"username": "dispatcher.one", "password": "incorrect-password"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "dispatcher.one", "password": "Dispatcher-password-2026"}).status_code, 429)
        for _ in range(5):
            self.assertEqual(self.client.post("/api/auth/register", json={"username": "admin", "name": "Новое имя", "password": "New-password-2026"}).status_code, 409)
        self.assertEqual(self.client.post("/api/auth/register", json={"username": "next", "name": "Новое имя", "password": "New-password-2026"}).status_code, 429)

    def test_logout_revokes_token_and_blocking_revokes_other_clients(self):
        headers, _ = self.login()
        token = self.client.cookies.get(COOKIE_NAME)
        self.assertEqual(self.client.post("/api/auth/logout", headers=headers).status_code, 200)
        self.client.cookies.set(COOKIE_NAME, token)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.client.cookies.clear()
        self.login()
        with self.new_client() as admin_client:
            admin_headers, _ = self.login(admin_client, admin=True)
            response = admin_client.patch("/api/admin/users/" + self.dispatcher["id"], json={"status": "blocked"}, headers=admin_headers)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_expired_session_and_profile_change_require_new_login(self):
        self.login()
        with self.auth.store.transaction() as db:
            db.execute("UPDATE auth_sessions SET expires_at=0")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.client.cookies.clear()
        self.login()
        with self.new_client() as admin_client:
            headers, _ = self.login(admin_client, admin=True)
            self.assertEqual(admin_client.patch("/api/admin/users/" + self.dispatcher["id"], json={"operator_id": "dispatcher-2"}, headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/site").status_code, 401)
        self.client.cookies.clear()
        _, response = self.login()
        self.assertEqual(response.json()["user"]["operator_id"], "dispatcher-2")

    def test_source_key_separate_from_browser_session_and_internal_demo_works(self):
        self.assertEqual(self.client.post("/api/events", json=self.event()).status_code, 503)
        key = "test-source-key-" + "a" * 32
        event = self.event()
        with patch.dict(os.environ, {"DISPATCH_SOURCE_KEY": key}):
            self.assertEqual(self.client.post("/api/events", json=event, headers={"X-Source-Key": "wrong"}).status_code, 401)
            self.assertEqual(self.client.post("/api/events", json=event, headers={"X-Source-Key": key}).status_code, 201)
            self.assertEqual(self.client.post("/api/events", json=event, headers={"X-Source-Key": key}).status_code, 200)
        headers, _ = self.login()
        self.assertEqual(self.client.post("/api/demo/start", json={"scenario": "normal"}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/demo/stop", json={}, headers=headers).status_code, 200)

    def test_remote_plaintext_rejected_and_https_sets_secure_cookie(self):
        with self.new_client(remote=True) as remote:
            self.assertEqual(remote.get("/api/auth/status").status_code, 200)
            response = remote.post("/api/auth/login", json={"username": "admin", "password": "Admin-password-2026"},
                                   headers={"X-Forwarded-Proto": "https", "X-Forwarded-For": "127.0.0.1"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "encrypted_transport_required")
        with self.new_client(secure=True, remote=True) as encrypted:
            _, response = self.login(encrypted)
            self.assertIn("; Secure", response.headers["set-cookie"])
            self.assertEqual(encrypted.get("/api/site").status_code, 200)
            self.assertIn("max-age=", response.headers["strict-transport-security"])

    def test_audit_and_admin_responses_contain_no_passwords_or_session_secrets(self):
        headers, login = self.login(admin=True)
        response = self.client.get("/api/admin/users")
        self.assertEqual(response.status_code, 200)
        audit = self.client.get("/api/admin/audit")
        self.assertEqual(audit.status_code, 200)
        for text in (response.text, audit.text):
            for secret in ("password_hash", "Admin-password-2026", self.client.cookies.get(COOKIE_NAME), login.json()["csrf_token"]):
                self.assertNotIn(secret, text)
        actions = {item["action"] for item in audit.json()["items"]}
        self.assertIn("login_success", actions)
        self.assertIn("account_created", actions)
        self.assertEqual(self.client.patch("/api/admin/users/" + self.admin["id"], json={"status": "blocked"}, headers=headers).status_code, 409)

    def test_security_headers_and_explicit_test_mode(self):
        response = self.client.get("/")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        app = create_app(db_path=Path(self.temp.name) / "legacy.db", enable_scheduler=False, enable_auth=False)
        with TestClient(app) as legacy:
            self.assertEqual(legacy.get("/api/site").status_code, 200)
            self.assertFalse(legacy.get("/api/health").json()["auth"]["enabled"])


if __name__ == "__main__":
    unittest.main()
