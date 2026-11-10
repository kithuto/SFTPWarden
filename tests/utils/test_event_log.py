import json

from sftpwarden.config import LoggingConfig
from sftpwarden.utils.event_log import service_event


def test_service_log_threshold_and_json_records(capsys):
    settings = LoggingConfig(level="error", format="json")
    service_event(settings, "info", "Completed.")
    assert capsys.readouterr().err == ""
    service_event(settings, "error", "Retry pending.", context="remote")
    result = capsys.readouterr()
    assert result.out == ""
    event = json.loads(result.err)
    assert event["level"] == "error"
    assert event["context"] == "remote"
    assert event["message"] == "Retry pending."


def test_service_log_text_format_uses_selected_level(capsys):
    service_event(LoggingConfig(level="debug", format="text"), "debug", "Polling.")
    assert capsys.readouterr().err == "DEBUG: Polling.\n"
