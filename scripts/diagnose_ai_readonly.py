"""Read-only diagnosis; does not import or start the application."""
import argparse
import datetime
import json
import sqlite3
from pathlib import Path

DATABASE = None

def main():
    if not DATABASE.is_file():
        raise RuntimeError("Рабочая база не найдена; новая база не создаётся.")
    with sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        print("Источник:", DATABASE)
        print("Задания ИИ:", dict(db.execute("SELECT status,count(*) FROM b1_agent_jobs GROUP BY status").fetchall()))
        rows = db.execute("SELECT incident_id,status,queued_at,error_json,execution_json FROM b1_agent_jobs ORDER BY queued_at DESC LIMIT 12").fetchall()
        for row in rows:
            record = db.execute("SELECT body FROM incidents WHERE incident_id=?", (row["incident_id"],)).fetchone()
            incident = json.loads(record[0]) if record else {}
            error = json.loads(row["error_json"]) if row["error_json"] else {}
            execution = json.loads(row["execution_json"]) if row["execution_json"] else {}
            print(json.dumps({
                "время_UTC": datetime.datetime.fromtimestamp(row["queued_at"], datetime.timezone.utc).isoformat(),
                "статус": row["status"], "тип": incident.get("type"),
                "сектор": incident.get("responsible_sector_id"), "объект": incident.get("asset_id"),
                "код_ошибки": error.get("code"), "сообщение": error.get("message"),
                "версия_инструкции": execution.get("prompt_version"),
            }, ensure_ascii=False))
        db.rollback()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    DATABASE = parser.parse_args().database
    if not DATABASE.is_absolute():
        parser.error('Укажите абсолютный путь существующей базы')
    try:
        main()
    except (OSError, sqlite3.Error, RuntimeError) as error:
        raise SystemExit("Диагностика не завершена: " + str(error))
