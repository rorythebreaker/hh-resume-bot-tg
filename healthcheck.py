"""Check the process inside its own PID namespace, without contacting HH/Telegram."""
import sys
from pathlib import Path

def healthy(script):
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == __import__('os').getpid(): continue
        try:
            args = (proc / 'cmdline').read_bytes().split(b'\0')
            if any(a.endswith(('/' + script).encode()) or a == script.encode() for a in args): return True
        except OSError: pass
    return False

if __name__ == '__main__':
    sys.exit(0 if healthy(sys.argv[1]) else 1)
