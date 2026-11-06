"""Bounded, atomic run history, without tokens or full API payloads."""
import json
import os
from pathlib import Path
from storage import transaction, atomic_write

def path():
    return Path(os.environ.get('CONFIG_DIR','/app/config')) / 'run_history.json'

def load():
    target = path()
    return json.loads(target.read_text()) if target.exists() else []

def append(record):
    target=path()
    with transaction(target.with_suffix('.lock')):
        records=load()
        records.append(record)
        atomic_write(target, json.dumps(records[-100:], ensure_ascii=False, indent=2)+'\n')
