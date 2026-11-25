"""Local dashboard with a small JSON API; Docker publishes loopback only."""
import argparse
import json
import mimetypes
import os
import re
import signal
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import importer
import tracker


class Handler(BaseHTTPRequestHandler):
    server_version = "Joblistings"

    def respond(self, status, value, content_type="application/json; charset=utf-8", extra=None):
        data = json.dumps(value, ensure_ascii=False).encode() if content_type.startswith("application/json") else value
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; img-src 'self' data:; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def valid_request(self, mutation=False):
        ports = {str(self.server.server_port), os.environ.get("JOBLISTINGS_PUBLIC_PORT", str(self.server.server_port))}
        authorities = {f"{host}:{port}" for host in ("127.0.0.1", "localhost") for port in ports}
        if self.headers.get("Host") not in authorities:
            self.respond(403, {"error": "Nur lokaler Zugriff ist erlaubt."})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{authority}" for authority in authorities}:
            self.respond(403, {"error": "Ungültiger Ursprung."})
            return False
        if mutation and self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.respond(415, {"error": "JSON wird erwartet."})
            return False
        return True

    def body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 150000:
            raise ValueError("Die Eingabe ist zu groß.")
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError("Ungültige Eingabe.")
        return data

    def do_GET(self):
        if not self.valid_request():
            return
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.respond(200, {"status": "ok"})
        if path == "/api/jobs":
            return self.respond(200, {"jobs": tracker.list_jobs(), "statuses": tracker.STATUSES, "profile": tracker.get_profile()})
        if path == "/api/export":
            return self.respond(200, {"version": 1, "exported_at": tracker.now(), "profile": tracker.get_profile(), "jobs": tracker.list_jobs()},
                                extra={"Content-Disposition": 'attachment; filename="joblistings-backup.json"'})
        allowed = {"/": "index.html", "/style.css": "style.css", "/app.js": "app.js", "/icon.svg": "icon.svg"}
        if path not in allowed:
            return self.respond(404, {"error": "Nicht gefunden."})
        file = tracker.ROOT / "static" / allowed[path]
        kind = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        self.respond(200, file.read_bytes(), kind + ("; charset=utf-8" if kind.startswith("text/") or "javascript" in kind else ""))

    def do_POST(self):
        self.mutate("POST")

    def do_PATCH(self):
        self.mutate("PATCH")

    def mutate(self, method):
        if not self.valid_request(mutation=True):
            return
        try:
            data = self.body()
            if self.path == "/api/jobs" and method == "POST":
                url = data.get("url", "")
                if not isinstance(url, str) or not url.strip():
                    raise ValueError("Bitte einen Stellenlink einfügen.")
                # Validate syntax now; network access happens only in the importer.
                job, created = tracker.add_job(url)
                if created:
                    importer.enqueue(job["id"])
                return self.respond(201 if created else 200, {"job": tracker.get_job(job["id"]), "created": created})
            found = re.fullmatch(r"/api/jobs/(\d+)(?:/(retry|text))?", self.path)
            if not found:
                return self.respond(404, {"error": "Nicht gefunden."})
            job_id, action = int(found[1]), found[2]
            job = tracker.get_job(job_id)
            if method == "PATCH" and action is None:
                return self.respond(200, {"job": tracker.update_job(job_id, data)})
            if method == "POST" and action == "retry":
                if job["import_state"] != "pending":
                    importer.enqueue(job_id)
                return self.respond(200, {"job": tracker.get_job(job_id)})
            if method == "POST" and action == "text":
                if job["import_state"] == "pending":
                    raise ValueError("Bitte warten, bis der laufende Import abgeschlossen ist.")
                text = data.get("text", "")
                if not isinstance(text, str):
                    raise ValueError("Ungültiger Anzeigentext.")
                return self.respond(200, {"job": importer.import_text(job_id, text)})
            return self.respond(405, {"error": "Aktion nicht unterstützt."})
        except (ValueError, json.JSONDecodeError) as exc:
            self.respond(400, {"error": str(exc)})
        except KeyError:
            self.respond(404, {"error": "Stelle nicht gefunden."})
        except Exception:
            self.log_error("API request failed")
            self.respond(500, {"error": "Speichern fehlgeschlagen. Bitte erneut versuchen."})


def main():
    parser = argparse.ArgumentParser(description="Lokaler Werkstudenten-Tracker")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default=os.environ.get("JOBLISTINGS_HOST", "127.0.0.1"))
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    tracker.initialize()
    print(f"Joblistings läuft: http://127.0.0.1:{args.port}", flush=True)
    if args.open_browser:
        threading.Timer(0.5, webbrowser.open, args=(f"http://127.0.0.1:{args.port}",)).start()
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        importer.POOL.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    main()
