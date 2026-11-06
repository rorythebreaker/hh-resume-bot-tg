#!/bin/bash
# Reads active profile from /app/profiles.json and runs apply-vacancies.
# Argument building is done in Python (via run-apply.py) to avoid bash
# word-splitting issues with search queries containing spaces/quotes/operators.
set -u

exec python3 /app/run-apply.py
