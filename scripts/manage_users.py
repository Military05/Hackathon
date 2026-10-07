"""Interactive local account bootstrap. Passwords never appear in command arguments."""
import argparse
import getpass
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.auth import AuthManager, OPERATORS
from src.core.service import ApiError


def main():
    parser = argparse.ArgumentParser(description="Локальное создание администратора и диспетчеров")
    parser.add_argument("command", choices=("create-admin", "create-dispatcher", "list"))
    parser.add_argument("--db", default=os.environ.get("DISPATCH_DB", str(ROOT / "data/runtime/dispatch.db")))
    args = parser.parse_args()
    manager = AuthManager(args.db)
    if args.command == "list":
        for user in manager.users():
            print(f"{user['username']} | {user['name']} | {user['role']} | {user['status']} | {user['operator_id'] or 'не назначен'}")
        return 0
    username, name = input("Логин: "), input("Имя: ")
    role = "admin" if args.command == "create-admin" else "dispatcher"
    operator = "dispatcher-3" if role == "admin" else input("Профиль (dispatcher-1/dispatcher-2/dispatcher-3): ").strip()
    if operator not in OPERATORS:
        print("Неизвестный диспетчерский профиль", file=sys.stderr)
        return 1
    password = getpass.getpass("Пароль (15–128 символов): ")
    confirmation = getpass.getpass("Повторите пароль: ")
    if password != confirmation:
        print("Пароли не совпадают", file=sys.stderr)
        return 1
    try:
        user = manager.create_user(username, name, password, role=role, operator_id=operator, status="active")
    except ApiError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print(f"Создан активный аккаунт: {user['username']} ({user['role']}, {user['operator_id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
