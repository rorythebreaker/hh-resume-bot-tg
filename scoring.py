"""Deterministic, profile-aware vacancy scoring. Never authorizes applications."""
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
WEIGHTS = {"role": 30, "skills": 25, "duties": 20, "experience": 10, "work_format": 10, "salary": 5}
DEFAULT_RULES = {
    "role": ["devops", "devsecops", "sre", "системный администратор", "инженер инфраструктуры", "linux administrator"],
    "skills": ["linux", "docker", "kubernetes", "ansible", "zabbix", "grafana", "terraform", "windows server", "nginx"],
    "duties": ["автоматизац", "администрирован", "мониторинг", "инфраструктур", "ci/cd", "развертыван"],
    "negative": ["первая линия поддержки", "1-я линия поддержки", "l1 support", "call-центр", "колл-центр"],
}
# Explicitly configurable per profile; no inferred hard rejection for absent fields.
def rules_for(profile):
    custom = (profile or {}).get("scoring", {}).get("rules", {})
    return {key: list(custom.get(key, values)) if isinstance(custom.get(key, values), list) else values
            for key, values in DEFAULT_RULES.items()}


def _contains(text, phrase):
    phrase = phrase.strip().casefold()
    if not phrase:
        return False
    if phrase.isascii() and phrase.replace(" ", "").isalnum():
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text))
    return phrase in text


def evaluate(vacancy, profile=None):
    """Return explainable heuristic score, not a calibrated probability."""
    rules = rules_for(profile)
    title = str(vacancy.get("name") or "").casefold()
    body = " ".join(str(vacancy.get(k) or "") for k in ("description", "snippet")).casefold()
    text = title + " " + body
    role = any(_contains(title, p) for p in rules["role"])
    matched_skills = [p for p in rules["skills"] if _contains(text, p)]
    duties = any(_contains(body, p) for p in rules["duties"])
    negative = [p for p in rules["negative"] if _contains(text, p)]
    # Unknown metadata contributes zero; this is an explicit heuristic, not a negative label.
    experience = bool(vacancy.get("experience"))
    work_format = bool(vacancy.get("work_format"))
    salary = bool(vacancy.get("salary") or vacancy.get("salary_range"))
    parts = {
        "role": WEIGHTS["role"] if role else 0,
        "skills": round(WEIGHTS["skills"] * min(len(set(matched_skills)), 4) / 4),
        "duties": WEIGHTS["duties"] if duties else 0,
        "experience": WEIGHTS["experience"] if experience else 0,
        "work_format": WEIGHTS["work_format"] if work_format else 0,
        "salary": WEIGHTS["salary"] if salary else 0,
    }
    penalty = 40 if negative else 0
    score = max(0, min(100, sum(parts.values()) - penalty))
    return {"score": score, "parts": parts, "matched_skills": matched_skills,
            "negative_matches": negative, "version": SCHEMA_VERSION, "mode": "shadow"}


def database_path():
    return Path(os.environ.get("CONFIG_DIR", "/app/config")) / "scoring" / "scoring.db"


def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=5)
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS evaluations (
      profile_id TEXT NOT NULL,
      vacancy_id TEXT NOT NULL,
      evaluated_at TEXT NOT NULL,
      score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 100),
      rule_version INTEGER NOT NULL,
      features_json TEXT NOT NULL,
      vacancy_json TEXT NOT NULL,
      PRIMARY KEY (profile_id, vacancy_id)
    );
    CREATE TABLE IF NOT EXISTS feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      profile_id TEXT NOT NULL,
      vacancy_id TEXT NOT NULL,
      decision TEXT NOT NULL CHECK(decision IN ('positive','negative','unsure')),
      reason TEXT,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS feedback_profile_idx ON feedback(profile_id, vacancy_id);
    """)
    return connection


def save_evaluation(profile_id, vacancy, result, path=None):
    """Idempotent upsert; never changes existing human feedback."""
    vacancy_id = str(vacancy.get("id") or "")
    if not profile_id or not vacancy_id.isdigit():
        raise ValueError("profile_id and numeric vacancy id are required")
    snapshot = {key: vacancy.get(key) for key in ("id", "name", "description", "snippet", "experience", "work_format", "salary", "salary_range")}
    now = datetime.now(timezone.utc).isoformat()
    with _connect(path or database_path()) as db:
        db.execute("""INSERT INTO evaluations
            (profile_id,vacancy_id,evaluated_at,score,rule_version,features_json,vacancy_json)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(profile_id,vacancy_id) DO UPDATE SET
            evaluated_at=excluded.evaluated_at,score=excluded.score,
            rule_version=excluded.rule_version,features_json=excluded.features_json,
            vacancy_json=excluded.vacancy_json""",
            (profile_id, vacancy_id, now, result["score"], result["version"],
             json.dumps(result, ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False)))


def add_feedback(profile_id, vacancy_id, decision, reason=None, path=None):
    """Explicit user labels only; HH rejection is not a negative training label."""
    if decision not in ("positive", "negative", "unsure"):
        raise ValueError("Invalid feedback decision")
    if not profile_id or not str(vacancy_id).isdigit():
        raise ValueError("Invalid profile or vacancy")
    with _connect(path or database_path()) as db:
        exists = db.execute("SELECT 1 FROM evaluations WHERE profile_id=? AND vacancy_id=?",
                            (profile_id, str(vacancy_id))).fetchone()
        if not exists:
            raise ValueError("Vacancy has not been scored for this profile")
        db.execute("INSERT INTO feedback(profile_id,vacancy_id,decision,reason,created_at) VALUES (?,?,?,?,?)",
                   (profile_id, str(vacancy_id), decision, reason,
                    datetime.now(timezone.utc).isoformat()))
