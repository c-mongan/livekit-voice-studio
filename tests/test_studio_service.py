import plistlib
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools.studio_service import LABEL, ServiceError, StudioService, make_plist


def result(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


@pytest.fixture
def service(tmp_path):
    root = tmp_path / "checkout"
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").touch()
    (root / "web/dist").mkdir(parents=True)
    (root / "web/dist/index.html").touch()
    return StudioService(root, tmp_path / "service", uid=501)


def test_plist_has_explicit_ownership_no_restart_or_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-for-the-plist")
    value = make_plist(tmp_path, tmp_path / "service")
    assert value["Label"] == LABEL
    assert value["KeepAlive"] is False
    assert value["RunAtLoad"] is True
    assert value["ExitTimeOut"] >= 175
    assert value["AbandonProcessGroup"] is False
    assert value["WorkingDirectory"] == str(tmp_path)
    assert "not-for-the-plist" not in plistlib.dumps(value).decode()
    assert "studio" in " ".join(value["ProgramArguments"])
    assert set(value["EnvironmentVariables"]) == {"HOME", "PATH"}


def test_stopped_is_distinct_from_failed_inspection(service):
    service.run = Mock(return_value=result(113, err="Could not find service"))
    assert service.job()["loaded"] is False
    service.run = Mock(return_value=result(1, err="Permission denied"))
    with pytest.raises(ServiceError, match="inspect"):
        service.job()


def test_start_refuses_unmanaged_listener(service):
    service.run = Mock(return_value=result(113, err="Could not find service"))
    service.listening = Mock(return_value=True)
    with pytest.raises(ServiceError, match="outside"):
        service.start()
    assert all("bootstrap" not in call.args[0] for call in service.run.call_args_list)


def test_stop_does_not_claim_an_unmanaged_listener_stopped(service):
    service.run = Mock(return_value=result(113, err="Could not find service"))
    service.listening = Mock(return_value=True)
    with pytest.raises(ServiceError, match="outside"):
        service.stop()
    assert not any("kill" in c.args[0] for c in service.run.call_args_list)


def test_start_writes_private_plist_and_bootstraps_once(service):
    def run(args):
        if "print" in args:
            return result(113, err="Could not find service")
        return result()

    service.run = Mock(side_effect=run)
    service.listening = Mock(return_value=False)
    service.wait_ready = Mock(return_value={"state": "running", "ready": True})
    assert service.start()["ready"]
    assert sum("bootstrap" in c.args[0] for c in service.run.call_args_list) == 1
    assert service.plist.stat().st_mode & 0o777 == 0o600
    assert service.directory.stat().st_mode & 0o777 == 0o700
    assert service.directory.parent.name != "LaunchAgents"


def test_running_start_is_idempotent(service):
    service.write_config()
    service.run = Mock(return_value=result(out="state = running\npid = 123\n"))
    service.status = Mock(return_value={"state": "running", "ready": True})
    assert service.start()["ready"]
    assert not any("bootstrap" in c.args[0] for c in service.run.call_args_list)


def test_stop_only_signals_owned_label_and_returns_without_waiting(service):
    service.write_config()
    service.run = Mock(return_value=result(out="state = running\npid = 123\n"))
    assert service.stop()["state"] == "stopping"
    assert service.run.call_args.args[0] == ["launchctl", "kill", "SIGTERM", "gui/501/" + LABEL]
    assert not any("killall" in c.args[0] for c in service.run.call_args_list)
    assert service.stopping.exists()


def test_other_checkout_cannot_replace_or_stop_service(service, tmp_path):
    service.write_config()
    other = StudioService(tmp_path / "other", service.directory, uid=501)
    other.run = Mock(return_value=result(out="pid = 123"))
    with pytest.raises(ServiceError, match="another checkout"):
        other.stop()
    with pytest.raises(ServiceError, match="another checkout"):
        other.start()
    assert not other.run.called


def test_start_during_drain_is_rejected(service):
    service.write_config()
    service.stopping.touch()
    service.run = Mock(return_value=result(out="state = running\npid = 123"))
    with pytest.raises(ServiceError, match="stopping"):
        service.start()


def test_crash_does_not_automatically_clear_uncertainty(service):
    service.write_config()
    service.run = Mock(return_value=result(out="state = not running\nlast exit code = 1"))
    service.listening = Mock(return_value=False)
    state = service.status()
    assert state["state"] == "failed"
    assert state["exitCode"] == 1
    assert (
        "--confirm-backend-restarted"
        not in plistlib.loads(service.plist.read_bytes())["ProgramArguments"]
    )


def test_missing_environment_is_actionable(service):
    Path(service.root / ".venv/bin/python").unlink()
    service.run = Mock(return_value=result(113, err="Could not find service"))
    service.listening = Mock(return_value=False)
    with pytest.raises(ServiceError, match="dependencies"):
        service.start()
