#!/usr/bin/env python3
"""Habit Lens — dependency-free analytics dashboard for Habitify."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import secrets
import sqlite3
import statistics
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, unquote, urlparse
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def load_local_env() -> None:
    """Load a simple local .env without overriding the process environment."""
    env_path = BASE_DIR / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value)


load_local_env()

DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "habits.db")))
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
HABITIFY_API_KEY = os.getenv("HABITIFY_API_KEY", "").strip()
HABITIFY_BASE_URL = os.getenv("HABITIFY_BASE_URL", "https://api.habitify.me/v2").rstrip("/")
HABITIFY_TIMEOUT = float(os.getenv("HABITIFY_TIMEOUT", "30"))
SYNC_INTERVAL_MINUTES = max(0, int(os.getenv("SYNC_INTERVAL_MINUTES", "30")))
SYNC_OVERLAP_DAYS = max(1, int(os.getenv("SYNC_OVERLAP_DAYS", "8")))
SYNC_LOCK = threading.Lock()
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", str(DB_PATH.parent / "backup")))
BACKUP_KEEP = max(1, int(os.getenv("BACKUP_KEEP", "14")))
BACKUP_TIME = os.getenv("BACKUP_TIME", "03:00").strip()
MAX_BACKUP_BYTES = max(1024, int(os.getenv("MAX_BACKUP_MB", "100")) * 1024 * 1024)
BACKUP_LOCK = threading.RLock()

REQUIRED_BACKUP_TABLES = {"sync_runs", "habits", "records"}


class HabitifyError(RuntimeError):
    pass


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def database():
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with database() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        legacy = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='imports'"
        ).fetchone()
        if version < 2 or legacy:
            # Version 2 intentionally starts from Habitify only; legacy CSV data is discarded.
            conn.executescript(
                """
                DROP TABLE IF EXISTS records;
                DROP TABLE IF EXISTS imports;
                DROP TABLE IF EXISTS habits;
                DROP TABLE IF EXISTS sync_runs;
                """
            )
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                full_sync INTEGER NOT NULL DEFAULT 0,
                habit_count INTEGER NOT NULL DEFAULT 0,
                total_rows INTEGER NOT NULL DEFAULT 0,
                inserted_rows INTEGER NOT NULL,
                updated_rows INTEGER NOT NULL,
                deleted_rows INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS habits (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                archived INTEGER NOT NULL DEFAULT 0,
                period TEXT NOT NULL,
                habit_type TEXT NOT NULL,
                goal REAL NOT NULL DEFAULT 0,
                unit TEXT NOT NULL DEFAULT '',
                list_name TEXT NOT NULL DEFAULT '',
                start_date TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS records (
                date TEXT NOT NULL,
                habit_id TEXT NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                archived INTEGER NOT NULL DEFAULT 0,
                period TEXT NOT NULL,
                habit_type TEXT NOT NULL,
                goal REAL NOT NULL DEFAULT 0,
                quantity REAL NOT NULL DEFAULT 0,
                unit TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                list_name TEXT NOT NULL DEFAULT '',
                sync_id INTEGER NOT NULL REFERENCES sync_runs(id),
                PRIMARY KEY (date, habit_id, period)
            );
            CREATE INDEX IF NOT EXISTS idx_records_habit_date ON records(habit_id, date);
            CREATE INDEX IF NOT EXISTS idx_records_name_date ON records(name, date);
            CREATE INDEX IF NOT EXISTS idx_records_date ON records(date);
            PRAGMA user_version=2;
            """
        )


def validate_database(path: Path) -> dict:
    """Validate integrity and schema of a Habit Lens SQLite backup."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Plik nie istnieje lub jest pusty")
        with path.open("rb") as handle:
            if handle.read(16) != b"SQLite format 3\x00":
                raise ValueError("Plik nie jest bazą SQLite")
        conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"integrity_check: {integrity}")
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            missing = sorted(REQUIRED_BACKUP_TABLES - tables)
            if missing:
                raise ValueError("Brak tabel Habit Lens: " + ", ".join(missing))
            # Przywracanie woła init_db(), a ten kasuje tabele ze starego schematu.
            # Odrzucenie tutaj zatrzymuje taką bazę przed, a nie po skasowaniu danych.
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 2 or "imports" in tables:
                raise ValueError(f"Backup pochodzi ze starszego schematu (user_version={version})")
            counts = {
                "habits": conn.execute("SELECT COUNT(*) FROM habits").fetchone()[0],
                "records": conn.execute("SELECT COUNT(*) FROM records").fetchone()[0],
                "syncs": conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0],
            }
        finally:
            conn.close()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"valid": True, "error": None, "integrity": "ok", "counts": counts,
                "size_kb": round(path.stat().st_size / 1024, 1), "sha256": digest.hexdigest()}
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        return {"valid": False, "error": str(exc), "integrity": None, "counts": None,
                "size_kb": round(path.stat().st_size / 1024, 1) if path.exists() else 0,
                "sha256": None}


def backup_filename(kind: str = "backup") -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    return f"habit-lens-{kind}-{stamp}.db"


def cleanup_backup_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def cleanup_backup_directory_sidecars() -> None:
    if not BACKUP_DIR.is_dir():
        return
    for pattern in ("habit-lens-*.db-wal", "habit-lens-*.db-shm"):
        for path in BACKUP_DIR.glob(pattern):
            path.unlink(missing_ok=True)


def backup_clock() -> tuple[int, int]:
    try:
        parsed = datetime.strptime(BACKUP_TIME, "%H:%M")
    except ValueError as exc:
        raise HabitifyError("BACKUP_TIME musi mieć format HH:MM, np. 03:00") from exc
    return parsed.hour, parsed.minute


def prune_backups() -> None:
    backups = sorted(BACKUP_DIR.glob("habit-lens-*.db"), key=lambda path: path.stat().st_mtime)
    for old in backups[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)
        cleanup_backup_sidecars(old)


def backup_database(kind: str = "backup", *, prune: bool = True) -> Path:
    """Create a consistent online backup and verify it before returning."""
    with BACKUP_LOCK:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        target = BACKUP_DIR / backup_filename(kind)
        source = connect()
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
            destination.execute("PRAGMA journal_mode=DELETE")
        finally:
            destination.close()
            source.close()
        cleanup_backup_sidecars(target)
        validation = validate_database(target)
        if not validation["valid"]:
            target.unlink(missing_ok=True)
            raise HabitifyError(f"Backup nie przeszedł kontroli: {validation['error']}")
        if prune:
            prune_backups()
        return target


def resolve_backup(filename: str) -> Path:
    if (not filename or Path(filename).name != filename
            or not filename.startswith("habit-lens-") or not filename.endswith(".db")):
        raise HabitifyError("Nieprawidłowa nazwa backupu")
    path = BACKUP_DIR / filename
    if not path.is_file():
        raise HabitifyError("Nie znaleziono backupu")
    return path


def list_backups() -> list[dict]:
    if not BACKUP_DIR.is_dir():
        return []
    result = []
    for path in sorted(BACKUP_DIR.glob("habit-lens-*.db"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = path.stat()
        result.append({
            "file": path.name, "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
            "age_hours": round((time.time() - stat.st_mtime) / 3600, 1),
            "kind": ("pre_restore" if "-pre-restore-" in path.name else
                     "manual" if "-manual-" in path.name else
                     "scheduled" if "-scheduled-" in path.name else "snapshot"),
        })
    return result


def history_options(params: dict[str, list[str]]) -> tuple[int, int, str | None, str | None]:
    try:
        page = max(1, int(params.get("page", ["1"])[0]))
        per_page = min(50, max(1, int(params.get("per_page", ["10"])[0])))
    except ValueError as exc:
        raise HabitifyError("Nieprawidłowa strona historii") from exc
    date_from = params.get("date_from", [""])[0] or None
    date_to = params.get("date_to", [""])[0] or None
    try:
        if date_from:
            date.fromisoformat(date_from)
        if date_to:
            date.fromisoformat(date_to)
    except ValueError as exc:
        raise HabitifyError("Daty historii muszą mieć format RRRR-MM-DD") from exc
    if date_from and date_to and date_from > date_to:
        raise HabitifyError("Data początkowa nie może być późniejsza niż końcowa")
    return page, per_page, date_from, date_to


def page_result(items: list[dict], total: int, page: int, per_page: int) -> dict:
    pages = max(1, (total + per_page - 1) // per_page)
    return {"items": items, "pagination": {"page": page, "per_page": per_page,
            "total": total, "pages": pages, "has_previous": page > 1,
            "has_next": page < pages}}


def backup_status(params: dict[str, list[str]] | None = None) -> dict:
    backups = list_backups()
    latest = backups[0] if backups else None
    validation = validate_database(resolve_backup(latest["file"])) if latest else None
    page, per_page, date_from, date_to = history_options(params or {})
    filtered = [item for item in backups
                if (not date_from or item["modified"][:10] >= date_from)
                and (not date_to or item["modified"][:10] <= date_to)]
    offset = (page - 1) * per_page
    return {"healthy": bool(latest and validation and validation["valid"]),
            "keep": BACKUP_KEEP, "backup_time": BACKUP_TIME,
            "latest": latest, "latest_validation": validation,
            "backups": filtered[offset:offset + per_page],
            "pagination": page_result([], len(filtered), page, per_page)["pagination"]}


def sync_history(params: dict[str, list[str]]) -> dict:
    page, per_page, date_from, date_to = history_options(params)
    clauses, values = [], []
    if date_from:
        clauses.append("substr(started_at,1,10) >= ?")
        values.append(date_from)
    if date_to:
        clauses.append("substr(started_at,1,10) <= ?")
        values.append(date_to)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    offset = (page - 1) * per_page
    with database() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM sync_runs{where}", values).fetchone()[0]
        rows = [dict(row) for row in conn.execute(
            f"SELECT * FROM sync_runs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*values, per_page, offset])]
    return page_result(rows, total, page, per_page)


def backup_if_due(now: datetime | None = None) -> Path | None:
    with BACKUP_LOCK:
        now = now or datetime.now().astimezone()
        hour, minute = backup_clock()
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < due:
            return None
        for item in list_backups():
            if item["kind"] != "scheduled":
                continue
            modified = datetime.fromisoformat(item["modified"])
            if modified.date() == now.date():
                return None
        with database() as conn:
            if conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0:
                return None
        return backup_database("scheduled")


def restore_database(source_path: Path) -> dict:
    """Validate a backup, save the current DB, then restore it atomically via SQLite."""
    if not SYNC_LOCK.acquire(blocking=False):
        raise HabitifyError("Synchronizacja trwa — spróbuj przywrócić backup za chwilę")
    try:
        with BACKUP_LOCK:
            validation = validate_database(source_path)
            if not validation["valid"]:
                raise HabitifyError(f"Nie można przywrócić backupu: {validation['error']}")
            safety = backup_database("pre-restore", prune=False)
            source = sqlite3.connect(f"file:{source_path}?mode=ro&immutable=1", uri=True)
            destination = connect()
            try:
                source.backup(destination)
                destination.commit()
            finally:
                destination.close()
                source.close()
                cleanup_backup_sidecars(source_path)
            init_db()
            restored = validate_database(DB_PATH)
            if not restored["valid"]:
                raise HabitifyError(f"Przywrócona baza nie przeszła kontroli: {restored['error']}")
            prune_backups()
            return {"ok": True, "restored_from": source_path.name,
                    "safety_backup": safety.name, "validation": restored}
    finally:
        SYNC_LOCK.release()


def habitify_request(path: str, params: dict[str, str | int] | None = None) -> dict:
    if not HABITIFY_API_KEY:
        raise HabitifyError("Brak HABITIFY_API_KEY w konfiguracji")
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{HABITIFY_BASE_URL}/{path.lstrip('/')}{query}",
        headers={
            "Accept": "application/json",
            "User-Agent": "HabitLens/2.0",
            "X-API-Key": HABITIFY_API_KEY,
        },
    )
    try:
        with urlopen(request, timeout=HABITIFY_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise HabitifyError(f"Habitify API zwróciło HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HabitifyError(f"Nie udało się połączyć z Habitify: {exc}") from exc


def fetch_habits() -> list[dict]:
    habits: dict[str, dict] = {}
    for archived in (False, True):
        offset = 0
        while True:
            payload = habitify_request("habits", {
                "archived": str(archived).lower(), "limit": 100, "offset": offset,
            })
            page = payload.get("data") or []
            for habit in page:
                if habit.get("id"):
                    habits[habit["id"]] = habit
            pagination = payload.get("pagination") or {}
            total = int(pagination.get("total", len(page)))
            offset += len(page)
            if not page or offset >= total:
                break
    return list(habits.values())


def active_goal(habit: dict) -> dict:
    goals = habit.get("goals") or []
    active = [goal for goal in goals if goal.get("isActive")]
    candidates = active or goals
    return max(candidates, key=lambda goal: goal.get("createdAt", "")) if candidates else {}


def normalize_quantity(value: float | int | None, unit: dict) -> float:
    """Convert Habitify statistics' base units to the unit shown to the user."""
    quantity = float(value or 0)
    unit_type = str(unit.get("type") or "").lower()
    symbol = str(unit.get("symbol") or "").lower()
    if unit_type == "duration":
        return quantity / {"min": 60, "h": 3600}.get(symbol, 1)
    if unit_type == "mass":
        return quantity * {"mg": 1_000_000, "g": 1_000, "kg": 1}.get(symbol, 1)
    if unit_type == "energy":
        return quantity / {"cal": 4.184, "kcal": 4_184, "kj": 1_000}.get(symbol, 1)
    return quantity


def normalized_habit(habit: dict) -> dict:
    goal = active_goal(habit)
    periodicity = str(goal.get("periodicity") or "daily").lower()
    if periodicity not in {"daily", "weekly"}:
        raise HabitifyError(
            f"Nawyk {habit.get('name', habit.get('id'))!r} ma nieobsługiwany okres: {periodicity}"
        )
    areas = habit.get("areas") or []
    return {
        "id": str(habit["id"]),
        "name": str(habit.get("name") or "Bez nazwy"),
        "description": str(habit.get("description") or ""),
        "archived": int(bool(habit.get("isArchived"))),
        "period": periodicity.capitalize(),
        "habit_type": "Breaking" if habit.get("type") == "bad" else "Building",
        "goal": float(goal.get("value") or 0),
        "unit": str(goal.get("unit") or habit.get("customUnitName") or ""),
        "list_name": ", ".join(str(area.get("name")) for area in areas if area.get("name")),
        "start_date": str(habit.get("startDate") or date.today().isoformat()),
    }


def records_from_statistics(habit: dict, statistics_payload: dict) -> list[dict]:
    stats = statistics_payload.get("data") or {}
    unit_info = stats.get("unit") or {"symbol": habit["unit"]}
    points = stats.get("dailyProgress") or []
    if habit["period"] == "Daily":
        records = []
        for point in points:
            if not point.get("date"):
                continue
            quantity = normalize_quantity(point.get("totalLog"), unit_info)
            api_status = str(point.get("status") or "").lower()
            threshold_met = quantity <= habit["goal"] if habit["habit_type"] == "Breaking" else quantity >= habit["goal"]
            # Habitify marks measured "bad" habits as failed whenever a positive
            # value is logged, even when it remains below a non-zero ceiling.
            complete = api_status != "skipped" and (api_status == "completed" or threshold_met)
            records.append({
                "date": str(point["date"]), "quantity": quantity,
                "status": "Complete" if complete else "Incomplete",
            })
        return records

    weeks: dict[str, float] = defaultdict(float)
    for point in points:
        if not point.get("date"):
            continue
        week = period_key(date.fromisoformat(str(point["date"])), "Weekly").isoformat()
        weeks[week] += normalize_quantity(point.get("totalLog"), unit_info)
    records = []
    for week, quantity in sorted(weeks.items()):
        complete = quantity <= habit["goal"] if habit["habit_type"] == "Breaking" else quantity >= habit["goal"]
        records.append({"date": week, "quantity": quantity, "status": "Complete" if complete else "Incomplete"})
    return records


def sync_habitify(full: bool = False, today: date | None = None) -> dict:
    if not SYNC_LOCK.acquire(blocking=False):
        raise HabitifyError("Synchronizacja już trwa")
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with database() as conn:
        cursor = conn.execute(
            "INSERT INTO sync_runs(started_at,status,full_sync,inserted_rows,updated_rows) VALUES(?, 'running', ?, 0, 0)",
            (started_at, int(full)),
        )
        sync_id = cursor.lastrowid
    try:
        sync_day = today or date.today()
        raw_habits = fetch_habits()
        prepared = []
        with database() as conn:
            last_dates = {
                row["habit_id"]: row["last_date"]
                for row in conn.execute("SELECT habit_id, MAX(date) AS last_date FROM records GROUP BY habit_id")
            }
        for raw_habit in raw_habits:
            habit = normalized_habit(raw_habit)
            start = date.fromisoformat(habit["start_date"])
            if not full and last_dates.get(habit["id"]):
                overlap_start = date.fromisoformat(last_dates[habit["id"]]) - timedelta(days=SYNC_OVERLAP_DAYS)
                start = max(start, overlap_start)
            # Weekly records are rewritten whole periods at a time, so a mid-week
            # start would rebuild that week from a partial range and undercount it.
            start = period_key(start, habit["period"])
            stats = habitify_request(f"habits/{habit['id']}/statistics", {
                "startDate": start.isoformat(), "endDate": sync_day.isoformat(),
            })
            prepared.append((habit, start, records_from_statistics(habit, stats)))

        completed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        inserted = updated = deleted = total = 0
        seen_ids = [habit["id"] for habit, _, _ in prepared]
        with database() as conn:
            existing_keys = {
                (row["date"], row["habit_id"], row["period"])
                for row in conn.execute("SELECT date,habit_id,period FROM records")
            }
            for habit, start, records in prepared:
                conn.execute(
                    """INSERT INTO habits(id,name,description,archived,period,habit_type,goal,unit,list_name,start_date,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,description=excluded.description,
                    archived=excluded.archived,period=excluded.period,habit_type=excluded.habit_type,
                    goal=excluded.goal,unit=excluded.unit,list_name=excluded.list_name,
                    start_date=excluded.start_date,updated_at=excluded.updated_at""",
                    (habit["id"], habit["name"], habit["description"], habit["archived"], habit["period"],
                     habit["habit_type"], habit["goal"], habit["unit"], habit["list_name"], habit["start_date"], completed_at),
                )
                conn.execute(
                    """UPDATE records SET name=?,description=?,archived=?,period=?,habit_type=?,goal=?,unit=?,list_name=?
                    WHERE habit_id=?""",
                    (habit["name"], habit["description"], habit["archived"], habit["period"], habit["habit_type"],
                     habit["goal"], habit["unit"], habit["list_name"], habit["id"]),
                )
                delete_from = period_key(start, habit["period"]).isoformat()
                old_range_keys = {key for key in existing_keys if key[1] == habit["id"] and key[0] >= delete_from}
                new_keys = {(record["date"], habit["id"], habit["period"]) for record in records}
                deleted += len(old_range_keys - new_keys)
                conn.execute("DELETE FROM records WHERE habit_id=? AND date>=?", (habit["id"], delete_from))
                for record in records:
                    key = (record["date"], habit["id"], habit["period"])
                    inserted += int(key not in existing_keys)
                    updated += int(key in existing_keys)
                    conn.execute(
                        """INSERT INTO records
                        (date,habit_id,name,description,archived,period,habit_type,goal,quantity,unit,status,list_name,sync_id)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (record["date"], habit["id"], habit["name"], habit["description"], habit["archived"],
                         habit["period"], habit["habit_type"], habit["goal"], record["quantity"], habit["unit"],
                         record["status"], habit["list_name"], sync_id),
                    )
                total += len(records)
            if seen_ids:
                placeholders = ",".join("?" for _ in seen_ids)
                conn.execute(f"DELETE FROM habits WHERE id NOT IN ({placeholders})", seen_ids)
            else:
                conn.execute("DELETE FROM habits")
            conn.execute(
                """UPDATE sync_runs SET completed_at=?,status='success',habit_count=?,total_rows=?,
                inserted_rows=?,updated_rows=?,deleted_rows=? WHERE id=?""",
                (completed_at, len(prepared), total, inserted, updated, deleted, sync_id),
            )
        backup_name = None
        backup_error = None
        try:
            backup = backup_if_due()
            backup_name = backup.name if backup else None
        except (HabitifyError, OSError, sqlite3.Error) as exc:
            backup_error = str(exc)
        return {"ok": True, "id": sync_id, "started_at": started_at, "completed_at": completed_at,
                "full_sync": full, "habit_count": len(prepared), "total_rows": total,
                "inserted_rows": inserted, "updated_rows": updated, "deleted_rows": deleted,
                "backup": backup_name, "backup_error": backup_error}
    except Exception as exc:
        completed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with database() as conn:
            conn.execute(
                "UPDATE sync_runs SET completed_at=?,status='failed',error=? WHERE id=?",
                (completed_at, str(exc)[:1000], sync_id),
            )
        raise
    finally:
        SYNC_LOCK.release()


def is_complete(row: dict | sqlite3.Row) -> bool:
    return str(row["status"]).lower() == "complete"


def period_key(day: date, period: str) -> date:
    if period.lower() == "weekly":
        return day - timedelta(days=day.weekday())
    return day


def is_running(row: dict | sqlite3.Row, today: date) -> bool:
    """Czy rekord opisuje okres, który jeszcze trwa — niezależnie od statusu."""
    return row["date"] == period_key(today, row["period"]).isoformat()


def record_state(row: dict | sqlite3.Row, today: date) -> str:
    if is_complete(row):
        return "complete"
    return "in_progress" if is_running(row, today) else "missed"


def streaks(rows: list[dict], today: date) -> tuple[int, int, str]:
    if not rows:
        return 0, 0, "day"
    weekly = rows[0]["period"].lower() == "weekly"
    unit = "week" if weekly else "day"
    values = {period_key(date.fromisoformat(r["date"]), r["period"]): is_complete(r) for r in rows}
    completed = sorted(d for d, done in values.items() if done)
    if not completed:
        return 0, 0, unit
    step = timedelta(weeks=1) if weekly else timedelta(days=1)
    best = run = 0
    previous = None
    for day in completed:
        run = run + 1 if previous is not None and day - previous == step else 1
        best = max(best, run)
        previous = day

    cursor = period_key(today, rows[0]["period"])
    current_period_done = values.get(cursor, False)
    # An unfinished current day/week gets grace until the period ends.
    if not current_period_done:
        cursor -= step
    current = 0
    while values.get(cursor, False):
        current += 1
        cursor -= step
    return current, best, unit


def query_records(params: dict[str, list[str]]) -> list[dict]:
    clauses, args = [], []
    if params.get("start"):
        clauses.append("date >= ?")
        args.append(params["start"][0])
    if params.get("end"):
        clauses.append("date <= ?")
        args.append(params["end"][0])
    for field, column in (("habit", "name"), ("list", "list_name"), ("period", "period")):
        values = [v for v in params.get(field, []) if v]
        if values:
            placeholders = ",".join("?" for _ in values)
            clauses.append(f"{column} IN ({placeholders})")
            args.extend(values)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with database() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM records" + where + " ORDER BY date,name", args)]


def all_rows_for_habit(name: str) -> list[dict]:
    with database() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM records WHERE name=? ORDER BY date", (name,))]


def rate_of(rows: list[dict], today: date | None = None) -> float | None:
    today = today or date.today()
    resolved = [row for row in rows if record_state(row, today) != "in_progress"]
    if not resolved:
        return None
    return round(sum(is_complete(r) for r in resolved) / len(resolved) * 100, 1)


def previous_period_params(params: dict[str, list[str]], rows: list[dict]) -> tuple[dict[str, list[str]], str | None, str | None]:
    start_raw = params.get("start", [None])[0]
    end_raw = params.get("end", [None])[0]
    if not start_raw or not end_raw:
        if not rows:
            return dict(params), None, None
        start_raw = min(r["date"] for r in rows)
        end_raw = max(r["date"] for r in rows)
    start, end = date.fromisoformat(start_raw), date.fromisoformat(end_raw)
    length = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=length - 1)
    result = {key: list(value) for key, value in params.items() if key not in {"start", "end"}}
    result["start"] = [previous_start.isoformat()]
    result["end"] = [previous_end.isoformat()]
    return result, previous_start.isoformat(), previous_end.isoformat()


def period_series(rows: list[dict], period: str, today: date | None = None) -> list[dict]:
    today = today or date.today()
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["period"].lower() != period.lower():
            continue
        key = period_key(date.fromisoformat(row["date"]), row["period"]).isoformat()
        buckets[key].append(row)
    points = []
    rates = []
    for key, items in sorted(buckets.items()):
        if any(record_state(row, today) == "in_progress" for row in items):
            continue
        value = sum(is_complete(r) for r in items) / len(items) * 100
        rates.append(value)
        points.append({
            "date": key, "rate": round(value, 1),
            "avg7": round(sum(rates[-7:]) / len(rates[-7:]), 1),
            "avg30": round(sum(rates[-30:]) / len(rates[-30:]), 1),
        })
    return points


def habit_behavior(rows: list[dict]) -> dict:
    ordered = sorted(rows, key=lambda r: r["date"])
    longest_break = current_break = 0
    recoveries, waiting = [], 0
    for row in ordered:
        if is_complete(row):
            if waiting:
                recoveries.append(waiting)
                waiting = 0
            current_break = 0
        else:
            waiting += 1
            current_break += 1
            longest_break = max(longest_break, current_break)
    return {
        "longest_break": longest_break,
        "median_recovery": round(statistics.median(recoveries), 1) if recoveries else None,
        "recoveries": len(recoveries),
    }


def goal_metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"average_margin": None, "average_ratio": None, "personal_best": None}
    latest = rows[-1]
    goal = latest["goal"]
    breaking = latest["habit_type"].lower() == "breaking"
    margins = [(r["goal"] - r["quantity"]) if breaking else (r["quantity"] - r["goal"]) for r in rows]
    ratios = []
    for row in rows:
        if row["goal"] <= 0:
            continue
        if breaking:
            # Zero logów bije limit bezwarunkowo; bez sufitu dzielenie przez ~0
            # wywalało średnią w miliony procent.
            ratios.append(min(row["goal"] / row["quantity"] * 100, 999) if row["quantity"] > 0 else 999)
        else:
            ratios.append(row["quantity"] / row["goal"] * 100)
    best = min(rows, key=lambda r: r["quantity"]) if breaking else max(rows, key=lambda r: r["quantity"])
    return {
        "average_margin": round(sum(margins) / len(margins), 2),
        "average_ratio": round(sum(ratios) / len(ratios), 1) if ratios else None,
        "personal_best": {"value": best["quantity"], "date": best["date"], "unit": best["unit"]},
        "zero_goal_successes": sum(r["quantity"] == 0 for r in rows) if goal == 0 else None,
        "zero_goal_violations": sum(r["quantity"] != 0 for r in rows) if goal == 0 else None,
    }


def coverage_metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"first": None, "last": None, "present": 0, "expected": 0, "gaps": 0, "coverage": 0}
    unique = sorted({period_key(date.fromisoformat(r["date"]), r["period"]) for r in rows})
    weekly = rows[0]["period"].lower() == "weekly"
    expected = ((unique[-1] - unique[0]).days // (7 if weekly else 1)) + 1
    gaps = max(0, expected - len(unique))
    return {
        "first": unique[0].isoformat(), "last": unique[-1].isoformat(),
        "present": len(unique), "expected": expected, "gaps": gaps,
        "coverage": round(len(unique) / expected * 100, 1) if expected else 0,
    }


def correlations(rows: list[dict]) -> list[dict]:
    by_habit: dict[str, dict[str, bool]] = defaultdict(dict)
    for row in rows:
        if row["period"].lower() == "daily":
            by_habit[row["name"]][row["date"]] = is_complete(row)
    names = sorted(by_habit)
    result = []
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            shared = sorted(set(by_habit[first]) & set(by_habit[second]))
            if not shared:
                continue
            a = [by_habit[first][d] for d in shared]
            b = [by_habit[second][d] for d in shared]
            n11 = sum(x and y for x, y in zip(a, b))
            n10 = sum(x and not y for x, y in zip(a, b))
            n01 = sum(not x and y for x, y in zip(a, b))
            n00 = len(shared) - n11 - n10 - n01
            denominator = ((n11+n10)*(n01+n00)*(n11+n01)*(n10+n00)) ** 0.5
            phi = (n11*n00 - n10*n01) / denominator if denominator else None
            result.append({
                "first": first, "second": second, "observations": len(shared),
                "both_complete": round(n11 / len(shared) * 100, 1),
                "agreement": round((n11+n00) / len(shared) * 100, 1),
                "correlation": round(phi, 3) if phi is not None else None,
                "reliable": len(shared) >= 30,
            })
    return sorted(result, key=lambda x: (not x["reliable"], -(abs(x["correlation"]) if x["correlation"] is not None else 0), -x["observations"]))[:12]


def extended_analytics(rows: list[dict], previous_rows: list[dict], all_by_name: dict[str, list[dict]], today: date) -> dict:
    current_rate, previous_rate = rate_of(rows, today), rate_of(previous_rows, today)
    daily_series = period_series(rows, "Daily", today)
    weekly_series = period_series(rows, "Weekly", today)
    primary_series = daily_series or weekly_series
    current14 = [p["rate"] for p in primary_series[-14:]]
    previous14 = [p["rate"] for p in primary_series[-28:-14]]
    momentum = None
    if len(primary_series) >= 28:
        momentum = round(sum(current14)/len(current14) - sum(previous14)/len(previous14), 1)

    weekdays_data = []
    for weekday in range(7):
        items = [r for r in rows if r["period"].lower() == "daily" and date.fromisoformat(r["date"]).weekday() == weekday]
        weekdays_data.append({"day": weekday, "rate": rate_of(items, today), "records": len(items)})
    valid_weekdays = [item for item in weekdays_data if item["records"] and item["rate"] is not None]
    best_weekday = max(valid_weekdays, key=lambda x: x["rate"]) if valid_weekdays else None
    worst_weekday = min(valid_weekdays, key=lambda x: x["rate"]) if valid_weekdays else None

    # Regularity is the variation of ISO-week completion rates, not daily scores.
    iso_weeks: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["period"].lower() == "daily":
            d = date.fromisoformat(row["date"])
            iso_weeks[f"{d.isocalendar().year}-{d.isocalendar().week:02d}"].append(row)
    current_iso_week = f"{today.isocalendar().year}-{today.isocalendar().week:02d}"
    week_rates = [rate_of(items, today) for key, items in sorted(iso_weeks.items()) if key != current_iso_week]
    week_rates = [value for value in week_rates if value is not None]
    regularity_sd = round(statistics.pstdev(week_rates), 1) if len(week_rates) >= 2 else None

    current_by_name: dict[str, list[dict]] = defaultdict(list)
    previous_by_name: dict[str, list[dict]] = defaultdict(list)
    for row in rows: current_by_name[row["name"]].append(row)
    for row in previous_rows: previous_by_name[row["name"]].append(row)
    changes = []
    for name in sorted(set(current_by_name) & set(previous_by_name)):
        cr, pr = rate_of(current_by_name[name], today), rate_of(previous_by_name[name], today)
        if cr is None or pr is None:
            continue
        changes.append({"name": name, "current_rate": cr, "previous_rate": pr,
                        "delta": round(cr-pr, 1), "current_records": len(current_by_name[name]),
                        "previous_records": len(previous_by_name[name]),
                        "reliable": min(len(current_by_name[name]), len(previous_by_name[name])) >= 5})
    improved = max(changes, key=lambda x: x["delta"]) if changes else None
    regressed = min(changes, key=lambda x: x["delta"]) if changes else None

    list_groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows: list_groups[row["list_name"] or "Bez listy"].append(row)
    lists = [{"name": name, "rate": rate_of(items, today), "done": sum(is_complete(r) for r in items),
              "in_progress": sum(record_state(r, today) == "in_progress" for r in items),
              "total": len(items)} for name, items in sorted(list_groups.items())]

    months_data: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["period"].lower() == "daily": months_data[row["date"][:7]].append(row)
    monthly = []
    for month, items in sorted(months_data.items()):
        day_groups: dict[str, list[dict]] = defaultdict(list)
        for row in items: day_groups[row["date"]].append(row)
        monthly.append({"month": month, "rate": rate_of(items, today), "records": len(items),
                        "perfect_days": sum(
                            all(record_state(r, today) == "complete" for r in day)
                            for day in day_groups.values()
                        )})

    quality, behaviors, records_data, pending = [], [], [], []
    today_done = today_total = 0
    for name, items in all_by_name.items():
        current_streak, longest, unit = streaks(items, today)
        period = items[0]["period"]
        step = timedelta(weeks=1) if period.lower() == "weekly" else timedelta(days=1)
        by_key = {period_key(date.fromisoformat(r["date"]), r["period"]): r for r in items}
        current_key = period_key(today, period)
        current_row = by_key.get(current_key)
        if current_row:
            today_total += 1
            if is_complete(current_row):
                today_done += 1
            else:
                # Every open period counts, streak or not: a habit already slipping
                # is exactly the one worth surfacing, and the old streak>0 guard hid it.
                cursor, missed = current_key - step, 0
                while cursor in by_key and not is_complete(by_key[cursor]):
                    missed += 1
                    cursor -= step
                pending.append({"name": name, "period": period, "type": current_row["habit_type"],
                                "streak": current_streak,
                                "unit": unit, "missed": missed, "quantity": current_row["quantity"],
                                "goal": current_row["goal"], "value_unit": current_row["unit"]})
        selected_items = current_by_name.get(name, [])
        if selected_items:
            resolved_items = [item for item in selected_items if record_state(item, today) != "in_progress"]
            behaviors.append({"name": name, **habit_behavior(resolved_items)})
            records_data.append({"name": name, **goal_metrics(resolved_items)})
        quality.append({"name": name, "period": period, **coverage_metrics(items)})
    pending.sort(key=lambda item: (-item["streak"], -item["missed"], item["name"].lower()))

    with database() as conn:
        latest_sync = conn.execute(
            "SELECT * FROM sync_runs WHERE status='success' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    current_count, previous_count = len(rows), len(previous_rows)
    coverage_warning = None
    if previous_count and (current_count < previous_count * .7 or current_count > previous_count * 1.3):
        coverage_warning = "Porównywane okresy mają różną liczbę rekordów; zmianę interpretuj ostrożnie."

    return {
        "comparison": {"current_rate": current_rate, "previous_rate": previous_rate,
                       "delta": round(current_rate-previous_rate, 1) if current_rate is not None and previous_rate is not None else None,
                       "current_records": current_count, "previous_records": previous_count},
        "trends": {"daily": daily_series, "weekly": weekly_series, "momentum": momentum},
        "weekdays": weekdays_data, "best_weekday": best_weekday, "worst_weekday": worst_weekday,
        "regularity": {"weekly_stddev": regularity_sd, "weeks": len(week_rates)},
        "habit_changes": changes, "most_improved": improved, "most_regressed": regressed,
        "lists": lists, "monthly": monthly, "behaviors": behaviors,
        "goal_metrics": records_data,
        "correlations": correlations([row for row in rows if record_state(row, today) != "in_progress"]),
        "today": {"date": today.isoformat(), "done": today_done,
                  "total": today_total, "pending": pending},
        "data_quality": {"latest_sync": dict(latest_sync) if latest_sync else None,
                         "habits": quality, "coverage_warning": coverage_warning,
                         "current_records": current_count, "previous_records": previous_count},
    }


def dashboard(params: dict[str, list[str]], today: date | None = None) -> dict:
    rows = query_records(params)
    today = today or date.today()
    previous_params, previous_start, previous_end = previous_period_params(params, rows)
    previous_rows = query_records(previous_params) if previous_start else []
    # One un-dated read backs both streaks and analytics; the per-habit lookups
    # this replaces meant a full table scan for every habit on screen.
    all_by_name: dict[str, list[dict]] = defaultdict(list)
    for row in query_records({k: v for k, v in params.items() if k not in {"start", "end"}}):
        all_by_name[row["name"]].append(row)
    grouped: dict[str, list[dict]] = defaultdict(list)
    days: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["name"]].append(row)
        if row["period"].lower() == "daily":
            days[row["date"]].append(row)

    habit_stats = []
    for name, items in grouped.items():
        current, longest, unit = streaks(all_by_name[name], today)
        done = sum(is_complete(r) for r in items)
        missed = sum(record_state(r, today) == "missed" for r in items)
        in_progress = sum(record_state(r, today) == "in_progress" for r in items)
        # Trwający okres ma z definicji niepełną wartość — także wtedy, gdy cel
        # już padł, więc liczy się koniec okresu, nie jego status.
        quantities = [r["quantity"] for r in items if not is_running(r, today)]
        habit_stats.append({
            "name": name, "period": items[0]["period"], "type": items[0]["habit_type"],
            "unit": items[0]["unit"], "goal": items[-1]["goal"],
            "list": items[0]["list_name"], "done": done, "missed": missed,
            "in_progress": in_progress, "rate": rate_of(items, today), "current_streak": current,
            "longest_streak": longest, "streak_unit": unit,
            "average": round(sum(quantities) / len(quantities), 2) if quantities else 0,
            "latest": items[-1]["quantity"] if items else 0,
        })
    habit_stats.sort(key=lambda h: (h["rate"] is None, -(h["rate"] or 0), h["name"].lower()))
    done = sum(is_complete(r) for r in rows)
    missed = sum(record_state(r, today) == "missed" for r in rows)
    in_progress = sum(record_state(r, today) == "in_progress" for r in rows)
    total = len(rows)
    perfect = sum(1 for items in days.values()
                  if items and all(record_state(r, today) == "complete" for r in items))
    heatmap = [
        {"date": key, "done": sum(is_complete(r) for r in items),
         "missed": sum(record_state(r, today) == "missed" for r in items),
         "in_progress": sum(record_state(r, today) == "in_progress" for r in items),
         "total": len(items),
         "rate": round(sum(is_complete(r) for r in items) / len(items) * 100)}
        for key, items in sorted(days.items())
    ]
    with database() as conn:
        bounds = conn.execute("SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM records").fetchone()
        options = conn.execute(
            "SELECT DISTINCT name, list_name, period FROM records ORDER BY name"
        ).fetchall()
    analytics = extended_analytics(rows, previous_rows, all_by_name, today)
    analytics["comparison"]["previous_start"] = previous_start
    analytics["comparison"]["previous_end"] = previous_end
    return {
        "summary": {"done": done, "missed": missed, "in_progress": in_progress,
                    "rate": round(done / (done + missed) * 100, 1) if done + missed else None,
                    "perfect_days": perfect, "records": total, "resolved": done + missed},
        "heatmap": heatmap, "habits": habit_stats,
        "bounds": dict(bounds) if bounds else {"min_date": None, "max_date": None},
        "options": {
            "habits": sorted({r["name"] for r in options}),
            "lists": sorted({r["list_name"] for r in options if r["list_name"]}),
            "periods": sorted({r["period"] for r in options}),
        },
        "analytics": analytics,
    }


def habit_detail(name: str, params: dict[str, list[str]], today: date | None = None) -> dict | None:
    today = today or date.today()
    params = dict(params)
    params["habit"] = [name]
    rows = query_records(params)
    all_rows = all_rows_for_habit(name)
    if not all_rows:
        return None
    current, longest, unit = streaks(all_rows, today)
    done = sum(is_complete(r) for r in rows)
    missed = sum(record_state(r, today) == "missed" for r in rows)
    in_progress = sum(record_state(r, today) == "in_progress" for r in rows)
    quantities = [r["quantity"] for r in rows if not is_running(r, today)]
    weekday = defaultdict(lambda: [0, 0])
    for row in rows:
        idx = date.fromisoformat(row["date"]).weekday()
        if record_state(row, today) != "in_progress":
            weekday[idx][1] += 1
            weekday[idx][0] += int(is_complete(row))
    return {
        "name": name, "period": all_rows[-1]["period"], "type": all_rows[-1]["habit_type"],
        "goal": all_rows[-1]["goal"], "unit": all_rows[-1]["unit"],
        "list": all_rows[-1]["list_name"], "current_streak": current,
        "longest_streak": longest, "streak_unit": unit, "done": done,
        "missed": missed, "in_progress": in_progress, "rate": rate_of(rows, today),
        "average": round(sum(quantities) / len(quantities), 2) if quantities else 0,
        "minimum": min(quantities) if quantities else 0, "maximum": max(quantities) if quantities else 0,
        "records": [{"date": r["date"], "quantity": r["quantity"],
                     "state": record_state(r, today), "status": r["status"]} for r in rows],
        "weekdays": [{"day": i, "done": weekday[i][0], "total": weekday[i][1]} for i in range(7)],
    }


def extract_multipart(body: bytes, content_type: str) -> tuple[bytes, str]:
    if "boundary=" not in content_type:
        raise HabitifyError("Brak granicy multipart")
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"').encode()
    for part in body.split(b"--" + boundary):
        head, separator, content = part.partition(b"\r\n\r\n")
        if separator and b'name="file"' in head:
            filename = "habit-lens-backup.db"
            disposition = head.decode("utf-8", errors="replace").lstrip("\r\n").split("\r\n", 1)[0]
            for chunk in disposition.split(";"):
                if chunk.strip().startswith("filename="):
                    filename = chunk.split("=", 1)[1].strip().strip('"')
            if content.endswith(b"\r\n"):
                content = content[:-2]
            return content, Path(filename).name
    raise HabitifyError("Nie znaleziono pola file")


class Handler(BaseHTTPRequestHandler):
    server_version = "HabitLens/2.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}")

    def send_json(self, payload, status=200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def send_file(self, path: Path) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.sqlite3")
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                self.wfile.write(chunk)

    def read_body(self, limit: int = MAX_BACKUP_BYTES + 65536) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise HabitifyError("Nieprawidłowy Content-Length") from exc
        if length <= 0:
            raise HabitifyError("Brak danych w żądaniu")
        if length > limit:
            raise HabitifyError(f"Żądanie przekracza limit {limit // 1024 // 1024} MB")
        return self.rfile.read(length)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/health":
                return self.send_json({"status": "ok", "habitify_configured": bool(HABITIFY_API_KEY)})
            if parsed.path == "/api/config":
                with database() as conn:
                    latest = conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
                return self.send_json({
                    "habitify_configured": bool(HABITIFY_API_KEY),
                    "sync_interval_minutes": SYNC_INTERVAL_MINUTES,
                    "sync_in_progress": SYNC_LOCK.locked(),
                    "latest_sync": dict(latest) if latest else None,
                })
            if parsed.path == "/api/dashboard":
                return self.send_json(dashboard(params))
            if parsed.path == "/api/backups":
                return self.send_json(backup_status(params))
            if parsed.path.startswith("/api/backups/") and parsed.path.endswith("/download"):
                filename = unquote(parsed.path.removeprefix("/api/backups/").removesuffix("/download"))
                return self.send_file(resolve_backup(filename))
            if parsed.path.startswith("/api/habits/"):
                detail = habit_detail(unquote(parsed.path.removeprefix("/api/habits/")), params)
                return self.send_json(detail or {"error": "Habit not found"}, 200 if detail else 404)
            if parsed.path == "/api/syncs":
                return self.send_json(sync_history(params))
            return self.serve_static(parsed.path)
        except (HabitifyError, ValueError, sqlite3.Error) as exc:
            return self.send_json({"error": str(exc)}, 400)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            params = parse_qs(parsed.query)
            if parsed.path == "/api/backup":
                path = backup_database("manual")
                return self.send_json({"ok": True, "backup": path.name,
                                       "validation": validate_database(path)}, HTTPStatus.CREATED)
            if parsed.path.startswith("/api/backups/") and parsed.path.endswith("/restore"):
                filename = unquote(parsed.path.removeprefix("/api/backups/").removesuffix("/restore"))
                payload = json.loads(self.read_body().decode("utf-8"))
                if payload.get("confirmation") != "PRZYWRÓĆ":
                    raise HabitifyError("Wymagane potwierdzenie PRZYWRÓĆ")
                return self.send_json(restore_database(resolve_backup(filename)))
            if parsed.path == "/api/backups/restore-upload":
                if params.get("confirmation", [""])[0] != "PRZYWRÓĆ":
                    raise HabitifyError("Wymagane potwierdzenie PRZYWRÓĆ")
                body = self.read_body()
                content_type = self.headers.get("Content-Type", "")
                if not content_type.startswith("multipart/form-data"):
                    raise HabitifyError("Backup należy wysłać jako multipart/form-data")
                payload, filename = extract_multipart(body, content_type)
                if len(payload) > MAX_BACKUP_BYTES:
                    raise HabitifyError(f"Backup przekracza limit {MAX_BACKUP_BYTES // 1024 // 1024} MB")
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                temporary = BACKUP_DIR / (".restore-upload-" + secrets.token_hex(8) + ".db")
                try:
                    temporary.write_bytes(payload)
                    result = restore_database(temporary)
                    result["restored_from"] = Path(filename).name
                    return self.send_json(result)
                finally:
                    temporary.unlink(missing_ok=True)
            if parsed.path != "/api/sync":
                return self.send_json({"error": "Not found"}, 404)
            full = params.get("full", [""])[0].lower() in {"1", "true", "yes"}
            return self.send_json(sync_habitify(full=full))
        except (HabitifyError, ValueError, json.JSONDecodeError, UnicodeDecodeError, OSError, sqlite3.Error) as exc:
            return self.send_json({"error": str(exc)}, 400)

    def serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        target = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            target = STATIC_DIR / "index.html"
        raw = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        # Everything revalidates: there is no build step fingerprinting filenames,
        # so a cached app.js against a redeployed API is a silently broken dashboard.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(raw)


def auto_sync_loop() -> None:
    while True:
        try:
            result = sync_habitify()
            print(
                f"Habitify sync: {result['habit_count']} habits, "
                f"{result['inserted_rows']} inserted, {result['updated_rows']} updated"
            )
        except Exception as exc:
            print(f"Habitify sync failed: {exc}")
        if not SYNC_INTERVAL_MINUTES:
            return
        time.sleep(SYNC_INTERVAL_MINUTES * 60)


def backup_loop() -> None:
    while True:
        try:
            created = backup_if_due()
            if created:
                print(f"Automatyczny backup: {created.name}")
        except Exception as exc:
            print(f"Automatyczny backup nie powiódł się: {exc}")
        threading.Event().wait(60)


if __name__ == "__main__":
    init_db()
    cleanup_backup_directory_sidecars()
    try:
        backup_if_due()
    except Exception as exc:
        print(f"Backup przy starcie nie powiódł się: {exc}")
    print(f"Habit Lens: http://localhost:{PORT}")
    print(f"Habitify API: {'configured' if HABITIFY_API_KEY else 'missing HABITIFY_API_KEY'}")
    threading.Thread(target=backup_loop, name="scheduled-backup", daemon=True).start()
    threading.Thread(target=auto_sync_loop, name="habitify-sync", daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
