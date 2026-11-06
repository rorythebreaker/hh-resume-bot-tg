"""Inspect versioned source files before publishing; print paths, never secrets."""
import re
import subprocess
from pathlib import Path

def verify():
    names=subprocess.check_output(['git','ls-files','-z']).decode('utf-8').split('\0')
    forbidden=[]
    patterns=[re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'), re.compile(rb'\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b'), re.compile(rb'\bsk-[A-Za-z0-9_-]{24,}\b')]
    for name in filter(None,names):
        path=Path(name)
        if not path.exists(): continue
        if name=='.env' or name=='profiles.json' or name.startswith(('config/','.venv/')) or path.suffix in ('.zip','.enc'):
            forbidden.append(name); continue
        data=path.read_bytes()
        if any(pattern.search(data) for pattern in patterns): forbidden.append(name)
    if forbidden:
        print('Publication check failed: '+', '.join(forbidden)); return 1
    print('Publication check passed: no runtime files or recognizable credentials in tracked sources')
    return 0
if __name__=='__main__': raise SystemExit(verify())
