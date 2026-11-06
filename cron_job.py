"""Load allowlisted container environment for cron and exec a job."""
import json
import os
import sys
from pathlib import Path
if __name__ == '__main__':
    env_file = Path('/app/config/.cron_environment.json')
    if env_file.exists(): os.environ.update(json.loads(env_file.read_text()))
    os.execvp(sys.argv[1], sys.argv[1:])
