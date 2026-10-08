"""Prepare/verify the authorized update; no application imports during backup."""
import argparse
import ctypes
import datetime
import hashlib
import json
import os
import runpy
import sys
from pathlib import Path
import sqlite3
import struct
import urllib.request
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATABASE = None
BACKUP_DIR = Path.home() / "ContourBackups"
REPORT = ROOT / "data/local/diagnostics/switch-contour-report.json"
ENVIRONMENT = ROOT / "data/local/diagnostics/switch-contour-environment.bin"

def crypt(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.c_void_p)]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    output = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
    if not function(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel = ctypes.WinDLL("kernel32")
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(output.data)

def process_settings(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.ReadProcessMemory.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        def read(address, length):
            buffer = ctypes.create_string_buffer(length)
            count = ctypes.c_size_t()
            if not kernel.ReadProcessMemory(handle, address, buffer, length, ctypes.byref(count)) or count.value != length:
                raise ctypes.WinError(ctypes.get_last_error())
            return buffer.raw
        info = (ctypes.c_void_p * 6)()
        returned = ctypes.c_ulong()
        nt = ctypes.WinDLL("ntdll").NtQueryInformationProcess
        nt.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        if nt(handle, 0, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned)) != 0:
            raise RuntimeError("Не удалось прочитать параметры процесса")
        params = struct.unpack("<Q", read(info[1] + 0x20, 8))[0]
        length, maximum, address = struct.unpack("<HH4xQ", read(params + 0x38, 16))
        cwd = read(address, length).decode("utf-16-le")
        address = struct.unpack("<Q", read(params + 0x80, 8))[0]
        raw = bytearray()
        for _ in range(2048):
            amount = min(512, 4096 - address % 4096)
            raw.extend(read(address, amount)); address += amount
            text = raw.decode("utf-16-le")
            if "\x00\x00" in text:
                entries = text.split("\x00\x00", 1)[0].split("\x00")
                environment = dict(item.split("=", 1) for item in entries if "=" in item and not item.startswith("="))
                break
        else:
            raise RuntimeError("Не удалось прочитать окружение процесса")
        configured = environment.get("DISPATCH_DB") or str(Path(cwd) / "data/runtime/dispatch.db")
        if Path(configured).resolve() != DATABASE.resolve():
            raise RuntimeError("Процесс использует другую базу; переключение отменено")
        runtime = {key: value for key, value in environment.items() if key.startswith(("DISPATCH_", "LOCAL_LLM_")) or key in ("DEMO_AUTOSTART", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")}
        ENVIRONMENT.parent.mkdir(parents=True, exist_ok=True)
        ENVIRONMENT.write_bytes(crypt(json.dumps(runtime).encode()))
        return {"cwd": cwd, "database": configured,
                "flags": {key: environment.get(key) for key in ("DISPATCH_ENABLE_AGENT", "DISPATCH_ENABLE_ML", "DISPATCH_ENABLE_AUTH")}}
    finally:
        kernel.CloseHandle(handle)

def inventory(db):
    accounts = db.execute("SELECT id,username,role,status,operator_id,password_hash FROM auth_users ORDER BY id").fetchall()
    digest = hashlib.sha256(json.dumps(accounts, ensure_ascii=False).encode()).hexdigest()
    return {"accounts_digest": digest, "accounts": len(accounts),
            "active_admins": db.execute("SELECT count(*) FROM auth_users WHERE role='admin' AND status='active'").fetchone()[0],
            "active_dispatchers": db.execute("SELECT count(*) FROM auth_users WHERE role='dispatcher' AND status='active'").fetchone()[0],
            "events": db.execute("SELECT count(*) FROM events").fetchone()[0],
            "incidents": db.execute("SELECT count(*) FROM incidents").fetchone()[0],
            "history": db.execute("SELECT count(*) FROM dispatch_history").fetchone()[0]}

def prepare(pid):
    if pid:
        settings = process_settings(pid)
    else:
        if not ENVIRONMENT.is_file():
            from src.agent.config import load_env_file
            if (ROOT / '.env').is_file():
                load_env_file(ROOT / '.env')
            runtime = {key:value for key,value in os.environ.items() if key.startswith(('DISPATCH_', 'LOCAL_LLM_'))}
            ENVIRONMENT.parent.mkdir(parents=True, exist_ok=True)
            ENVIRONMENT.write_bytes(crypt(json.dumps(runtime).encode()))
        runtime = json.loads(crypt(ENVIRONMENT.read_bytes(), decrypt=True))
        settings = {"cwd": str(ROOT), "database": str(DATABASE),
                    "flags": {key: runtime.get(key) for key in ("DISPATCH_ENABLE_AGENT", "DISPATCH_ENABLE_ML", "DISPATCH_ENABLE_AUTH")}}
    if not DATABASE.is_file():
        raise RuntimeError("Рабочая база не найдена")
    backup = BACKUP_DIR / ("contour-8000-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-before-update.db")
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        raise RuntimeError("Файл копии уже существует")
    with closing(sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True, timeout=10)) as source:
        source.execute("PRAGMA query_only=ON")
        has_jobs = source.execute("SELECT 1 FROM sqlite_master WHERE name='b1_agent_jobs'").fetchone()
        pending = source.execute("SELECT count(*) FROM b1_agent_jobs WHERE status IN ('queued','running')").fetchone()[0] if has_jobs else 0
        if pid and pending:
            raise RuntimeError("Есть незавершённые анализы ИИ; дождитесь завершения и повторите переключение")
        with closing(sqlite3.connect(backup)) as destination:
            source.backup(destination, pages=512, sleep=0.02)
            destination.commit()
            check = destination.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError("Резервная копия не прошла quick_check")
            before = inventory(destination)
            if before["active_admins"] != 1:
                raise RuntimeError("Ожидался один действующий администратор")
            sys.path.insert(0, str(ROOT))
            from src.core.auth import ADMIN_USERNAME, ADMIN_PASSWORD, password_matches
            row = destination.execute("SELECT username,role,status,operator_id,password_hash FROM auth_users WHERE role='admin'").fetchone()
            if row[:4] != (ADMIN_USERNAME, 'admin', 'active', None) or not password_matches(ADMIN_PASSWORD, row[4]):
                raise RuntimeError("Новая версия попыталась бы изменить администратора; переключение отменено")
    report = {"status": "prepared", "old_pid": pid, "process": settings,
              "database": str(DATABASE), "backup": str(backup), "backup_check": check,
              "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(), "before": before}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k != "before"}, ensure_ascii=False))

def verify():
    report = json.loads(REPORT.read_text(encoding="utf-8-sig"))
    with closing(sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
        db.execute("PRAGMA query_only=ON")
        after = inventory(db)
    before = report["before"]
    if after["accounts_digest"] != before["accounts_digest"]:
        raise RuntimeError("Состав аккаунтов изменился: требуется проверка")
    if any(after[key] < before[key] for key in ("events", "incidents", "history")):
        raise RuntimeError("Количество сохранённых записей уменьшилось")
    with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=10) as response:
        health = json.load(response)
    if health["agent"].get("status") != "ready" or not health["agent"].get("worker_running") or health["ml"].get("status") != "ready" or not health["auth"].get("enabled"):
        raise RuntimeError("AI/ML или авторизация не готовы")
    with urllib.request.urlopen("http://127.0.0.1:8000/app.js", timeout=10) as response:
        actual = response.read().replace(b"\r\n", b"\n")
    expected = (ROOT / "src/interface/web/app.js").read_bytes().replace(b"\r\n", b"\n")
    if actual != expected or b"incidentInWorkspace" not in actual:
        raise RuntimeError("Сервер не отдаёт новый интерфейс")
    report.update(status="verified", after=after, health=health)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Новая версия готова; аккаунты и сохранённые записи проверены.")

def run(root):
    root = Path(root).resolve()
    allowed = (ROOT).resolve()
    previous = Path(json.loads(REPORT.read_text(encoding='utf-8-sig'))['process']['cwd']).resolve()
    if root not in (allowed, previous):
        raise RuntimeError("Неожиданная рабочая директория")
    runtime = json.loads(crypt(ENVIRONMENT.read_bytes(), decrypt=True))
    for key in list(os.environ):
        if key.startswith(("DISPATCH_", "LOCAL_LLM_")):
            del os.environ[key]
    os.environ.update(runtime)
    os.environ['DISPATCH_DB'] = str(DATABASE)
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.argv = ['src.core.launch', '--with-ai', '--db', str(DATABASE), '--env-file', str(ROOT / '.env'), '--host', '127.0.0.1', '--port', '8000']
    runpy.run_module('src.core.launch', run_name='__main__')

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "verify", "run", "check"))
    parser.add_argument("--pid", type=int)
    parser.add_argument("--root")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, default=BACKUP_DIR)
    args = parser.parse_args()
    DATABASE = args.database
    BACKUP_DIR = args.backup_dir
    if not DATABASE.is_absolute() or not DATABASE.is_file():
        parser.error('Нужен абсолютный путь к существующей базе; пустая база не создаётся')
    if args.action == 'prepare':
        prepare(args.pid)
    elif args.action == 'verify':
        verify()
    elif args.action == 'check':
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/app.js', timeout=3) as response:
                actual = response.read().replace(b'\r\n', b'\n')
            with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3) as response:
                health = json.load(response)
            expected = (ROOT / 'src/interface/web/app.js').read_bytes().replace(b'\r\n', b'\n')
            ready = actual == expected and health['auth']['enabled'] and health['ml']['status'] == 'ready' and health['agent']['status'] == 'ready' and health['agent']['worker_running']
            raise SystemExit(0 if ready else 1)
        except (OSError, KeyError, ValueError):
            raise SystemExit(1)
    else:
        run(args.root)
