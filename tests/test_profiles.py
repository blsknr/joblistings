import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import importer
import profiles
import tracker
from taxonomy import infer_job_type


class EditableProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_dir = tracker.DATA_DIR
        tracker.DATA_DIR = Path(self.temp.name)
        tracker.initialize()

    def tearDown(self):
        tracker.DATA_DIR = self.old_dir
        self.temp.cleanup()

    def test_legacy_profile_migrates_without_losing_preferences(self):
        path = tracker.DATA_DIR / "profile.json"
        original = {"role": "Vollzeit · Pflege", "cities": ["Hamburg"], "excluded_cities": ["Leipzig"],
                    "hours_min": 32, "hours_max": 38, "remote": "Remote bevorzugt; gelegentliche Präsenz möglich."}
        path.write_text(json.dumps(original))
        profile = tracker.get_profile()
        self.assertEqual(profile["job_types"], ["Vollzeit"])
        self.assertEqual(profile["fields"], ["Pflege"])
        self.assertEqual(profile["work_model"], "remote_preferred")
        self.assertEqual(profile["excluded_cities"], ["Leipzig"])
        self.assertEqual(json.loads(path.read_text()), original)

    def test_save_normalizes_lists_and_persists_after_restart(self):
        profile = tracker.update_profile({"name": "Neuer Start", "job_types": ["Teilzeit", "teilzeit", ""],
                                          "fields": ["Marketing"], "cities": [" Hamburg ", "hamburg"],
                                          "categories": ["Favoriten", "Später"]})
        self.assertEqual(profile["job_types"], ["Teilzeit"])
        self.assertEqual(profile["cities"], ["Hamburg"])
        tracker.initialize()
        self.assertEqual(tracker.get_profile()["categories"], ["Favoriten", "Später"])
        self.assertEqual(profile["role"], "Teilzeit · Marketing")

    def test_invalid_profile_never_overwrites_existing_file(self):
        path = tracker.DATA_DIR / "profile.json"
        original = path.read_bytes()
        cases = [{"hours_min": 40, "hours_max": 20}, {"hours_max": 20, "hours_emergency_max": 10},
                 {"hours_max": float("nan")}, {"hours_min": True}, {"cities": "Hamburg"},
                 {"categories": [1]}, {"work_model": "invalid"}, {"work_model": []}, {"name": ""},
                 {"cities": ["Hamburg"], "excluded_cities": ["hamburg"]}, {"unknown": "bad"}]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                tracker.update_profile(values)
            self.assertEqual(path.read_bytes(), original)

    def test_profile_change_recalculates_existing_job_flags_without_changing_job(self):
        job, _ = tracker.add_job("https://example.com/job/1")
        tracker.update_job(job["id"], {"title": "Pflegefachkraft", "location": "Hamburg", "job_type": "Vollzeit", "hours": "38 Std./Woche"})
        tracker.update_profile({"cities": ["Hamburg"], "job_types": ["Vollzeit"], "hours_min": 35, "hours_max": 40})
        self.assertIn({"kind": "good", "text": "Wunschregion"}, tracker.get_job(job["id"])["flags"])
        tracker.update_profile({"cities": [], "excluded_cities": ["Hamburg"]})
        saved = tracker.get_job(job["id"])
        self.assertIn({"kind": "excluded", "text": "Hamburg ausgeschlossen"}, saved["flags"])
        self.assertEqual(saved["title"], "Pflegefachkraft")

    def test_empty_profile_does_not_impose_region_or_hours_preferences(self):
        flags = tracker.match({"location": "Hamburg", "hours": "40 Stunden"}, profiles.DEFAULT_PROFILE)
        self.assertFalse(any(flag["kind"] in {"check", "excluded"} for flag in flags))

    def test_category_survives_removal_from_profile_and_reimport(self):
        job, _ = tracker.add_job("https://example.com/job/2")
        tracker.update_profile({"categories": ["Favorit"]})
        tracker.update_job(job["id"], {"category": "Favorit"})
        tracker.update_profile({"categories": []})
        tracker.update_job(job["id"], {"title": "Neu eingelesen"}, imported=True)
        self.assertEqual(tracker.get_job(job["id"])["category"], "Favorit")

    def test_manually_cleared_job_type_stays_empty_after_restart_and_import(self):
        job, _ = tracker.add_job("https://example.com/job/3")
        tracker.update_job(job["id"], {"title": "Werkstudent IT", "job_type": ""})
        tracker.initialize()
        tracker.update_job(job["id"], {"job_type": "Werkstudent"}, imported=True)
        self.assertEqual(tracker.get_job(job["id"])["job_type"], "")

    def test_previous_database_gains_columns_and_keeps_data(self):
        # Simulate the previous schema rather than simply checking new-table defaults.
        path = tracker.DATA_DIR / "jobs.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("ALTER TABLE jobs DROP COLUMN job_type")
            db.execute("ALTER TABLE jobs DROP COLUMN category")
            db.execute("INSERT INTO jobs (url,title,created_at,updated_at) VALUES (?,?,?,?)", ("https://example.com/old", "Werkstudent IT", tracker.now(), tracker.now()))
        tracker.initialize()
        tracker.initialize()
        job = tracker.list_jobs()[0]
        self.assertEqual(job["job_type"], "Werkstudent")
        self.assertEqual(job["category"], "")


class GeneralJobTypeTests(unittest.TestCase):
    def test_job_types_are_not_tied_to_informatics(self):
        self.assertEqual(infer_job_type("Ausbildung zur Pflegefachkraft"), "Ausbildung")
        self.assertEqual(infer_job_type("Marketing Manager", "FULL_TIME"), "Vollzeit")
        self.assertEqual(infer_job_type("Verkäufer in Vollzeit oder Teilzeit"), "")

    def test_non_technical_job_import_is_successful(self):
        html = '<main><h1>Pflegefachkraft in Teilzeit</h1><h2>Ihre Aufgaben</h2><p>' + "Sie begleiten und betreuen Menschen im Alltag. " * 12 + '</p><a href="/apply">Jetzt bewerben</a></main>'
        data = importer.parse_html(html, "https://example.com/job")
        self.assertTrue(data["_complete"])
        self.assertEqual(data["job_type"], "Teilzeit")


if __name__ == "__main__":
    unittest.main()
