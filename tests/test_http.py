import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app
import tracker


class QuietHandler(app.Handler):
    def log_message(self, *args):
        pass


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.old_dir = tracker.DATA_DIR
        tracker.DATA_DIR = Path(cls.temp.name)
        tracker.initialize()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        tracker.DATA_DIR = cls.old_dir
        cls.temp.cleanup()

    def request(self, path, method="GET", body=None, headers=None):
        headers = {"Content-Type": "application/json", **(headers or {})}
        req = Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as exc:
            response = exc
        return response.status, response.read()

    def test_link_add_duplicate_edit_and_export(self):
        with patch("importer.enqueue") as enqueue:
            status, raw = self.request("/api/jobs", "POST", {"url": "https://example.com/job/42"})
            self.assertEqual(status, 201)
            job_id = json.loads(raw)["job"]["id"]
            status, raw = self.request("/api/jobs", "POST", {"url": "https://example.com/job/42?utm_source=test"})
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(raw)["created"])
            enqueue.assert_called_once_with(job_id)
        status, raw = self.request(f"/api/jobs/{job_id}", "PATCH", {"status": "Beworben", "notes": "Über Rückmeldung freuen"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["job"]["notes"], "Über Rückmeldung freuen")
        status, raw = self.request("/api/export")
        self.assertEqual(status, 200)
        self.assertTrue(any(job["id"] == job_id and job["status"] == "Beworben" for job in json.loads(raw)["jobs"]))

    def test_foreign_origin_and_host_cannot_mutate_data(self):
        before = len(tracker.list_jobs())
        status, _ = self.request("/api/jobs", "POST", {"url": "https://example.com/bad"}, {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.request("/api/jobs", "POST", {"url": "https://example.com/bad"}, {"Host": "evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(before, len(tracker.list_jobs()))

    def test_data_files_are_not_served(self):
        status, _ = self.request("/data/jobs.sqlite3")
        self.assertEqual(status, 404)

    def test_mapped_loopback_port_is_accepted(self):
        with patch.dict(os.environ, {"JOBLISTINGS_PUBLIC_PORT": "8877"}):
            status, raw = self.request("/api/health", headers={"Host": "localhost:8877", "Origin": "http://localhost:8877"})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)["status"], "ok")

    def test_invalid_json_and_missing_job_are_recoverable_errors(self):
        status, _ = self.request("/api/jobs", "POST", {"url": ""})
        self.assertEqual(status, 400)
        status, _ = self.request("/api/jobs/999999", "PATCH", {"status": "Beworben"})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
