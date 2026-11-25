import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import importer
import tracker


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = tracker.DATA_DIR
        tracker.DATA_DIR = Path(self.temp.name)
        tracker.initialize()

    def tearDown(self):
        tracker.DATA_DIR = self.old
        self.temp.cleanup()

    def test_tracking_parameters_deduplicate_but_hash_job_id_survives(self):
        url = "https://company.example/jobs/#/page=0&jobDetailId=4412"
        one, created = tracker.add_job(url)
        two, duplicate = tracker.add_job("https://company.example/jobs/?utm_source=email#/page=0&jobDetailId=4412")
        self.assertTrue(created)
        self.assertFalse(duplicate)
        self.assertEqual(one["id"], two["id"])
        self.assertIn("jobDetailId=4412", one["url"])
        third, created = tracker.add_job(url.replace("4412", "4413"))
        self.assertTrue(created)
        self.assertNotEqual(one["id"], third["id"])

    def test_manual_changes_survive_import_and_restart(self):
        job, _ = tracker.add_job("https://company.example/job/1")
        tracker.update_job(job["id"], {"title": "Mein Titel", "notes": "Nachfragen", "status": "Beworben"})
        updated = tracker.update_job(job["id"], {"title": "Alter Titel", "location": "Tübingen", "sources": {"title": "Webseite"}, "import_state": "ready"}, imported=True)
        self.assertEqual(updated["title"], "Mein Titel")
        self.assertEqual(updated["sources"]["title"], "Manuell bearbeitet")
        tracker.initialize()
        restored = tracker.get_job(job["id"])
        self.assertEqual(restored["status"], "Beworben")
        self.assertEqual(restored["notes"], "Nachfragen")
        self.assertEqual(restored["location"], "Tübingen")

    def test_interrupted_import_is_retryable_and_archive_reversible(self):
        job, _ = tracker.add_job("https://company.example/job/2")
        tracker.update_job(job["id"], {"archived": True})
        tracker.initialize()
        job = tracker.get_job(job["id"])
        self.assertEqual(job["import_state"], "error")
        self.assertTrue(job["archived"])
        self.assertFalse(tracker.update_job(job["id"], {"archived": False})["archived"])

    def test_status_validation(self):
        job, _ = tracker.add_job("https://company.example/job/3")
        with self.assertRaises(ValueError):
            tracker.update_job(job["id"], {"status": "Invalid"})


class ImportTests(unittest.TestCase):
    def test_jsonld_graph_and_unknown_fields(self):
        ld = {"@graph": [{"@type": "JobPosting", "title": "Werkstudent IT", "description": "<p>Software entwickeln</p>", "hiringOrganization": {"name": "Beispiel"}, "jobLocation": {"address": {"addressLocality": "Reutlingen"}}}]}
        data = importer.parse_html(f'<script type="application/ld+json">{json.dumps(ld)}</script>', "https://example.com/job")
        self.assertEqual(data["title"], "Werkstudent IT")
        self.assertEqual(data["company"], "Beispiel")
        self.assertEqual(data["location"], "Reutlingen")
        self.assertEqual(data["hours"], "")
        self.assertEqual(data["remote"], "")
        self.assertTrue(data["_complete"])

    def test_job_dialog_is_used_instead_of_board_or_other_jobs(self):
        html = '<h1>Stellenangebote</h1><p>Andere Stelle in Ulm, 20 Stunden/Woche</p><div role="dialog"><main class="job-detail"><header><h1>Werkstudent Informatik</h1><p>Standort: Stuttgart</p><p>Remotefähig: bis 60%</p></header><p>' + "Energiewende mit Python gestalten. " * 15 + '</p><a href="https://jobs.example/apply">Jetzt bewerben</a></main></div>'
        job = importer.parse_html(html, "https://example.com/#/jobDetailId=4412")
        self.assertEqual(job["title"], "Werkstudent Informatik")
        self.assertEqual(job["location"], "Stuttgart")
        self.assertEqual(job["remote"], "Remotefähig: bis 60%")
        self.assertEqual(job["hours"], "")
        self.assertNotIn("Andere Stelle", job["description"])
        self.assertTrue(job["_complete"])

    def test_mobile_work_is_not_claimed_as_fully_remote(self):
        html = '<main><h1>Werkstudent CoDeSys</h1><p>Wir leben New Work mit mobilem und flexiblem Arbeiten. Der Einsatzort ist Tübingen. Vergütung von 15€/brutto/Stunde.</p></main>'
        data = importer.parse_html(html, "https://www.avat.de/job")
        self.assertEqual(data["remote"], "Mobiles Arbeiten · Umfang offen")
        self.assertEqual(data["location"], "Tübingen")
        self.assertEqual(data["salary"], "15€/brutto/Stunde")
        self.assertEqual(data["hours"], "")

    def test_negative_remote_and_hours_range(self):
        data = importer.parse_html('<main><h1>Werkstudent</h1><p>Nicht remotefähig. 10–12 Stunden pro Woche.</p></main>', "https://example.com/job")
        self.assertEqual(data["remote"], "Vor Ort")
        self.assertEqual(data["hours"], "10–12 Stunden pro Woche")

    def test_shell_is_not_a_successful_import(self):
        data = importer.parse_html("<h1>Stellenangebote</h1><p>Daten werden geladen...</p>", "https://example.com/")
        self.assertFalse(data["_complete"])

    def test_internal_network_is_rejected(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(ValueError):
                importer.validate_public_url("https://localhost/job")


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = {**tracker.DEFAULT_PROFILE, "cities": ["Berlin"],
                        "excluded_cities": ["Leipzig"], "excluded_industries": ["Automobilzulieferer"],
                        "hours_min": 8, "hours_max": 10, "hours_emergency_max": 12}

    def test_excluded_city_stays_excluded_even_if_remote(self):
        flags = tracker.match({"location": "Leipzig", "remote": "Vollständig remote", "hours": "10 Stunden"}, self.profile)
        self.assertIn({"kind": "excluded", "text": "Leipzig ausgeschlossen"}, flags)

    def test_other_city_requires_presence_check_and_extra_hours_are_emergency(self):
        flags = tracker.match({"location": "Hamburg", "hours": "12 Stunden"}, self.profile)
        self.assertIn({"kind": "check", "text": "Präsenztage klären"}, flags)
        self.assertIn({"kind": "check", "text": "Nur im Notfall"}, flags)

    def test_industry_exclusion_comes_from_personal_profile(self):
        flags = tracker.match({"industry": "Automobilzulieferer", "location": "Berlin"}, self.profile)
        self.assertIn({"kind": "excluded", "text": "Automobilzulieferer ausgeschlossen"}, flags)

    def test_config_is_stored_only_in_data_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tracker, "DATA_DIR", Path(directory)):
            tracker.initialize()
            config = Path(directory) / "profile.json"
            self.assertTrue(config.exists())
            config.write_text(json.dumps(self.profile))
            self.assertEqual(tracker.get_profile()["excluded_cities"], ["Leipzig"])
            self.assertIn({"kind": "good", "text": "Stunden passen"}, tracker.match({"hours": "8–10 Stunden"}))


if __name__ == "__main__":
    unittest.main()
