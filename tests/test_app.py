import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import app


class HabitLensTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_db = app.DB_PATH
        app.DB_PATH = Path(self.tmp.name) / "test.db"
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
        with app.connect() as conn:
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
        result = app.dashboard({"start": ["2026-08-03"], "end": ["2026-08-04"]})
        self.assertEqual(result["summary"]["records"], 4)
        self.assertEqual(result["summary"]["done"], 2)
        self.assertEqual(result["summary"]["rate"], 50.0)
        self.assertEqual(result["summary"]["perfect_days"], 1)
        self.assertEqual(app.streaks(app.all_rows_for_habit("Błonnik"), date(2026, 8, 4)), (1, 1, "day"))
        self.assertIsNotNone(result["analytics"]["data_quality"]["latest_sync"])

    def test_rename_keeps_one_habit_and_updates_history(self):
        self.sync(full=True)
        self.habits[0]["name"] = "Błonnik pokarmowy"
        self.sync()
        with app.connect() as conn:
            names = [row[0] for row in conn.execute("SELECT DISTINCT name FROM records WHERE habit_id='fiber'")]
            habit_count = conn.execute("SELECT COUNT(*) FROM habits WHERE id='fiber'").fetchone()[0]
        self.assertEqual(names, ["Błonnik pokarmowy"])
        self.assertEqual(habit_count, 1)

    def test_legacy_schema_is_discarded(self):
        legacy_path = Path(self.tmp.name) / "legacy.db"
        with sqlite3.connect(legacy_path) as conn:
            conn.executescript("CREATE TABLE imports(id INTEGER); CREATE TABLE records(date TEXT);")
        app.DB_PATH = legacy_path
        app.init_db()
        with app.connect() as conn:
            old_table = conn.execute("SELECT 1 FROM sqlite_master WHERE name='imports'").fetchone()
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            records = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        self.assertIsNone(old_table)
        self.assertEqual(version, 2)
        self.assertEqual(records, 0)

    def test_behavior_goal_and_coverage_metrics(self):
        rows = [
            {"date": "2026-01-01", "period": "Daily", "status": "Complete", "goal": 10.0, "quantity": 12.0, "habit_type": "Building", "unit": "min"},
            {"date": "2026-01-02", "period": "Daily", "status": "Incomplete", "goal": 10.0, "quantity": 4.0, "habit_type": "Building", "unit": "min"},
            {"date": "2026-01-04", "period": "Daily", "status": "Complete", "goal": 10.0, "quantity": 15.0, "habit_type": "Building", "unit": "min"},
        ]
        self.assertEqual(app.habit_behavior(rows)["median_recovery"], 1)
        self.assertEqual(app.goal_metrics(rows)["personal_best"]["value"], 15.0)
        self.assertEqual(app.coverage_metrics(rows)["coverage"], 75.0)


if __name__ == "__main__":
    unittest.main()
