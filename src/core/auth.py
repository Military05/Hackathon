"""Small local account/session service; no default credentials or external services."""
from collections import OrderedDict
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from fastapi import Body, Request
from fastapi.responses import JSONResponse

from src.core.service import ApiError, stamp
from src.storage.sqlite_store import Store

COOKIE_NAME = "dispatch_session"
PASSWORD_ITERATIONS = 600_000
SESSION_SECONDS = 8 * 60 * 60
OPERATORS = ("dispatcher-1", "dispatcher-2", "dispatcher-3")


def password_hash(password):
    if not isinstance(password, str) or not 15 <= len(password) <= 128:
        raise ApiError(422, "invalid_password", "Пароль должен содержать от 15 до 128 символов")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def password_matches(password, encoded):
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or not 100_000 <= int(iterations) <= 1_000_000:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def normalized_username(username):
    if not isinstance(username, str):
        raise ApiError(422, "invalid_username", "Укажите логин")
    normalized = username.strip().casefold()
    if not re.fullmatch(r"[a-z0-9._-]{3,40}", normalized):
        raise ApiError(422, "invalid_username", "Логин: 3–40 латинских букв, цифр, точек, дефисов или подчёркиваний")
    return normalized


def public_user(row):
    return {key: row[key] for key in ("id", "username", "name", "role", "status", "operator_id", "created_at")}


class AttemptLimiter:
    """Bounded single-worker demo limit; counters expire after one minute."""
    def __init__(self, clock=time.time, maximum=5, window=60):
        self.clock, self.maximum, self.window = clock, maximum, window
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def check(self, keys, record=False):
        with self.lock:
            now = self.clock()
            for key in list(self.entries):
                if now - self.entries[key][0] >= self.window:
                    del self.entries[key]
            for key in keys:
                entry = self.entries.get(key)
                if entry and entry[1] >= self.maximum:
                    raise ApiError(429, "auth_rate_limited", "Слишком много попыток. Повторите через минуту")
            if record:
                for key in keys:
                    started, count = self.entries.get(key, (now, 0))
                    self.entries[key] = (started, count + 1)
                    self.entries.move_to_end(key)
                while len(self.entries) > 2048:
                    self.entries.popitem(last=False)

    def clear(self, keys):
        with self.lock:
            for key in keys:
                self.entries.pop(key, None)


class AuthManager:
    def __init__(self, store_or_path, clock=time.time, session_seconds=SESSION_SECONDS):
        self.store = store_or_path if isinstance(store_or_path, Store) else Store(store_or_path)
        self.clock, self.session_seconds = clock, session_seconds
        self.login_limiter = AttemptLimiter(clock)
        self.signup_limiter = AttemptLimiter(clock)
        # An unknown account still performs the same expensive password derivation.
        self.dummy_hash = password_hash(secrets.token_urlsafe(24))
        with self.store.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS auth_users (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL, password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('dispatcher','admin')),
                    operator_id TEXT, status TEXT NOT NULL CHECK(status IN ('pending','active','blocked')),
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    csrf_token TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS auth_session_user ON auth_sessions(user_id);
                CREATE TABLE IF NOT EXISTS auth_audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                    action TEXT NOT NULL, actor_id TEXT, target_id TEXT, details TEXT NOT NULL);
            """)

    def _audit(self, db, action, actor_id=None, target_id=None, details=None):
        db.execute("INSERT INTO auth_audit(created_at,action,actor_id,target_id,details) VALUES (?,?,?,?,?)",
                   (stamp(), action, actor_id, target_id, json.dumps(details or {}, ensure_ascii=False)))

    def configured(self):
        with self.store.read() as db:
            return bool(db.execute("SELECT 1 FROM auth_users WHERE role='admin' AND status='active' LIMIT 1").fetchone())

    def create_user(self, username, name, password, role="dispatcher", operator_id=None, status="pending"):
        username = normalized_username(username)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ApiError(422, "invalid_name", "Имя должно содержать от 1 до 80 символов")
        if role not in ("dispatcher", "admin") or status not in ("pending", "active", "blocked"):
            raise ApiError(422, "invalid_account", "Некорректная роль или состояние")
        if role == "admin" and operator_id is None:
            operator_id = "dispatcher-3"
        if operator_id is not None and operator_id not in OPERATORS:
            raise ApiError(422, "invalid_operator", "Укажите один из трёх диспетчерских профилей")
        if status == "active" and operator_id is None:
            raise ApiError(422, "operator_required", "Активному аккаунту нужен диспетчерский профиль")
        encoded = password_hash(password)
        user_id = "user-" + secrets.token_hex(16)
        with self.store.transaction() as db:
            try:
                db.execute("INSERT INTO auth_users VALUES (?,?,?,?,?,?,?,?)",
                           (user_id, username, name.strip(), encoded, role, operator_id, status, stamp()))
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "username_exists", "Такой логин уже зарегистрирован") from exc
            self._audit(db, "account_created", target_id=user_id, details={"role": role, "status": status})
            return public_user(db.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone())

    def register(self, body, client_ip):
        self.signup_limiter.check(("ip:" + client_ip,), record=True)
        if not isinstance(body, dict) or set(body) != {"username", "name", "password"}:
            raise ApiError(422, "invalid_registration", "Нужны только username, name, password")
        return self.create_user(**body)

    def login(self, body, client_ip):
        if not isinstance(body, dict) or set(body) != {"username", "password"}:
            raise ApiError(422, "invalid_login", "Нужны username и password")
        username = normalized_username(body["username"])
        keys = ("ip:" + client_ip, "user:" + username)
        # Reserve an attempt before hashing: concurrent requests cannot bypass the limit.
        self.login_limiter.check(keys, record=True)
        with self.store.read() as db:
            row = db.execute("SELECT * FROM auth_users WHERE username=?", (username,)).fetchone()
        correct = password_matches(body["password"], row["password_hash"] if row else self.dummy_hash)
        if not row or not correct:
            with self.store.transaction() as db:
                self._audit(db, "login_failed", target_id=row["id"] if row else None, details={"username": username})
            raise ApiError(401, "invalid_credentials", "Неверный логин или пароль")
        if row["status"] != "active":
            with self.store.transaction() as db:
                self._audit(db, "login_rejected", target_id=row["id"], details={"status": row["status"]})
            raise ApiError(403, "account_" + row["status"], "Аккаунт ожидает подтверждения или заблокирован")
        self.login_limiter.clear(keys)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expiry = self.clock() + self.session_seconds
        with self.store.transaction() as db:
            # Re-check approval atomically with session issuance.
            current = db.execute("SELECT * FROM auth_users WHERE id=?", (row["id"],)).fetchone()
            if current["status"] != "active":
                raise ApiError(403, "account_blocked", "Аккаунт недоступен")
            db.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (self.clock(),))
            # Keep only a small bounded set of devices per account.
            excess = db.execute("SELECT token_hash FROM auth_sessions WHERE user_id=? ORDER BY expires_at DESC LIMIT -1 OFFSET 9",
                                (row["id"],)).fetchall()
            for old in excess:
                db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (old[0],))
            db.execute("INSERT INTO auth_sessions VALUES (?,?,?,?)", (self.token_hash(token), row["id"], csrf, expiry))
            self._audit(db, "login_success", actor_id=row["id"])
        return {"user": public_user(current), "csrf_token": csrf, "expires_at": expiry}, token

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def session(self, token):
        if not isinstance(token, str) or not 30 <= len(token) <= 100:
            raise ApiError(401, "authentication_required", "Войдите в аккаунт")
        token_hash = self.token_hash(token)
        with self.store.read() as db:
            row = db.execute("SELECT u.*,s.csrf_token,s.expires_at FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id WHERE s.token_hash=?",
                             (token_hash,)).fetchone()
        if not row or row["status"] != "active" or row["expires_at"] <= self.clock():
            raise ApiError(401, "session_expired", "Сессия истекла или аккаунт недоступен. Войдите снова")
        return {"user": public_user(row), "csrf_token": row["csrf_token"], "expires_at": row["expires_at"]}

    def logout(self, token, user_id):
        with self.store.transaction() as db:
            db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (self.token_hash(token),))
            self._audit(db, "logout", actor_id=user_id)

    def users(self):
        with self.store.read() as db:
            return [public_user(row) for row in db.execute("SELECT * FROM auth_users ORDER BY created_at,id")]

    def update_user(self, user_id, body, actor):
        if not isinstance(body, dict) or not body or set(body) - {"status", "operator_id"}:
            raise ApiError(422, "invalid_account_update", "Допустимы status и operator_id")
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise ApiError(404, "unknown_user", "Аккаунт не найден")
            status, operator = body.get("status", row["status"]), body.get("operator_id", row["operator_id"])
            if status not in ("pending", "active", "blocked") or (operator is not None and operator not in OPERATORS):
                raise ApiError(422, "invalid_account_update", "Некорректный статус или профиль")
            if status == "active" and operator is None:
                raise ApiError(422, "operator_required", "При подтверждении назначьте диспетчерский профиль")
            if row["role"] == "admin" and status != "active":
                count = db.execute("SELECT COUNT(*) FROM auth_users WHERE role='admin' AND status='active'").fetchone()[0]
                if row["status"] == "active" and count <= 1:
                    raise ApiError(409, "last_admin", "Нельзя заблокировать последнего администратора")
            db.execute("UPDATE auth_users SET status=?,operator_id=? WHERE id=?", (status, operator, user_id))
            if status != row["status"] or operator != row["operator_id"]:
                db.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
            self._audit(db, "account_updated", actor_id=actor["id"], target_id=user_id,
                        details={"status": status, "operator_id": operator})
            return public_user(db.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone())

    def audit(self, limit=100):
        with self.store.read() as db:
            result = []
            for row in db.execute("SELECT * FROM auth_audit ORDER BY seq DESC LIMIT ?", (limit,)):
                item = dict(row)
                item["details"] = json.loads(item["details"])
                result.append(item)
            return result


def secure_transport(request):
    # Forwarded headers are deliberately not trusted here.
    return (request.url.scheme == "https" or
            (request.client and request.client.host in ("127.0.0.1", "::1")) or
            os.environ.get("DISPATCH_TRUST_ENCRYPTED_TUNNEL") == "1")


def check_origin(request):
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise ApiError(403, "origin_rejected", "Запрос с другого сайта запрещён")
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            expected = urlsplit(str(request.url))
        except ValueError as exc:
            raise ApiError(403, "origin_rejected", "Некорректный источник запроса") from exc
        if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc) or parsed.path not in ("", "/"):
            raise ApiError(403, "origin_rejected", "Запрос с другого сайта запрещён")


def add_auth(app, service, enabled=True):
    manager = AuthManager(service.store) if enabled else None
    app.state.auth = manager
    app.state.auth_enabled = enabled
    public_paths = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/register"}
    source_paths = {"/api/events", "/api/model-observations"}

    @app.middleware("http")
    async def security(request: Request, call_next):
        import asyncio
        try:
            path = request.url.path.rstrip("/") or "/"
            if enabled and path.startswith("/api/") and path not in ("/api/health", "/api/auth/status"):
                if not secure_transport(request):
                    raise ApiError(403, "encrypted_transport_required", "Удалённый доступ требует HTTPS или настроенного защищённого туннеля")
                if path in source_paths and request.method == "POST":
                    expected = os.environ.get("DISPATCH_SOURCE_KEY", "")
                    provided = request.headers.get("x-source-key", "")
                    if len(expected) < 24:
                        raise ApiError(503, "source_key_unconfigured", "Ключ внешнего источника не настроен")
                    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
                        raise ApiError(401, "source_authentication_required", "Нужен ключ источника")
                    check_origin(request)
                elif path in public_paths:
                    if request.method not in ("GET", "HEAD", "OPTIONS"):
                        check_origin(request)
                else:
                    session = await asyncio.to_thread(manager.session, request.cookies.get(COOKIE_NAME))
                    request.state.auth_session = session
                    request.state.user = session["user"]
                    if path.startswith("/api/admin/") and session["user"]["role"] != "admin":
                        raise ApiError(403, "admin_required", "Операция доступна администратору")
                    if request.method not in ("GET", "HEAD", "OPTIONS"):
                        check_origin(request)
                        csrf = request.headers.get("x-csrf-token", "")
                        if not hmac.compare_digest(csrf.encode("utf-8"), session["csrf_token"].encode("utf-8")):
                            raise ApiError(403, "csrf_required", "Необходим CSRF-токен текущей сессии")
                    headers = [(key, value) for key, value in request.scope["headers"] if key.lower() != b"x-demo-operator"]
                    headers.append((b"x-demo-operator", session["user"]["operator_id"].encode("ascii")))
                    request.scope["headers"] = headers
                    # Request.headers may already be cached from the security checks.
                    if hasattr(request, "_headers"):
                        del request._headers
            response = await call_next(request)
        except ApiError as exc:
            response = JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message, "details": exc.details})
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=86400"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/auth/status")
    def auth_status():
        return {"enabled": enabled, "configured": manager.configured() if manager else False}

    @app.post("/api/auth/register")
    def register(request: Request, body: dict = Body(...)):
        if not manager:
            raise ApiError(503, "auth_disabled", "Авторизация отключена в тестовом режиме")
        user = manager.register(body, request.client.host if request.client else "unknown")
        return JSONResponse(status_code=201, content={"user": user, "status": "pending"})

    @app.post("/api/auth/login")
    def login(request: Request, body: dict = Body(...)):
        if not manager:
            raise ApiError(503, "auth_disabled", "Авторизация отключена в тестовом режиме")
        payload, token = manager.login(body, request.client.host if request.client else "unknown")
        response = JSONResponse(payload)
        response.set_cookie(COOKIE_NAME, token, max_age=manager.session_seconds, httponly=True,
                            secure=request.url.scheme == "https", samesite="strict", path="/")
        return response

    @app.get("/api/auth/me")
    def me(request: Request):
        if not manager:
            raise ApiError(503, "auth_disabled", "Авторизация отключена в тестовом режиме")
        return request.state.auth_session

    @app.post("/api/auth/logout")
    def logout(request: Request):
        if not manager:
            raise ApiError(503, "auth_disabled", "Авторизация отключена в тестовом режиме")
        manager.logout(request.cookies[COOKIE_NAME], request.state.user["id"])
        response = JSONResponse({"status": "logged_out"})
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict", secure=request.url.scheme == "https")
        return response

    def admin(request):
        if not manager or getattr(request.state, "user", {}).get("role") != "admin":
            raise ApiError(403, "admin_required", "Операция доступна администратору")

    @app.get("/api/admin/users")
    def users(request: Request):
        admin(request)
        return {"users": manager.users()}

    @app.patch("/api/admin/users/{user_id}")
    def update_user(user_id: str, request: Request, body: dict = Body(...)):
        admin(request)
        return {"user": manager.update_user(user_id, body, request.state.user)}

    @app.get("/api/admin/audit")
    def audit(request: Request):
        admin(request)
        return {"items": manager.audit()}

    return manager
