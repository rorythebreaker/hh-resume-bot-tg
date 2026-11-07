"""Shared library for working with profiles config.
Used by tg_bot.py, tg_watcher.py, _summary.py, apply-vacancies wrapper.
"""
import json
import os
import re
import functools
import threading
from contextlib import contextmanager
from storage import transaction, atomic_write
from hh_compat import validate_account
from pathlib import Path

APP_DIR = Path(os.environ.get("APP_DIR", "/app"))
PROFILES_FILE = Path(
    os.environ.get("HH_PROFILES_FILE", str(APP_DIR / "profiles.json"))
)
PROFILES_EXAMPLE = APP_DIR / "profiles.example.json"
LETTERS_DIR = APP_DIR / "letters"
DEFAULT_LETTER = "letter_default.txt"

# Блок AI в профиле. Пустая строка в filter — фильтрация выключена; допустимые
# режимы соответствуют --ai-filter {heavy,light,custom} в 1.8.26.
AI_FILTER_MODES = ("", "heavy", "light", "custom")
DEFAULT_AI = {
    "enabled": False,        # --use-ai: генерация сопроводительных писем
    "filter": "",            # --ai-filter: отсев вакансий до отклика
    "filter_prompt": "",     # --ai-filter-prompt: нужен только для режима custom
    "system_prompt": "",     # --system-prompt: роль модели при генерации письма
    "message_prompt": "",    # --message-prompt: что именно генерировать
    "rate_limit": 40,        # --ai-rate-limit: запросов в минуту (дефолт утилиты)
    "dry_run": False,        # --dry-run: прогон без отправки откликов
}

DEFAULT_PROFILES = {
    "active": None,
    "profiles": {},
}


def _ensure_files():
    """Creates profiles.json (from example if available) and letters/ if missing."""
    LETTERS_DIR.mkdir(parents=True, exist_ok=True)
    if not PROFILES_FILE.exists():
        # При первом запуске копируем example, если он есть
        if PROFILES_EXAMPLE.exists():
            try:
                PROFILES_FILE.write_text(PROFILES_EXAMPLE.read_text(encoding="utf-8"))
                return
            except Exception:
                pass
        with PROFILES_FILE.open("w", encoding="utf-8") as f:
            json.dump(DEFAULT_PROFILES, f, ensure_ascii=False, indent=2)


def _serialized(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with transaction(PROFILES_FILE.with_suffix('.lock')):
            return fn(*args, **kwargs)
    return wrapped


@_serialized
def load_profiles():
    _ensure_files()
    data = json.loads(PROFILES_FILE.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('profiles', {}), dict):
        raise ValueError('Invalid profiles.json; restore a backup')
    data.setdefault('active', None)
    data.setdefault('profiles', {})
    for profile in data['profiles'].values():
        validate_account(profile.get('hh_account', 'main'))
    return data


@_serialized
def save_profiles(data):
    atomic_write(PROFILES_FILE, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


_context = threading.local()

@contextmanager
def profile_context(pid, profile):
    previous = getattr(_context, 'profile', None)
    _context.profile = (pid, profile)
    try:
        yield
    finally:
        _context.profile = previous


def get_active_profile():
    """Returns (profile_id, profile_dict) or (None, None)."""
    pinned = getattr(_context, "profile", None)
    if pinned is not None:
        return pinned
    snapshot = os.environ.get('HH_BOT_PROFILE')
    if snapshot:
        pinned = json.loads(snapshot)
        return pinned[0], pinned[1]
    data = load_profiles()
    pid = data.get("active")
    if not pid:
        return None, None
    profile = data["profiles"].get(pid)
    if not profile:
        return None, None
    return pid, profile


@_serialized
def set_active_profile(profile_id):
    """Switch active profile. Raises ValueError if profile not found."""
    data = load_profiles()
    if profile_id not in data["profiles"]:
        raise ValueError(f"Profile not found: {profile_id}")
    data["active"] = profile_id
    save_profiles(data)


def list_profiles():
    """Returns sorted list of (profile_id, profile_dict) tuples."""
    data = load_profiles()
    return sorted(data["profiles"].items(), key=lambda x: x[0])


def next_profile_id():
    """Generate next unused profile_N id."""
    data = load_profiles()
    used = set()
    pat = re.compile(r"^profile_(\d+)$")
    for pid in data["profiles"]:
        m = pat.match(pid)
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return f"profile_{n}"


# Фильтры поиска и поведение рассылки. Пустая строка / 0 / False означает
# «не передавать флаг» — утилита возьмёт своё умолчание.
EXPERIENCE_VALUES = ("", "noExperience", "between1And3", "between3And6", "moreThan6")
SCHEDULE_VALUES = ("", "fullDay", "shift", "flexible", "remote", "flyInFlyOut")
WORK_FORMAT_VALUES = ("REMOTE", "HYBRID", "ON_SITE", "FIELD_WORK")
ORDER_BY_VALUES = ("", "publication_time", "salary_desc", "salary_asc", "relevance")
DEFAULT_FILTERS = {
    "experience": "",        # --experience
    "schedule": "",          # --schedule
    "work_format": [],       # --work-format (можно несколько)
    "salary": 0,             # --salary
    "only_with_salary": False,
    "period": 0,             # --period, дней
    "order_by": "",          # --order-by
    "skip_tests": False,     # --skip-tests
    "max_responses": 50,
    "send_email": False,     # --send-email
    "excluded_employer_ids": [],  # --excluded-employer-id
}


def get_filters(profile):
    """Фильтры профиля, дополненные умолчаниями и вычищенные от мусора."""
    f = dict(DEFAULT_FILTERS)
    f["work_format"] = []
    f["excluded_employer_ids"] = []
    stored = (profile or {}).get("filters")
    if isinstance(stored, dict):
        for key in DEFAULT_FILTERS:
            if key in stored and stored[key] is not None:
                f[key] = stored[key]
    if f["experience"] not in EXPERIENCE_VALUES:
        f["experience"] = ""
    if f["schedule"] not in SCHEDULE_VALUES:
        f["schedule"] = ""
    if f["order_by"] not in ORDER_BY_VALUES:
        f["order_by"] = ""
    f["work_format"] = [v for v in (f["work_format"] or []) if v in WORK_FORMAT_VALUES]
    f["excluded_employer_ids"] = [str(v) for v in (f["excluded_employer_ids"] or []) if str(v).isdigit()]
    for key in ("salary", "period", "max_responses"):
        try:
            f[key] = max(0, int(f[key]))
        except (TypeError, ValueError):
            f[key] = 0
    for key in ("only_with_salary", "skip_tests", "send_email"):
        f[key] = bool(f[key])
    return f


@_serialized
def update_filters(profile_id, **fields):
    data = load_profiles()
    if profile_id not in data["profiles"]:
        raise ValueError(f"Profile not found: {profile_id}")
    profile = data["profiles"][profile_id]
    unknown = set(fields) - set(DEFAULT_FILTERS)
    if unknown:
        raise ValueError(f"Unknown filter fields: {', '.join(sorted(unknown))}")
    filters = get_filters(profile)
    filters.update(fields)
    profile["filters"] = filters
    save_profiles(data)
    return filters


@_serialized
def ban_employer(profile_id, employer_id):
    """Добавляет работодателя в чёрный список профиля. True — если добавлен."""
    employer_id = str(employer_id).strip()
    if not employer_id.isdigit():
        raise ValueError("employer_id must be numeric")
    data = load_profiles()
    if profile_id not in data["profiles"]:
        raise ValueError(f"Profile not found: {profile_id}")
    filters = get_filters(data["profiles"][profile_id])
    if employer_id in filters["excluded_employer_ids"]:
        return False
    filters["excluded_employer_ids"].append(employer_id)
    data["profiles"][profile_id]["filters"] = filters
    save_profiles(data)
    return True


def get_ai(profile):
    """AI-настройки профиля, дополненные значениями по умолчанию.

    Профили, созданные до появления блока, читаются без миграции: не хватает
    ключей — берём дефолты."""
    ai = dict(DEFAULT_AI)
    stored = (profile or {}).get("ai")
    if isinstance(stored, dict):
        for key in DEFAULT_AI:
            if key in stored and stored[key] is not None:
                ai[key] = stored[key]
    if ai["filter"] not in AI_FILTER_MODES:
        ai["filter"] = ""
    try:
        ai["rate_limit"] = max(1, int(ai["rate_limit"]))
    except (TypeError, ValueError):
        ai["rate_limit"] = DEFAULT_AI["rate_limit"]
    ai["enabled"] = bool(ai["enabled"])
    ai["dry_run"] = bool(ai["dry_run"])
    return ai


@_serialized
def update_ai(profile_id, **fields):
    """Точечно правит блок ai, не затирая остальные его ключи."""
    data = load_profiles()
    if profile_id not in data["profiles"]:
        raise ValueError(f"Profile not found: {profile_id}")
    profile = data["profiles"][profile_id]
    ai = get_ai(profile)
    unknown = set(fields) - set(DEFAULT_AI)
    if unknown:
        raise ValueError(f"Unknown ai fields: {', '.join(sorted(unknown))}")
    ai.update(fields)
    profile["ai"] = ai
    save_profiles(data)
    return ai


@_serialized
def add_profile(display_name, hh_account, search, letter, area=113, excluded_filter="", resume_id=""):
    """Add a new profile. Returns the new profile_id."""
    validate_account(hh_account)
    data = load_profiles()
    pid = next_profile_id()
    data["profiles"][pid] = {
        "display_name": display_name,
        "hh_account": hh_account,
        "search": search,
        "letter": letter,
        "area": area,
        "excluded_filter": excluded_filter,
        "resume_id": resume_id,
        "reply_message": "",
        "ai": dict(DEFAULT_AI),
        "filters": dict(DEFAULT_FILTERS),
    }
    # If no active profile yet, this one becomes active
    if not data.get("active"):
        data["active"] = pid
    save_profiles(data)
    return pid


@_serialized
def update_profile(profile_id, **fields):
    """Update fields of an existing profile."""
    data = load_profiles()
    if profile_id not in data["profiles"]:
        raise ValueError(f"Profile not found: {profile_id}")
    if "hh_account" in fields:
        validate_account(fields["hh_account"])
    data["profiles"][profile_id].update(fields)
    save_profiles(data)


@_serialized
def remove_profile(profile_id):
    """Remove a profile. If it was active, picks another or clears active."""
    data = load_profiles()
    if profile_id not in data["profiles"]:
        raise ValueError(f"Profile not found: {profile_id}")
    del data["profiles"][profile_id]
    if data.get("active") == profile_id:
        remaining = sorted(data["profiles"].keys())
        data["active"] = remaining[0] if remaining else None
    save_profiles(data)


def get_letter_path(profile):
    """Returns absolute Path to letter file for a profile."""
    letter = profile.get("letter") or DEFAULT_LETTER
    return LETTERS_DIR / Path(letter).name


def list_letters():
    """Returns sorted list of letter file names available."""
    _ensure_files()
    files = sorted(p.name for p in LETTERS_DIR.glob("*.txt"))
    return files


def get_hh_accounts():
    """Returns sorted list of unique hh_account values across all profiles."""
    data = load_profiles()
    accounts = set()
    for p in data["profiles"].values():
        acc = p.get("hh_account")
        if acc:
            accounts.add(acc)
    return sorted(accounts)


def get_profiles_for_account(hh_account):
    """Returns list of (pid, profile) tuples for given hh_account."""
    data = load_profiles()
    return [
        (pid, p) for pid, p in data["profiles"].items()
        if p.get("hh_account") == hh_account
    ]


@_serialized
def migrate_unique_letters():
    """Idempotent: give every profile its own letter file.

    A profile whose letter is shared with another profile, or is the
    default template, gets a dedicated letter_<pid>.txt with the current
    content copied in. After running, every profile has a unique,
    non-default letter file."""
    _ensure_files()
    data = load_profiles()
    profiles = data.get("profiles") or {}

    counts = {}
    for p in profiles.values():
        name = p.get("letter") or DEFAULT_LETTER
        counts[name] = counts.get(name, 0) + 1

    changed = False
    for pid, p in profiles.items():
        cur = p.get("letter") or DEFAULT_LETTER
        if counts.get(cur, 0) <= 1 and cur != DEFAULT_LETTER:
            continue
        new_name = f"letter_{pid}.txt"
        src = LETTERS_DIR / Path(cur).name
        dst = LETTERS_DIR / new_name
        content = ""
        try:
            if src.exists():
                content = src.read_text(encoding="utf-8")
        except Exception:
            content = ""
        try:
            dst.write_text(content)
        except Exception:
            continue
        p["letter"] = new_name
        changed = True

    if changed:
        save_profiles(data)
