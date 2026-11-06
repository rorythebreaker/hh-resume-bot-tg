#!/usr/bin/env python3
"""Non-destructive migration of legacy config.json/token to 2.5.x files.

Run with --write after inspecting --dry-run output. Never deletes old files.
"""
import argparse
import json
from pathlib import Path
from storage import atomic_write


def migrate(folder, write=False):
    source = folder / 'config.json'
    if not source.is_file():
        return 'skip: no config.json'
    old = json.loads(source.read_text(encoding='utf-8'))
    token = old.get('token') or ({'access_token': old.get('access_token'), 'refresh_token': old.get('refresh_token')} if old.get('access_token') else None)
    if not isinstance(token, dict) or not token.get('access_token'):
        token = None
    config = {k: v for k, v in old.items() if k not in ('token', 'access_token', 'refresh_token') and v is not None}
    sections = {'openai_cover_letter': 'cover_letter', 'openai_vacancy_filter': 'vacancy_filter', 'openai_captcha': 'captcha'}
    ai = dict(config.get('openai') or {})
    default = {k: ai.pop(k) for k in list(ai) if not isinstance(ai[k], dict)}
    if default: ai.setdefault('default', {}).update(default)
    for old_key, new_key in sections.items():
        if isinstance(config.get(old_key), dict): ai[new_key] = config.pop(old_key)
    for section in ai.values():
        if isinstance(section, dict) and isinstance(section.get('base_url'), str):
            section['base_url'] = section['base_url'].removesuffix('/chat/completions')
    if ai: config['openai'] = ai
    # Original config.json is retained for rollback.
    if not isinstance(config.get('general'), dict):
        config['general'] = {}
    outputs = []
    if token:
        outputs.append((folder / 'token.json', json.dumps({'token': token}, ensure_ascii=False, indent=2) + '\n'))
    if config:
        try:
            import tomli_w
        except ImportError as e:
            raise RuntimeError('Install tomli-w to migrate config: pip install tomli-w') from e
        outputs.append((folder / 'config.toml', tomli_w.dumps(config)))
    conflicts = [str(p) for p, _ in outputs if p.exists()]
    if conflicts:
        return 'skip: existing target(s): ' + ', '.join(conflicts)
    if write:
        for path, content in outputs:
            atomic_write(path, content)
            path.chmod(0o600)
    return ('written: ' if write else 'would create: ') + ', '.join(str(p) for p, _ in outputs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config-dir', default='/app/config')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()
    root = Path(args.config_dir)
    for folder in [root, *sorted(p for p in root.iterdir() if p.is_dir())] if root.exists() else []:
        print(folder, migrate(folder, args.write))


if __name__ == '__main__':
    main()
