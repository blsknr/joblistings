"""Shared job types and conservative classification, independent of a user's profile."""
import re

JOB_TYPES = ["Vollzeit", "Teilzeit", "Werkstudent", "Praktikum", "Ausbildung", "Duales Studium", "Trainee", "Minijob", "Freelance", "Abschlussarbeit"]
WORK_MODELS = {
    "any": "Keine Präferenz", "remote_preferred": "Remote bevorzugt",
    "remote_only": "Vollständig remote", "hybrid": "Hybrid", "onsite": "Vor Ort",
}


def infer_job_type(title, employment_type=""):
    patterns = [
        (r"werkstudent|working student|studentische (?:mitarbeit|aushilfe)", "Werkstudent"),
        (r"praktik|internship|\bintern\b", "Praktikum"),
        (r"duales? studium|dual student", "Duales Studium"),
        (r"ausbildung|auszubild|apprentice", "Ausbildung"),
        (r"abschlussarbeit|masterarbeit|bachelorarbeit|\bthesis\b", "Abschlussarbeit"),
        (r"\btrainee\b", "Trainee"), (r"minijob|geringfügig", "Minijob"),
        (r"freelance|freiberuf", "Freelance"),
        (r"teilzeit|part.time", "Teilzeit"), (r"vollzeit|full.time", "Vollzeit"),
    ]
    for text in (title, str(employment_type)):
        found = [label for pattern, label in patterns if re.search(pattern, text, re.I)]
        if found:
            # Specialized types take priority; ambiguous full/part-time remains open.
            return found[0] if found[0] not in {"Vollzeit", "Teilzeit"} or len(found) == 1 else ""
    return ""
