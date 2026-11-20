"""Share public vacancy details between application workers and the watcher."""
import json
import logging
from pathlib import Path

from hh_compat import validate_account
from storage import atomic_write

FACT_FIELDS = ("salary", "salary_range", "work_format", "experience", "schedule",
               "work_schedule_by_days", "working_hours")


def vacancy_metadata_path(config_dir, account, vacancy_id):
    vacancy_id = str(vacancy_id)
    if not vacancy_id.isascii() or not vacancy_id.isdigit():
        raise ValueError("Invalid vacancy ID")
    return Path(config_dir) / validate_account(account) / ".vacancy-meta" / f"{vacancy_id}.json"


def save_vacancy_metadata(config_dir, account, vacancy_id, vacancy):
    try:
        path = vacancy_metadata_path(config_dir, account, vacancy_id)
        description = str(vacancy.get("description") or "")
        employer = vacancy.get("employer")
        record = {
            "name": vacancy.get("name") or "",
            "employer": (employer.get("name") or "") if isinstance(employer, dict) else "",
            "description": description,
            "complete": bool(description),
            **{field: vacancy.get(field) for field in FACT_FIELDS},
        }
        atomic_write(path, json.dumps(record, ensure_ascii=False))
    except (OSError, ValueError, TypeError):
        logging.getLogger(__name__).warning("Could not persist vacancy notification metadata")
