"""Persistent hourly schedule, shared by cron and Telegram (Moscow time)."""
import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from storage import atomic_write

def schedule_path():
    return Path(os.environ.get('CONFIG_DIR', '/app/config')) / 'schedule.json'

def read_hours():
    path = schedule_path()
    return json.loads(path.read_text())['hours'] if path.exists() else list(range(8,22))

def parse_hours(text):
    hours = set()
    for part in text.split(','):
        values = part.strip().split('-')
        if len(values) == 1: hours.add(int(values[0]))
        elif len(values) == 2: hours.update(range(int(values[0]), int(values[1])+1))
        else: raise ValueError('Use 8-21 or 8,12,18')
    if not hours or min(hours)<0 or max(hours)>23: raise ValueError('Hours must be within 0-23')
    return sorted(hours)

def save_hours(text):
    hours = parse_hours(text)
    atomic_write(schedule_path(), json.dumps({'hours':hours})+'\n')
    return hours

def due():
    return datetime.now(timezone(timedelta(hours=3))).hour in read_hours()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--hours', help='Moscow hours, e.g. 8-21 or 8,12,18')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.hours: save_hours(args.hours)
    if args.check: raise SystemExit(0 if due() else 1)
    print(','.join(map(str, read_hours())))
