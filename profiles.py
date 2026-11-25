"""Validated local search preferences, including migration of the original profile."""
import json
import os
import re
import tempfile
from copy import deepcopy

from taxonomy import JOB_TYPES, WORK_MODELS

DEFAULT_PROFILE = {
    "name": "Meine Jobsuche", "job_types": [], "fields": [], "role": "Deine Jobsuche",
    "industries": [], "excluded_industries": [], "cities": [], "excluded_cities": [],
    "categories": [], "hours": "Ohne Stundenlimit", "hours_min": None,
    "hours_max": None, "hours_emergency_max": None, "work_model": "any", "remote": "",
}
LIST_FIELDS = {"job_types", "fields", "industries", "excluded_industries", "cities", "excluded_cities", "categories"}
NUMBER_FIELDS = {"hours_min", "hours_max", "hours_emergency_max"}
EDITABLE = LIST_FIELDS | NUMBER_FIELDS | {"name", "work_model", "remote"}


def normalize(raw):
    result = {**deepcopy(DEFAULT_PROFILE), **raw}
    # Older installations stored the job type and field together in 'role'.
    if "job_types" not in raw and raw.get("role"):
        parts = [part.strip() for part in raw["role"].split("·") if part.strip()]
        result["job_types"] = [part for part in parts if part in JOB_TYPES]
        result["fields"] = [part for part in parts if part not in JOB_TYPES and part not in {"Deine Jobsuche", "Individuell festlegen"}]
    if "work_model" not in raw and raw.get("remote"):
        text = raw["remote"].casefold()
        if "remote" in text and "bevorzugt" in text:
            result["work_model"] = "remote_preferred"
    result["role"] = " · ".join(result["job_types"] + result["fields"]) or "Deine Jobsuche"
    minimum, maximum, emergency = (result.get(key) for key in ("hours_min", "hours_max", "hours_emergency_max"))
    if minimum is not None and maximum is not None:
        result["hours"] = f"{minimum:g}–{maximum:g} Std. / Woche"
    elif maximum is not None:
        result["hours"] = f"Bis {maximum:g} Std. / Woche"
    elif minimum is not None:
        result["hours"] = f"Ab {minimum:g} Std. / Woche"
    else:
        result["hours"] = "Ohne Stundenlimit"
    if emergency is not None:
        result["hours"] += f" · bis {emergency:g} im Ausnahmefall"
    return result


def load(directory):
    path = directory / "profile.json"
    if not path.exists():
        return deepcopy(DEFAULT_PROFILE)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Das Suchprofil muss ein JSON-Objekt enthalten.")
    return normalize(raw)


def save(directory, current, values):
    if set(values) - EDITABLE:
        raise ValueError("Unbekannte Profilfelder.")
    clean = {}
    for key, value in values.items():
        if key in LIST_FIELDS:
            if not isinstance(value, list) or len(value) > 40 or any(not isinstance(item, str) or len(item) > 80 for item in value):
                raise ValueError("Listen dürfen höchstens 40 Einträge mit je 80 Zeichen enthalten.")
            seen = set()
            clean[key] = []
            for item in value:
                item = re.sub(r"\s+", " ", item).strip()
                if item and item.casefold() not in seen:
                    seen.add(item.casefold())
                    clean[key].append(item)
        elif key in NUMBER_FIELDS:
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 168):
                raise ValueError("Wochenstunden müssen zwischen 0 und 168 liegen.")
            clean[key] = value
        elif key == "work_model":
            if not isinstance(value, str) or value not in WORK_MODELS:
                raise ValueError("Bitte ein gültiges Arbeitsmodell wählen.")
            clean[key] = value
        else:
            if not isinstance(value, str) or len(value) > (2000 if key == "remote" else 100):
                raise ValueError("Profilname oder Präsenzhinweise sind zu lang.")
            clean[key] = value.strip()
    merged = {**current, **clean}
    if not merged["name"]:
        raise ValueError("Bitte einen Namen für dein Suchprofil eingeben.")
    minimum, maximum, emergency = (merged.get(key) for key in ("hours_min", "hours_max", "hours_emergency_max"))
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("Die Mindeststunden dürfen nicht über den Wunsch-Maximalstunden liegen.")
    if emergency is not None and (maximum is None or emergency < maximum):
        raise ValueError("Die Ausnahmegrenze muss mindestens den Wunsch-Maximalstunden entsprechen.")
    for positive, negative in (("cities", "excluded_cities"), ("industries", "excluded_industries")):
        if {item.casefold() for item in merged[positive]} & {item.casefold() for item in merged[negative]}:
            raise ValueError("Ein Eintrag kann nicht zugleich gewünscht und ausgeschlossen sein.")
    result = normalize(merged)
    directory.mkdir(parents=True, exist_ok=True)
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, prefix=".profile-", suffix=".tmp", delete=False) as file:
            path = file.name
            json.dump(result, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(path, directory / "profile.json")
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
    return result
