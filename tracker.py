"""Local SQLite store and explicit, evidence-based matching rules."""
import json
import os
import sqlite3
import threading
import profiles
from taxonomy import infer_job_type
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("JOBLISTINGS_DATA_DIR", ROOT / "data"))
STATUSES = ["Gespeichert", "In Vorbereitung", "Beworben", "Gespräch", "Angebot", "Absage", "Zurückgezogen"]
DEFAULT_PROFILE = profiles.DEFAULT_PROFILE

EDITABLE = {"title", "company", "location", "hours", "remote", "salary", "industry", "deadline", "status", "notes", "next_step", "archived", "job_type", "category"}
IMPORT_FIELDS = {"title", "company", "location", "hours", "remote", "salary", "industry", "deadline", "description", "apply_url", "sources", "import_state", "import_message", "imported_at", "job_type"}
LOCK = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def get_profile():
    return profiles.load(DATA_DIR)


def update_profile(values):
    with LOCK:
        return profiles.save(DATA_DIR, get_profile(), values)


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "jobs.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    profile_path = DATA_DIR / "profile.json"
    if not profile_path.exists():
        profile_path.write_text(json.dumps(DEFAULT_PROFILE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '',
            hours TEXT NOT NULL DEFAULT '', remote TEXT NOT NULL DEFAULT '', salary TEXT NOT NULL DEFAULT '',
            industry TEXT NOT NULL DEFAULT '', deadline TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Gespeichert', notes TEXT NOT NULL DEFAULT '',
            next_step TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
            description TEXT NOT NULL DEFAULT '', apply_url TEXT NOT NULL DEFAULT '',
            sources TEXT NOT NULL DEFAULT '{}', manual_fields TEXT NOT NULL DEFAULT '[]',
            import_state TEXT NOT NULL DEFAULT 'pending', import_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, imported_at TEXT
        )""")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
        for column in ("job_type", "category"):
            if column not in columns:
                db.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if "job_type" not in columns:
            for row in db.execute("SELECT id, title FROM jobs WHERE job_type=''").fetchall():
                kind = infer_job_type(row["title"])
                if kind:
                    db.execute("UPDATE jobs SET job_type=? WHERE id=?", (kind, row["id"]))
        # A stopped import is retryable, never silently left spinning forever.
        db.execute("UPDATE jobs SET import_state='error', import_message='Import wurde unterbrochen. Bitte erneut versuchen.' WHERE import_state='pending'")


def canonical_url(value):
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Bitte einen gültigen Link zu einer Stellenanzeige eingeben.")
    if len(value) > 4096:
        raise ValueError("Dieser Link ist zu lang.")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid", "msclkid"}]
    # Hash routes contain actual job IDs on e.g. TransnetBW; do not discard them.
    fragment = parts.fragment if any(k in parts.fragment.lower() for k in ["job", "position", "page=", "/"]) else ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), fragment))


def match(job, profile=None):
    import re
    profile = get_profile() if profile is None else profile
    flags = []
    location = job.get("location", "")
    excluded_city = next((city for city in profile["excluded_cities"] if re.search(r"(?<!\w)" + re.escape(city) + r"(?!\w)", location, re.I)), None)
    location_preferences = bool(profile["cities"] or profile["excluded_cities"])
    if excluded_city:
        flags.append({"kind": "excluded", "text": f"{excluded_city} ausgeschlossen"})
    elif any(city.casefold() in location.casefold() for city in profile["cities"]):
        flags.append({"kind": "good", "text": "Wunschregion"})
    elif location and profile["cities"]:
        flags.append({"kind": "check", "text": "Präsenztage klären"})
    elif not location and location_preferences:
        flags.append({"kind": "unknown", "text": "Standort offen"})
    industry = job.get("industry", "")
    if industry.casefold() in {item.casefold() for item in profile["excluded_industries"]}:
        flags.append({"kind": "excluded", "text": f"{industry} ausgeschlossen"})
    elif industry and industry.casefold() in {item.casefold() for item in profile["industries"]}:
        flags.append({"kind": "good", "text": industry})
    hours = job.get("hours", "")
    numbers = [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", hours)]
    minimum, maximum, emergency = (profile.get(key) for key in ("hours_min", "hours_max", "hours_emergency_max"))
    hours_preferences = any(number is not None for number in (minimum, maximum, emergency))
    if not numbers and hours_preferences:
        flags.append({"kind": "unknown", "text": "Wochenstunden offen"})
    elif numbers and hours_preferences and all((minimum is None or minimum <= number) and (maximum is None or number <= maximum) for number in numbers):
        flags.append({"kind": "good", "text": "Stunden passen"})
    elif len(numbers) == 1 and maximum is not None and emergency is not None and maximum < numbers[0] <= emergency:
        flags.append({"kind": "check", "text": "Nur im Notfall"})
    elif numbers and hours_preferences:
        flags.append({"kind": "check", "text": "Stundenumfang klären"})
    job_type = job.get("job_type", "")
    if not job_type and "job_type" not in job.get("manual_fields", []):
        job_type = infer_job_type(job.get("title", ""))
    if profile.get("job_types"):
        if not job_type:
            flags.append({"kind": "unknown", "text": "Jobart offen"})
        elif job_type.casefold() in {kind.casefold() for kind in profile["job_types"]}:
            flags.append({"kind": "good", "text": "Jobart passt"})
        else:
            flags.append({"kind": "check", "text": "Andere Jobart: " + job_type})
    model = profile.get("work_model", "any")
    remote = job.get("remote", "")
    if model != "any":
        if not remote:
            flags.append({"kind": "unknown", "text": "Arbeitsmodell offen"})
        elif model == "remote_only" and "vollständig remote" not in remote.casefold():
            flags.append({"kind": "check", "text": "Vollständig remote klären"})
        elif model == "remote_preferred" and remote == "Vor Ort":
            flags.append({"kind": "check", "text": "Remote-Wunsch prüfen"})
        elif model == "onsite" and "vollständig remote" in remote.casefold():
            flags.append({"kind": "check", "text": "Präsenzwunsch prüfen"})
    if job.get("remote"):
        flags.append({"kind": "info", "text": job["remote"]})
    return flags


def serialize(row):
    job = dict(row)
    job["archived"] = bool(job["archived"])
    job["sources"] = json.loads(job["sources"])
    job["manual_fields"] = json.loads(job["manual_fields"])
    if not job.get("job_type") and "job_type" not in job["manual_fields"]:
        job["job_type"] = infer_job_type(job["title"])
    job["flags"] = match(job)
    return job


def list_jobs():
    with connect() as db:
        return [serialize(r) for r in db.execute("SELECT * FROM jobs ORDER BY created_at DESC, id DESC")]


def get_job(job_id):
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise KeyError("Stelle nicht gefunden.")
    return serialize(row)


def add_job(url):
    url = canonical_url(url)
    with LOCK, connect() as db:
        previous = db.execute("SELECT id FROM jobs WHERE url=?", (url,)).fetchone()
        if previous:
            return get_job(previous["id"]), False
        stamp = now()
        cursor = db.execute("INSERT INTO jobs (url, company, created_at, updated_at) VALUES (?, ?, ?, ?)",
                            (url, urlsplit(url).hostname.removeprefix("www."), stamp, stamp))
        job_id = cursor.lastrowid
    return get_job(job_id), True


def update_job(job_id, values, imported=False):
    with LOCK, connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("Stelle nicht gefunden.")
        allowed = IMPORT_FIELDS if imported else EDITABLE
        if not imported and set(values) - allowed:
            raise ValueError("Unbekannte Felder.")
        manual = set(json.loads(row["manual_fields"]))
        sources = json.loads(row["sources"])
        clean = {}
        for key, value in values.items():
            if key not in allowed or imported and key in manual:
                continue
            if key == "sources":
                sources.update({k: v for k, v in value.items() if k not in manual})
                continue
            if key == "archived":
                if not isinstance(value, bool):
                    raise ValueError("Ungültiger Archivstatus.")
                clean[key] = int(value)
            else:
                if not isinstance(value, str) or len(value) > (100000 if key == "description" else 10000):
                    raise ValueError("Ungültiger Feldinhalt.")
                if key == "category" and len(value) > 80:
                    raise ValueError("Kategorien dürfen höchstens 80 Zeichen lang sein.")
                if key == "status" and value not in STATUSES:
                    raise ValueError("Ungültiger Bewerbungsstatus.")
                clean[key] = value
            if not imported:
                manual.add(key)
                sources[key] = "Manuell bearbeitet"
        clean.update(updated_at=now(), manual_fields=json.dumps(sorted(manual)), sources=json.dumps(sources, ensure_ascii=False))
        db.execute("UPDATE jobs SET " + ", ".join(f"{key}=?" for key in clean) + " WHERE id=?", [*clean.values(), job_id])
    return get_job(job_id)
