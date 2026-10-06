"""Run demo API scenarios and write an honest, standalone QA report."""

from __future__ import annotations

import argparse
import copy
import html
import importlib
import json
import math
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("Q07", "Разрешённый проход", "Нет происшествия unauthorized_access"),
    ("Q06", "Проход без допуска", "Один случай с сотрудником, зданием и исходным событием"),
    ("Q03", "Запрещённый въезд", "Один активный случай, без дублей при движении внутри зоны"),
    ("Q08", "Потеря связи", "Один случай после порога; восстановление сохраняет историю"),
    ("Q11", "Повтор события", "duplicate=true; нового происшествия нет"),
    ("Q27", "Qwen выключена", "Агент недоступен; приём событий и позиции продолжают работать"),
]


class Blocked(Exception):
    """A prerequisite is absent; this must never be reported as PASS."""


class Failed(Exception):
    """The tested API returned incorrect or malformed behavior."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url, timeout=3.0):
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("base-url должен быть HTTP(S)-адресом")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("base-url не должен содержать пароли, query или fragment")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.trace = []
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, payload=None, expected=(200,)):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(self.base_url + path, body, {"Content-Type": "application/json"}, method=method)
        started = time.monotonic()
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError) as error:
            self.trace.append({"method": method, "path": path, "status": None})
            raise Blocked(f"API недоступен: {type(error).__name__}") from error
        with response:
            status = response.code
            raw = response.read(2_000_001)
        self.trace.append({"method": method, "path": path, "status": status,
                           "duration_ms": round((time.monotonic() - started) * 1000, 1)})
        if status not in expected:
            raise Failed(f"{method} {path}: HTTP {status}, ожидалось {expected}")
        if len(raw) > 2_000_000:
            raise Failed("Ответ API превышает лимит QA-клиента")
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise Failed(f"{method} {path}: ответ не JSON") from error


def require(condition, message):
    if not condition:
        raise Failed(message)


def records(value, key):
    total = None
    if isinstance(value, dict):
        if value.get("has_more") or value.get("next_cursor") or value.get("next"):
            raise Blocked("Ответ содержит не все записи; для QA нужна полная выборка")
        total = value.get("total")
        value = value.get(key, value.get("items"))
    require(isinstance(value, list) and all(isinstance(row, dict) for row in value),
            f"Ожидался список {key} или объект с полем {key}/items")
    if isinstance(total, int) and total > len(value):
        raise Blocked("Выборка усечена по total; нужна полная выборка QA-стенда")
    return value


def state(value):
    return value.get("status") if isinstance(value, dict) else value


def timestamp(value):
    require(isinstance(value, str), "Нет timestamp в ответе")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise Failed("Неверный timestamp") from error
    require(result.tzinfo is not None, "Timestamp без часового пояса")
    return result


def load_config(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("QA-конфигурация должна быть объектом JSON")
    for key in ("allowed_access", "denied_access", "forbidden_zone", "heartbeat"):
        if not isinstance(value.get(key), dict):
            raise ValueError(f"{key} должен быть объектом JSON")
    for key in ("allowed_access", "denied_access"):
        for field in ("sensor_id", "employee_id", "building_id"):
            if not isinstance(value.get(key, {}).get(field), str) or not value[key][field]:
                raise ValueError(f"В конфигурации отсутствует {key}.{field}")
    zone = value.get("forbidden_zone", {})
    for field in ("sensor_id", "asset_id", "zone_id"):
        if not isinstance(zone.get(field), str) or not zone[field]:
            raise ValueError(f"В конфигурации отсутствует forbidden_zone.{field}")
    for point in ("outside", "inside", "inside_again"):
        if not isinstance(zone.get(point), dict):
            raise ValueError(f"forbidden_zone.{point} должен быть объектом JSON")
        for axis in ("x", "y"):
            number = zone.get(point, {}).get(axis)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not 0 <= number <= 100:
                raise ValueError(f"Неверная координата forbidden_zone.{point}.{axis}")
    heartbeat = value.get("heartbeat", {})
    threshold = heartbeat.get("threshold_seconds")
    if not isinstance(heartbeat.get("sensor_id"), str) or not heartbeat["sensor_id"]:
        raise ValueError("Отсутствует heartbeat.sensor_id")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 1 <= threshold <= 30:
        raise ValueError("heartbeat.threshold_seconds должен быть от 1 до 30")
    return value


def report_url(value):
    try:
        parts = urlsplit(value)
        if parts.username or parts.password or parts.query or parts.fragment:
            return "[некорректный адрес скрыт]"
    except ValueError:
        return "[некорректный адрес скрыт]"
    return value


@dataclass
class Result:
    case_id: str
    name: str
    expected: str
    status: str
    actual: str
    duration_seconds: float
    event_ids: list
    incident_ids: list
    requests: list


class Suite:
    def __init__(self, client, config, run_id, wait=2.0, observe=0.6, poll=0.1, event_factory=None):
        self.client, self.config, self.run_id = client, config, run_id
        self.wait, self.observe, self.poll = wait, observe, poll
        self.event_factory = event_factory
        self.counter = 0
        self.event_ids, self.incident_ids = [], []

    def until(self, predicate, timeout=None):
        deadline = time.monotonic() + (self.wait if timeout is None else timeout)
        while True:
            found = predicate()
            if found:
                return found
            if time.monotonic() >= deadline:
                raise Failed("Ожидаемый результат не появился до истечения времени")
            time.sleep(min(self.poll, max(0, deadline - time.monotonic())))

    def observe_for(self, predicate, duration=None):
        deadline = time.monotonic() + (self.observe if duration is None else max(0, duration))
        while True:
            predicate()
            if time.monotonic() >= deadline:
                return
            time.sleep(min(self.poll, max(0, deadline - time.monotonic())))

    def incidents(self, kind, **fields):
        rows = records(self.client.request("GET", "/api/incidents"), "incidents")
        for row in rows:
            require(isinstance(row.get("incident_id"), str), "Происшествие без incident_id")
        return [row for row in rows if row.get("type") == kind and all(row.get(k) == v for k, v in fields.items())]

    def ids(self, kind, **fields):
        return {row["incident_id"] for row in self.incidents(kind, **fields)}

    def event(self, kind, binding, payload):
        self.counter += 1
        event = {"event_id": f"qa-{self.run_id}-{self.counter}",
                 "event_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                 "sensor_id": binding["sensor_id"], "type": kind, "demo": True, "payload": payload}
        if self.event_factory:
            built = self.event_factory(**copy.deepcopy(event))
            require(built == event, "Генератор симулятора изменил согласованное QA-событие")
            event = built
        self.event_ids.append(event["event_id"])
        response = self.client.request("POST", "/api/events", event, (201,))
        require(isinstance(response, dict) and response.get("event_id") == event["event_id"], "POST вернул другой event_id")
        require(response.get("duplicate") is False, "Новое событие помечено duplicate")
        timestamp(response.get("received_at"))
        return event, response

    def access(self, binding):
        return self.event("access", binding, {"employee_id": binding["employee_id"],
                                              "building_id": binding["building_id"], "direction": "in"})

    def position(self, point):
        binding = self.config["forbidden_zone"]
        return self.event("position", binding, {"asset_id": binding["asset_id"], **binding[point]})

    def active(self, kind, **fields):
        return [row for row in self.incidents(kind, **fields) if row.get("condition_active") is True]

    def fresh_one(self, kind, before, event_id, **fields):
        rows = self.until(lambda: [r for r in self.incidents(kind, **fields) if r["incident_id"] not in before])
        require(len(rows) == 1, "Создано несколько происшествий вместо одного")
        row = rows[0]
        require(event_id in row.get("evidence_event_ids", []), "Нет исходного события в evidence")
        self.incident_ids.append(row["incident_id"])
        return row

    def no_new(self, kind, before, **fields):
        require(self.ids(kind, **fields) == before, "Появилось лишнее происшествие")

    def allowed_access(self):
        binding = self.config["allowed_access"]
        fields = {k: binding[k] for k in ("employee_id", "building_id")}
        before = self.ids("unauthorized_access", **fields)
        self.access(binding)
        self.observe_for(lambda: self.no_new("unauthorized_access", before, **fields))
        return "Разрешённый проход принят; новых тревог за окно наблюдения нет"

    def denied_access(self):
        binding = self.config["denied_access"]
        fields = {k: binding[k] for k in ("employee_id", "building_id")}
        before = self.ids("unauthorized_access", **fields)
        event, _ = self.access(binding)
        row = self.fresh_one("unauthorized_access", before, event["event_id"], **fields)
        self.observe_for(lambda: require(self.ids("unauthorized_access", **fields) == before | {row["incident_id"]}, "Повторная тревога"))
        return f"Создано {row['incident_id']}; сотрудник, здание и evidence совпадают"

    def forbidden_zone(self):
        binding = self.config["forbidden_zone"]
        fields = {k: binding[k] for k in ("asset_id", "zone_id")}
        if self.active("forbidden_zone", **fields):
            raise Blocked("Для машины уже активно нарушение; нужен отдельный чистый QA-стенд")
        before = self.ids("forbidden_zone", **fields)
        self.position("outside")
        self.observe_for(lambda: self.no_new("forbidden_zone", before, **fields))
        entered = False
        try:
            entered = True
            event, _ = self.position("inside")
            row = self.fresh_one("forbidden_zone", before, event["event_id"], **fields)
            require(row.get("condition_active") is True, "Нарушение не активно")
            self.position("inside_again")
            self.observe_for(lambda: require(self.ids("forbidden_zone", **fields) == before | {row["incident_id"]}, "Движение внутри зоны дублирует происшествие"))
        finally:
            if entered:
                self.position("outside")
                self.until(lambda: not self.active("forbidden_zone", **fields))
        require(row["incident_id"] in self.ids("forbidden_zone", **fields), "После выхода пропала история")
        return f"Одно {row['incident_id']}; повторного случая нет, выход восстановил условие"

    def sensor_offline(self):
        binding = self.config["heartbeat"]
        fields = {"sensor_id": binding["sensor_id"]}
        threshold = binding["threshold_seconds"]
        started = time.monotonic()
        _, receipt = self.event("heartbeat", binding, {})
        self.until(lambda: not self.active("sensor_offline", **fields))
        before = self.ids("sensor_offline", **fields)
        try:
            self.observe_for(lambda: self.no_new("sensor_offline", before, **fields),
                             started + threshold - 0.15 - time.monotonic())
            rows = self.until(lambda: [r for r in self.incidents("sensor_offline", **fields) if r["incident_id"] not in before], threshold + self.wait)
            require(len(rows) == 1, "Потеря связи создаёт несколько случаев")
            row = rows[0]
            self.incident_ids.append(row["incident_id"])
            require(row.get("condition_active") is True, "Потеря связи не активна")
            details = row.get("details", {})
            reported = details.get("threshold_seconds", row.get("threshold_seconds"))
            require(reported == threshold, "Порог сервера отличается от QA-конфигурации")
            last = timestamp(details.get("last_received_at", row.get("last_received_at")))
            detected = timestamp(row.get("detected_at"))
            require(last == timestamp(receipt["received_at"]), "Указан другой последний heartbeat")
            require((detected - last).total_seconds() >= threshold, "Тревога создана до порога")
            self.observe_for(lambda: require(self.ids("sensor_offline", **fields) == before | {row["incident_id"]}, "Потеря связи дублирует происшествие"))
        finally:
            self.event("heartbeat", binding, {})
            self.until(lambda: not self.active("sensor_offline", **fields))
        require(row["incident_id"] in self.ids("sensor_offline", **fields), "При восстановлении пропала история")
        return f"{row['incident_id']} появился после {threshold} секунд; связь восстановлена"

    def duplicate_event(self):
        binding = self.config["denied_access"]
        fields = {k: binding[k] for k in ("employee_id", "building_id")}
        before = self.ids("unauthorized_access", **fields)
        event, _ = self.access(binding)
        row = self.fresh_one("unauthorized_access", before, event["event_id"], **fields)
        replay = self.client.request("POST", "/api/events", event, (200,))
        require(isinstance(replay, dict) and replay.get("duplicate") is True, "Повтор не вернул duplicate=true")
        require(replay.get("event_id") == event["event_id"], "Повтор вернул другой event_id")
        self.observe_for(lambda: require(self.ids("unauthorized_access", **fields) == before | {row["incident_id"]}, "Повтор события создал новую тревогу"))
        return "Точный повтор принят как duplicate; число происшествий не изменилось"

    def qwen_off(self):
        health = self.client.request("GET", "/api/health")
        if state(health.get("agent")) != "unavailable":
            raise Blocked("Остановите Ollama только на QA-стенде: /api/health должен показать agent=unavailable")
        binding = self.config["denied_access"]
        fields = {k: binding[k] for k in ("employee_id", "building_id")}
        before = self.ids("unauthorized_access", **fields)
        event, _ = self.access(binding)
        row = self.fresh_one("unauthorized_access", before, event["event_id"], **fields)
        response = self.client.request("POST", f"/api/incidents/{row['incident_id']}/analysis", {}, (503,))
        require(isinstance(response, dict) and isinstance(response.get("code"), str), "Ошибка агента без code")
        self.access(self.config["allowed_access"])
        position, _ = self.position("outside")
        binding = self.config["forbidden_zone"]
        def updated():
            assets = records(self.client.request("GET", "/api/assets"), "assets")
            for asset in assets:
                if asset.get("asset_id", asset.get("id")) == binding["asset_id"]:
                    point = asset.get("position", asset)
                    return isinstance(point, dict) and all(point.get(k) == position["payload"][k] for k in ("x", "y"))
            return False
        self.until(updated)
        health = self.client.request("GET", "/api/health")
        require(state(health.get("rules")) == "ready", "Правила перестали работать без Qwen")
        require(state(health.get("agent")) == "unavailable", "Агент неожиданно доступен")
        self.client.request("GET", "/api/site")
        return "Анализ вернул 503; новые события приняты, позиции и API карты доступны"

    def preflight(self):
        health = self.client.request("GET", "/api/health")
        require(isinstance(health, dict), "/api/health должен вернуть объект")
        if state(health.get("rules")) != "ready":
            raise Blocked("Правила сервера ещё не готовы")
        site = self.client.request("GET", "/api/site")
        require(isinstance(site, dict), "/api/site должен вернуть объект")
        for kind, field in (("sensors", "sensor_id"), ("assets", "asset_id"), ("buildings", "building_id"), ("zones", "zone_id")):
            known = {row.get("id", row.get(field)) for row in records(site.get(kind), kind)}
            for binding in self.config.values():
                if isinstance(binding, dict) and field in binding and binding[field] not in known:
                    raise Blocked(f"Нет {field}={binding[field]} в /api/site; настройте QA bindings")
            if kind == "assets":
                for key in ("allowed_access", "denied_access"):
                    if self.config[key]["employee_id"] not in known:
                        raise Blocked(f"Сотрудник {self.config[key]['employee_id']} не зарегистрирован в assets")

    def run(self, selected):
        preflight_status, preflight_reason = None, None
        try:
            self.preflight()
        except (Blocked, Failed) as error:
            preflight_status = "BLOCKED" if isinstance(error, Blocked) else "FAIL"
            preflight_reason = str(error)
        except Exception as error:
            preflight_status = "FAIL"
            preflight_reason = f"Ошибка подготовки QA: {type(error).__name__}"
        functions = [self.allowed_access, self.denied_access, self.forbidden_zone,
                     self.sensor_offline, self.duplicate_event, self.qwen_off]
        results = []
        for (case_id, name, expected), function in zip(CASES, functions):
            started, trace_start = time.monotonic(), len(self.client.trace)
            self.event_ids, self.incident_ids = [], []
            if case_id not in selected:
                status, actual = "NOT RUN", "Не выбран для этого прогона"
            elif preflight_status:
                status, actual = preflight_status, preflight_reason
            else:
                try:
                    actual, status = function(), "PASS"
                except (Blocked, Failed) as error:
                    status = "BLOCKED" if isinstance(error, Blocked) else "FAIL"
                    actual = str(error)
                except Exception as error:
                    status, actual = "FAIL", f"Ошибка QA-инструмента: {type(error).__name__}"
            results.append(Result(case_id, name, expected, status, actual,
                                  round(time.monotonic() - started, 3), self.event_ids[:],
                                  self.incident_ids[:], self.client.trace[trace_start:]))
            print(f"{case_id}: {status} — {actual}")
        return results


def git_info(root):
    def get(*args):
        try:
            value = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, timeout=3)
            return value.stdout.strip() if value.returncode == 0 else "unknown"
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
    sha = get("rev-parse", "HEAD")
    branch = get("branch", "--show-current")
    dirty = get("status", "--porcelain")
    return {"runner_sha": sha, "runner_branch": branch or "detached", "runner_dirty": dirty != ""}


def write_report(path, metadata, results):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"metadata": metadata, "results": [asdict(row) for row in results]}
    path.with_suffix(".json").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    esc = lambda value: html.escape(str(value), quote=True)
    rows = []
    for row in results:
        identifiers = ", ".join(row.event_ids + row.incident_ids) or "—"
        trace = "\n".join(f"{r['method']} {r['path']}: {r['status']}" for r in row.requests)
        rows.append(f'<tr><td>{esc(row.case_id)} · {esc(row.name)}</td><td class="{row.status.lower()}">{esc(row.status)}</td><td>{esc(row.expected)}</td><td>{esc(row.actual)}<details><summary>События и HTTP-проверки</summary><p>{esc(identifiers)}</p><pre>{esc(trace)}</pre></details></td><td>{row.duration_seconds:.3f}</td></tr>')
    meta = "".join(f"<dt>{esc(key)}</dt><dd>{esc(value)}</dd>" for key, value in metadata.items())
    text = '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>QA — мониторинг предприятия</title><style>body{font:16px/1.5 system-ui;margin:24px;color:#172334;background:#f7f9fc}h1{font-size:24px}table{border-collapse:collapse;width:100%;background:white}th,td{padding:12px;border-bottom:1px solid #d4dce7;text-align:left;vertical-align:top}th{background:#e9edf4}.pass{color:#176537}.fail{color:#a62032}.blocked{color:#765200}td:nth-child(2){font-weight:bold}dl{display:grid;grid-template-columns:minmax(120px,200px) minmax(0,1fr);gap:8px}dd{margin:0;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere}.table{overflow-x:auto}details p{overflow-wrap:anywhere}</style><h1>QA — мониторинг предприятия</h1><p>PASS — проверено; FAIL — ошибка; BLOCKED — отсутствуют условия; NOT RUN — не запускалось. Приём API карты не доказывает правильность отображения в браузере.</p><dl>' + meta + '</dl><div class="table"><table><thead><tr><th>Сценарий</th><th>Результат</th><th>Ожидается</th><th>Фактически</th><th>Секунды</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div></html>'
    path.write_text(text, encoding="utf-8")


def factory_from(spec):
    if not spec:
        return None
    module, separator, name = spec.partition(":")
    if not separator or not module or not name:
        raise ValueError("event-factory: используйте module:function")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    function = getattr(importlib.import_module(module), name)
    if not callable(function):
        raise ValueError("Генератор симулятора должен быть функцией")
    return function


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--config", default=str(ROOT / "tests/fixtures/qa_config.example.json"))
    parser.add_argument("--report", default=str(ROOT / "runtime/qa/report.html"))
    parser.add_argument("--server-sha", default="unknown", help="SHA проверяемого сервера, отдельно от версии QA")
    parser.add_argument("--cases", default=",".join(row[0] for row in CASES))
    parser.add_argument("--allow-demo-writes", action="store_true", help="Подтвердить запись синтетических событий в отдельный demo-стенд")
    parser.add_argument("--event-factory", help="Необязательная функция симулятора module:function")
    parser.add_argument("--request-timeout", type=float, default=3)
    parser.add_argument("--wait", type=float, default=2)
    parser.add_argument("--observe", type=float, default=0.6)
    args = parser.parse_args(argv)
    if Path(args.report).suffix.lower() not in {".html", ".htm"}:
        parser.error("--report должен заканчиваться на .html или .htm")
    selected = set(args.cases.split(","))
    if not selected or not selected <= {row[0] for row in CASES}:
        parser.error("Неизвестные или пустые --cases")
    for option in ("request_timeout", "wait", "observe"):
        number = getattr(args, option)
        if not math.isfinite(number) or not 0 < number <= 30:
            parser.error(f"--{option.replace('_', '-')} должен быть >0 и <=30")
    started = datetime.now(timezone.utc).isoformat()
    run_id = uuid.uuid4().hex[:12]
    metadata = {"run_id": run_id, "started_at_utc": started, "base_url": report_url(args.base_url),
                "server_sha": args.server_sha, **git_info(ROOT), "python": sys.version.split()[0],
                "config": str(args.config), "source": args.event_factory or "QA contract fixtures",
                "observe_seconds": args.observe}
    client = None
    try:
        if not args.allow_demo_writes:
            raise Blocked("Не разрешена запись demo-событий: добавьте --allow-demo-writes на отдельном QA-стенде")
        config = load_config(args.config)
        client = Client(args.base_url, args.request_timeout)
        suite = Suite(client, config, run_id, args.wait, args.observe, event_factory=factory_from(args.event_factory))
        results = suite.run(selected)
    except (Blocked, ValueError, OSError, ImportError, AttributeError) as error:
        message = str(error) if isinstance(error, (Blocked, ValueError)) else f"Не готова конфигурация или генератор: {type(error).__name__}"
        results = [Result(case, name, expected, "BLOCKED" if case in selected else "NOT RUN", message, 0, [], [], []) for case, name, expected in CASES]
    metadata["preflight_requests"] = [] if client is None else client.trace[:2]
    metadata["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_report(args.report, metadata, results)
    print(f"Отчёт: {Path(args.report).resolve()}")
    if any(row.status == "FAIL" for row in results):
        return 1
    if any(row.status == "BLOCKED" for row in results):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
