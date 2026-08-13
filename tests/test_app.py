import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import app


class HabitLensTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_db = app.DB_PATH
        self.previous_backup_dir = app.BACKUP_DIR
        self.previous_backup_time = app.BACKUP_TIME
        app.DB_PATH = Path(self.tmp.name) / "test.db"
        app.BACKUP_DIR = Path(self.tmp.name) / "backup"
        app.BACKUP_TIME = "00:00"
        app.init_db()
        self.habits = [
            {
                "id": "fiber", "name": "Błonnik", "type": "good", "startDate": "2026-08-03",
                "isArchived": False, "goals": [{"isActive": True, "periodicity": "daily", "value": 25, "unit": "g"}],
                "areas": [{"name": "Zdrowie"}],
            },
            {
                "id": "exercise", "name": "Ćwiczenia", "type": "good", "startDate": "2026-08-03",
                "isArchived": False, "goals": [{"isActive": True, "periodicity": "weekly", "value": 150, "unit": "min"}],
                "areas": [],
            },
            {
                "id": "pepsi", "name": "Bez Pepsi", "type": "bad", "startDate": "2026-08-03",
                "isArchived": False, "goals": [{"isActive": True, "periodicity": "daily", "value": 0, "unit": "rep"}],
                "areas": [],
            },
        ]
        self.stats = {
            "fiber": {"data": {"unit": {"symbol": "g", "type": "mass"}, "dailyProgress": [
                {"date": "2026-08-03", "totalLog": 0.038, "status": "completed"},
                {"date": "2026-08-04", "totalLog": 0.011, "status": "inprogress"},
            ]}},
            "exercise": {"data": {"unit": {"symbol": "min", "type": "duration"}, "dailyProgress": [
                {"date": "2026-08-03", "totalLog": 3000, "status": "failed"},
                {"date": "2026-08-04", "totalLog": 120, "status": "inprogress"},
            ]}},
            "pepsi": {"data": {"unit": {"symbol": "rep", "type": "scalar"}, "dailyProgress": [
                {"date": "2026-08-03", "totalLog": 0, "status": "completed"},
            ]}},
        }

    def tearDown(self):
        app.DB_PATH = self.previous_db
        app.BACKUP_DIR = self.previous_backup_dir
        app.BACKUP_TIME = self.previous_backup_time
        self.tmp.cleanup()

    def fake_request(self, path, params=None):
        habit_id = path.split("/")[1]
        return self.stats[habit_id]

    def sync(self, full=False):
        with patch.object(app, "fetch_habits", return_value=self.habits), \
             patch.object(app, "habitify_request", side_effect=self.fake_request):
            return app.sync_habitify(full=full, today=date(2026, 8, 4))

    def test_sync_is_idempotent_and_normalizes_units(self):
        first = self.sync(full=True)
        second = self.sync()
        self.assertEqual(first["inserted_rows"], 4)
        self.assertEqual(second["updated_rows"], 4)
        with closing(app.connect()) as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM records ORDER BY habit_id,date")]
        fiber = [row for row in rows if row["habit_id"] == "fiber"]
        exercise = [row for row in rows if row["habit_id"] == "exercise"]
        self.assertEqual([row["quantity"] for row in fiber], [38.0, 11.0])
        self.assertEqual(len(exercise), 1)
        self.assertEqual(exercise[0]["date"], "2026-08-03")
        self.assertEqual(exercise[0]["quantity"], 52.0)
        self.assertEqual(exercise[0]["status"], "Incomplete")
        self.assertEqual(app.normalize_quantity(4184, {"symbol": "kCal", "type": "energy"}), 1.0)

    def test_dashboard_and_streaks_use_habitify_records(self):
        self.sync(full=True)
        result = app.dashboard({"start": ["2026-08-03"], "end": ["2026-08-04"]}, today=date(2026, 8, 4))
        self.assertEqual(result["summary"]["records"], 4)
        self.assertEqual(result["summary"]["done"], 2)
        self.assertEqual(result["summary"]["missed"], 0)
        self.assertEqual(result["summary"]["in_progress"], 2)
        self.assertEqual(result["summary"]["rate"], 100.0)
        self.assertEqual(result["summary"]["perfect_days"], 1)
        self.assertNotIn("2026-08-04", {point["date"] for point in result["analytics"]["trends"]["daily"]})
        self.assertEqual(app.streaks(app.all_rows_for_habit("Błonnik"), date(2026, 8, 4)), (1, 1, "day"))
        self.assertIsNotNone(result["analytics"]["data_quality"]["latest_sync"])

    def test_current_periods_become_missed_after_they_end(self):
        self.sync(full=True)
        result = app.dashboard({}, today=date(2026, 8, 10))
        self.assertEqual(result["summary"]["in_progress"], 0)
        self.assertEqual(result["summary"]["missed"], 2)
        self.assertEqual(result["summary"]["rate"], 50.0)

    def test_only_current_periods_have_no_failure_rate(self):
        self.sync(full=True)
        result = app.dashboard({"start": ["2026-08-04"], "end": ["2026-08-04"]},
                               today=date(2026, 8, 4))
        self.assertEqual(result["summary"]["in_progress"], 1)
        self.assertEqual(result["summary"]["missed"], 0)
        self.assertIsNone(result["summary"]["rate"])

    def test_rename_keeps_one_habit_and_updates_history(self):
        self.sync(full=True)
        self.habits[0]["name"] = "Błonnik pokarmowy"
        self.sync()
        with closing(app.connect()) as conn:
            names = [row[0] for row in conn.execute("SELECT DISTINCT name FROM records WHERE habit_id='fiber'")]
            habit_count = conn.execute("SELECT COUNT(*) FROM habits WHERE id='fiber'").fetchone()[0]
        self.assertEqual(names, ["Błonnik pokarmowy"])
        self.assertEqual(habit_count, 1)

    def test_incremental_sync_keeps_full_weeks(self):
        # Regression: the overlap window starts mid-week, so rebuilding the week
        # from that partial range used to wipe the days before it.
        self.habits = [{**self.habits[1], "startDate": "2026-07-27"}]
        days = [{"date": (date(2026, 7, 27) + timedelta(days=offset)).isoformat(),
                 "totalLog": 3000, "status": "failed"} for offset in range(18)]

        def ranged_request(path, params=None):
            start = (params or {}).get("startDate", "0000-01-01")
            return {"data": {**self.stats["exercise"]["data"],
                             "dailyProgress": [d for d in days if d["date"] >= start]}}

        with patch.object(app, "fetch_habits", return_value=self.habits), \
             patch.object(app, "habitify_request", side_effect=ranged_request):
            app.sync_habitify(full=True, today=date(2026, 8, 13))
            app.sync_habitify(today=date(2026, 8, 13))
        with closing(app.connect()) as conn:
            weeks = {row["date"]: row["quantity"]
                     for row in conn.execute("SELECT date,quantity FROM records ORDER BY date")}
        self.assertEqual(weeks, {"2026-07-27": 350.0, "2026-08-03": 350.0, "2026-08-10": 200.0})

    def test_legacy_schema_is_discarded(self):
        legacy_path = Path(self.tmp.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_path)) as conn:
            conn.executescript("CREATE TABLE imports(id INTEGER); CREATE TABLE records(date TEXT);")
        app.DB_PATH = legacy_path
        app.init_db()
        with closing(app.connect()) as conn:
            old_table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='imports'").fetchone()
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            records = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        self.assertIsNone(old_table)
        self.assertEqual(version, 2)
        self.assertEqual(records, 0)

    def test_today_lists_pending_habits_without_a_streak(self):
        # Regression: a habit missed yesterday too has streak 0 and used to be hidden.
        self.stats["fiber"]["data"]["dailyProgress"][0].update(totalLog=0.001, status="failed")
        self.sync(full=True)
        today = app.dashboard({"start": ["2026-08-03"], "end": ["2026-08-04"]}, today=date(2026, 8, 4))["analytics"]["today"]
        pending = {item["name"]: item for item in today["pending"]}
        self.assertEqual(pending["Błonnik"]["streak"], 0)
        self.assertEqual(pending["Błonnik"]["missed"], 1)
        self.assertIn("Ćwiczenia", pending)
        self.assertEqual((today["done"], today["total"]), (0, 2))

    def test_behavior_goal_and_coverage_metrics(self):
        rows = [
            {"date": "2026-01-01", "period": "Daily", "status": "Complete", "goal": 10.0, "quantity": 12.0, "habit_type": "Building", "unit": "min"},
            {"date": "2026-01-02", "period": "Daily", "status": "Incomplete", "goal": 10.0, "quantity": 4.0, "habit_type": "Building", "unit": "min"},
            {"date": "2026-01-04", "period": "Daily", "status": "Complete", "goal": 10.0, "quantity": 15.0, "habit_type": "Building", "unit": "min"},
        ]
        self.assertEqual(app.habit_behavior(rows)["median_recovery"], 1)
        self.assertEqual(app.goal_metrics(rows)["personal_best"]["value"], 15.0)
        self.assertEqual(app.coverage_metrics(rows)["coverage"], 75.0)

    def test_breaking_ratio_is_capped_when_nothing_was_logged(self):
        rows = [
            {"date": "2026-01-01", "period": "Daily", "status": "Complete", "goal": 2.0, "quantity": 0.0, "habit_type": "Breaking", "unit": "szt"},
            {"date": "2026-01-02", "period": "Daily", "status": "Complete", "goal": 2.0, "quantity": 2.0, "habit_type": "Breaking", "unit": "szt"},
        ]
        self.assertEqual(app.goal_metrics(rows)["average_ratio"], 549.5)

    def test_averages_ignore_the_running_period(self):
        self.sync(full=True)
        detail = app.habit_detail("Błonnik", {}, today=date(2026, 8, 4))
        # 2026-08-04 jest w trakcie (11 g), więc liczy się tylko zamknięty dzień.
        self.assertEqual((detail["average"], detail["minimum"]), (38.0, 38.0))
        habit = next(h for h in app.dashboard({}, today=date(2026, 8, 4))["habits"]
                     if h["name"] == "Błonnik")
        self.assertEqual((habit["average"], habit["latest"]), (38.0, 11.0))

    def test_restore_rejects_legacy_schema_before_wiping(self):
        self.sync(full=True)
        legacy = Path(self.tmp.name) / "legacy-v1.db"
        app.backup_database("manual").replace(legacy)
        with closing(sqlite3.connect(legacy)) as conn:
            conn.execute("PRAGMA user_version=1")
        self.assertFalse(app.validate_database(legacy)["valid"])
        with self.assertRaises(app.HabitifyError):
            app.restore_database(legacy)
        with closing(app.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0], 4)

    def test_backup_restore_and_safety_copy(self):
        synced = self.sync(full=True)
        self.assertTrue(synced["backup"])
        snapshot = app.backup_database("manual")
        validation = app.validate_database(snapshot)
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["counts"]["records"], 4)

        with closing(app.connect()) as conn:
            conn.execute("DELETE FROM records")
            conn.commit()
        restored = app.restore_database(snapshot)
        self.assertTrue(restored["ok"])
        self.assertTrue((app.BACKUP_DIR / restored["safety_backup"]).is_file())
        with closing(app.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0], 4)

    def test_scheduled_backup_runs_once_after_configured_time(self):
        app.BACKUP_TIME = "23:59"
        self.sync(full=True)
        now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
        self.assertIsNone(app.backup_if_due(now))
        app.BACKUP_TIME = "00:00"
        first = app.backup_if_due(now)
        self.assertIsNotNone(first)
        self.assertIsNone(app.backup_if_due(now))

    def test_history_is_filtered_and_paginated(self):
        self.sync(full=True)
        self.sync()
        history = app.sync_history({"page": ["2"], "per_page": ["1"],
                                    "date_from": [date.today().isoformat()]})
        self.assertEqual(history["pagination"]["total"], 2)
        self.assertEqual(history["pagination"]["page"], 2)
        self.assertEqual(len(history["items"]), 1)

    def test_backup_is_single_file_without_wal_sidecars(self):
        self.sync(full=True)
        backup = app.backup_database("manual")
        with closing(sqlite3.connect(f"file:{backup}?mode=ro&immutable=1", uri=True)) as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertFalse(Path(str(backup) + "-wal").exists())
        self.assertFalse(Path(str(backup) + "-shm").exists())

    def test_restore_rejects_foreign_database(self):
        self.sync(full=True)
        foreign = Path(self.tmp.name) / "foreign.db"
        foreign.write_bytes(b"not sqlite")
        with self.assertRaises(app.HabitifyError):
            app.restore_database(foreign)
        with closing(app.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
