import pytest

from examples import studio_worker
from examples.startup_progress import STARTUP_MESSAGES


def test_worker_reports_only_declared_startup_stages(monkeypatch):
    events = []
    monkeypatch.setattr(studio_worker, "report", lambda event, **data: events.append((event, data)))
    for stage in STARTUP_MESSAGES:
        assert studio_worker.report_startup(stage) == stage
    assert len(events) == len(STARTUP_MESSAGES)
    assert all(event == "startup" for event, _ in events)
    with pytest.raises(ValueError, match="Unknown startup stage"):
        studio_worker.report_startup("private provider error")
    assert len(events) == len(STARTUP_MESSAGES)
