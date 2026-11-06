import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
import profiles_lib

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DIR', str(tmp_path))
    monkeypatch.setenv('CONFIG_DIR', str(tmp_path / 'config'))
    for key, value in {'APP_DIR':tmp_path, 'PROFILES_FILE':tmp_path/'profiles.json', 'PROFILES_EXAMPLE':tmp_path/'absent.json', 'LETTERS_DIR':tmp_path/'letters'}.items():
        monkeypatch.setattr(profiles_lib, key, value)
    (tmp_path/'letters').mkdir()
    (tmp_path/'letters'/'letter_default.txt').write_text('Hello %(vacancy_name)s', encoding='utf-8')
    (tmp_path/'config').mkdir()
    pid = profiles_lib.add_profile('Test', 'main', '(python OR devops)', 'letter_default.txt', resume_id='resume123')
    return tmp_path, pid
