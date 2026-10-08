"""Единый запуск приложения с общей базой и авторизацией."""
import argparse
import os
import sqlite3
import json
from contextlib import closing
from pathlib import Path
import uvicorn
ROOT = Path(__file__).resolve().parents[2]


def configure(args):
    # Настройки модели не меняют базу аккаунтов и истории.
    database = args.db or os.environ.get("DISPATCH_DB")
    if args.with_ai:
        if not database or not Path(database).is_absolute():
            raise ValueError('Для AI укажите абсолютный --db существующей базы или DISPATCH_DB')
        if not Path(database).is_file():
            raise ValueError('Указанная база не существует; создание пустой базы запрещено')
        try:
            with closing(sqlite3.connect(Path(database).as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
                if not db.execute("SELECT 1 FROM auth_users WHERE role='admin' AND status='active' LIMIT 1").fetchone():
                    raise ValueError('В выбранной базе нет активного администратора')
        except sqlite3.Error as error:
            raise ValueError(f'Невозможно проверить существующую базу: {error}') from error
    database = database or str(ROOT / "data/runtime/dispatch.db")
    os.environ["DISPATCH_DB"] = str(Path(database).resolve())
    if args.with_ai:
        from src.agent.config import load_env_file
        if Path(args.env_file).is_file():
            load_env_file(args.env_file)
        os.environ["DISPATCH_ENABLE_AGENT"] = "1"
        os.environ["DISPATCH_ENABLE_ML"] = "1"
        os.environ["DISPATCH_ENABLE_AUTH"] = "1"
        os.environ.setdefault("LOCAL_LLM_MODEL", "hackathon-qwen3-4b")
        os.environ["DISPATCH_ML_ARTIFACT"] = str(ROOT / 'models/movement-v1/movement.joblib')
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(name, "2")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-ai", action="store_true")
    parser.add_argument("--check-only", action="store_true", help='Проверить настройки без запуска сервера')
    parser.add_argument("--db")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "0.0.0.0"))
    parser.add_argument("--ssl-certfile")
    parser.add_argument("--ssl-keyfile")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Порт должен быть в диапазоне 1..65535")
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        parser.error("Укажите сертификат и ключ TLS вместе")
    try:
        configure(args)
    except ValueError as error:
        parser.error(str(error))
    if args.host == "0.0.0.0":
        if not args.ssl_certfile and os.environ.get("DISPATCH_TRUST_ENCRYPTED_TUNNEL") != "1":
            parser.error("Для LAN необходим TLS или проверенный зашифрованный туннель")
        if os.environ.get("DISPATCH_ENABLE_AUTH", "1") == "0":
            parser.error("Нельзя открыть LAN-сервер без авторизации")
    if args.check_only:
        print(json.dumps({'database': os.environ['DISPATCH_DB'], 'with_ai': args.with_ai,
                          'server_started': False}, ensure_ascii=False))
        return
    uvicorn.run("src.core.main:app", host=args.host, port=args.port, workers=1,
                proxy_headers=False, ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile)


if __name__ == "__main__":
    main()
