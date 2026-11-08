"""Compatibility helpers for hh-applicant-tool 2.5.x profiles."""
import json
from pathlib import Path


def token_path(config_dir, account):
    root = Path(config_dir)
    validate_account(account)
    paths = [root / account / 'token.json', root / account / 'config.json']
    if account == 'main':
        paths += [root / 'token.json', root / 'config.json']
    for path in paths:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                token = data.get('token') or data
                if isinstance(token, dict) and token.get('access_token'):
                    return path
            except (OSError, ValueError, TypeError):
                continue
    return None


def read_access_token(config_dir, account):
    path = token_path(config_dir, account)
    if path is None:
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    return (data.get('token') or data).get('access_token')


def profile_config_path(config_dir, account):
    root = Path(config_dir)
    validate_account(account)
    paths = [root / account / 'config.toml', root / account / 'config.json']
    if account == 'main':
        paths += [root / 'config.toml', root / 'config.json']
    for p in paths:
        if p.is_file():
            return p
    return root / account / 'config.toml'


def read_profile_config(config_dir, account):
    path = profile_config_path(config_dir, account)
    if not path.is_file():
        return {}
    if path.suffix == '.toml':
        import tomllib
        with path.open('rb') as f:
            return tomllib.load(f)
    return json.loads(path.read_text(encoding='utf-8'))


def validate_account(account):
    import re
    if not isinstance(account, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', account):
        raise ValueError('Invalid HH account identifier')
    return account


def cli_args(account, *args):
    import os
    import sys
    validate_account(account)
    return [sys.executable, str(Path(__file__).with_name('hh_cli.py')),
            '--config-dir', os.environ.get('CONFIG_DIR', '/app/config'),
            '--profile', account, *args]


def tool_log_path(config_dir, account):
    """The default main account may keep its log directly in CONFIG_DIR."""
    validate_account(account)
    root=Path(config_dir)
    candidate=root/account/'log.txt'
    legacy=root/'log.txt'
    if account=='main' and not candidate.exists() and legacy.exists():
        return legacy
    return candidate
