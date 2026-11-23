"""Regression: summary must send even when attempt metrics are absent."""
import json
import runpy
from pathlib import Path
from urllib.parse import parse_qs


def test_summary_without_core_metrics_sends_message(tmp_path, monkeypatch):
    output = tmp_path / "last-run.log"
    output.write_text("Отправлено: 2\nSTOP_REASON: completed\nPASSES: 1\nSKIPPED: 3\n", encoding="utf-8")
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
    message = parse_qs(sent[0].decode())["text"][0]
    assert "Пропущено: <b>3</b>" in message
    assert "Причины пропусков" not in message


def test_summary_with_core_metrics_displays_skip_breakdown(tmp_path, monkeypatch):
    output = tmp_path / "last-run.log"
    metrics = {
        "found": 508, "sent": 6, "skipped": 502, "errors": 0, "pre_skipped": 127,
        "skip_reasons": {
            "geo_nonremote_outside_city": 369, "test": 22,
            "relations": 105, "geo_unknown_work_format": 1,
        },
    }
    output.write_text(
        f"CORE_METRICS: {json.dumps(metrics)}\nОтправлено: 6\nSKIPPED: 502\nERRORS: 0\n",
        encoding="utf-8",
    )
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
    message = parse_qs(sent[0].decode())["text"][0]
    assert "Обработано вакансий: 508" in message
    assert "Отправлено: <b>6</b>" in message
    assert "Пропущено: <b>502</b>" in message
    assert "Ошибок: 0" in message
    assert "Вне домашнего города, формат не только удалённый: 369" in message
    assert "Уже есть статус на HH (например, отклик или отказ): 105" in message
    assert "Вакансии с тестовым заданием: 22" in message
    assert "Вне домашнего города, формат работы не указан: 1" in message
    assert "Причина не записана в лог: 5" in message
    assert "Из общего числа пропущены до основной обработки: 127" in message


def test_summary_explains_incomplete_discovery(tmp_path, monkeypatch):
    output = tmp_path / "last-run.log"
    metrics = {
        "unique": 41, "completed": 1, "pending": 2, "requests": 3,
        "requests_total": 6, "overflow": [], "issue": {"code": "api_error", "status": 429},
    }
    output.write_text(
        f"DISCOVERY_METRICS: {json.dumps(metrics)}\nSTOP_REASON: discovery_incomplete\nОтправлено: 0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOG_FILE", str(output))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("TG_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TG_CHAT_ID", "123")
    monkeypatch.setenv("RC", "2")
    monkeypatch.setenv("BOT_TRANSPORT", "telegram")
    messages = []

    class Response:
        def read(self):
            return b'{"ok":true}'

    def send(url, data, timeout):
        messages.append(parse_qs(data.decode())["text"][0])
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", send)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "_summary.py"), run_name="__main__")
    assert len(messages) == 1
    assert "Уникальных вакансий в завершённых сегментах: 41" in messages[0]
    assert "завершено 1, осталось 2" in messages[0]
    assert "HTTP 429" in messages[0]
    assert "отклики не отправлялись" in messages[0]
    assert "Обработано вакансий" not in messages[0]
