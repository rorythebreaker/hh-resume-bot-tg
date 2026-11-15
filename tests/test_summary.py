"""Regression: summary must send even when attempt metrics are absent."""
import os
import runpy
from pathlib import Path


def test_summary_without_core_metrics_sends_message(tmp_path, monkeypatch):
    output = tmp_path / "last-run.log"
    output.write_text("Отправлено: 2\nSTOP_REASON: completed\nPASSES: 1\n", encoding="utf-8")
    monkeypatch.setenv("LOG_FILE", str(output))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("TG_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TG_CHAT_ID", "123")
    monkeypatch.setenv("RC", "0")
    monkeypatch.setenv("BOT_TRANSPORT", "telegram")
    sent = []

    class Response:
        def read(self):
            return b'{"ok":true}'

    monkeypatch.setattr("urllib.request.urlopen", lambda url, data, timeout: (sent.append(data), Response())[1])
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "_summary.py"), run_name="__main__")
    assert len(sent) == 1
    assert b"%D0%A1%D0%B2%D0%BE%D0%B4%D0%BA%D0%B0" in sent[0]
    assert b"%D0%9E%D1%82%D0%BF%D1%80%D0%B0%D0%B2%D0%BB%D0%B5%D0%BD%D0%BE" in sent[0]


def test_summary_with_core_metrics_displays_processed(tmp_path, monkeypatch):
    output = tmp_path / "last-run.log"
    output.write_text('CORE_METRICS: {"found": 42, "sent": 2, "skipped": 40, "errors": 0}\nОтправлено: 2\n', encoding="utf-8")
    monkeypatch.setenv("LOG_FILE", str(output))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("TG_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TG_CHAT_ID", "123")
    monkeypatch.setenv("RC", "0")
    monkeypatch.setenv("BOT_TRANSPORT", "telegram")
    sent = []
    class Response:
        def read(self):
            return b'{"ok":true}'
    monkeypatch.setattr("urllib.request.urlopen", lambda url, data, timeout: (sent.append(data), Response())[1])
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "_summary.py"), run_name="__main__")
    assert len(sent) == 1
    assert b"42" in sent[0]
