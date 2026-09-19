import json
import socket

import pytest
from aiohttp.test_utils import TestClient, TestServer

from examples import studio


async def test_setup_endpoint_uses_fixed_text_without_starting_services(monkeypatch, tmp_path):
    from tools import studio_doctor

    monkeypatch.setattr(
        studio_doctor,
        "run_checks",
        lambda root: {
            "version": 1,
            "checks": [
                {
                    "id": "livekit",
                    "status": "missing",
                    "message": "/private/secret",
                    "action": "key-secret",
                },
                {
                    "id": "/private/unknown",
                    "status": "pass",
                    "message": "secret",
                    "action": "secret",
                },
            ],
        },
    )
    app = studio.create_app(object())
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/setup", headers={"Host": "127.0.0.1:8765"})
        assert response.status == 200
        body = await response.json()
        assert body["version"] == 1
        check = next(item for item in body["checks"] if item["id"] == "livekit")
        assert check["status"] == "missing"
        assert set(check) == {"id", "status", "message", "action"}
        assert "secret" not in json.dumps(body)
        assert "/private/" not in json.dumps(body)
        assert response.headers["Cache-Control"] == "no-store"


def test_setup_real_offline_check_never_creates_library_or_connects(tmp_path, monkeypatch):
    from examples.studio_setup import setup_report

    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("network access"))
    monkeypatch.setenv("VOICEBOX_LIBRARY_DIR", str(tmp_path / "private-library"))
    report = setup_report(tmp_path)
    assert report["version"] == 1
    assert not (tmp_path / "private-library").exists()
    assert str(tmp_path) not in json.dumps(report)
    assert (
        next(c for c in report["checks"] if c["id"] == "authentication")["status"] == "unverified"
    )


def test_setup_does_not_disclose_inspection_exception(tmp_path, monkeypatch):
    from examples.studio_setup import setup_report
    from tools import studio_doctor

    def fail(root):
        raise OSError("/private/secret")

    monkeypatch.setattr(studio_doctor, "run_checks", fail)
    result = setup_report(tmp_path)
    assert all(c["status"] == "unverified" for c in result["checks"])
    assert "secret" not in json.dumps(result)


async def test_slow_setup_is_single_flight_bounded_and_recovers(monkeypatch, tmp_path):
    import asyncio
    import threading
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from examples.studio_setup import SetupChecks
    from tools import studio_doctor

    release = threading.Event()
    calls = []

    def inspect(root):
        calls.append(root)
        release.wait(2)
        return {"version": 1, "checks": []}

    monkeypatch.setattr(studio_doctor, "run_checks", inspect)
    checks = SetupChecks(tmp_path, timeout=0.02)
    app = studio.create_app(SimpleNamespace(status=AsyncMock(return_value={"phase": "idle"})))
    app[studio.SETUP_CHECKS] = checks
    headers = {"Host": "127.0.0.1:8765"}
    try:
        async with TestClient(TestServer(app)) as client:
            responses = await asyncio.gather(
                *(client.get("/api/setup", headers=headers) for _ in range(4))
            )
            assert all(response.status == 503 for response in responses)
            assert len(calls) == 1
            body = await responses[0].json()
            assert "local files" in body["error"]
            assert "secret" not in json.dumps(body)
            assert responses[0].headers["Cache-Control"] == "no-store"
            again = await client.get("/api/setup", headers=headers)
            assert again.status == 503
            assert len(calls) == 1
            status = await asyncio.wait_for(client.get("/api/status", headers=headers), 0.5)
            assert status.status == 200
            release.set()
            # Wait for the pending real thread to complete, then request fresh data.
            await checks.get()
            fresh = await client.get("/api/setup", headers=headers)
            assert fresh.status == 200
            assert len(calls) >= 2
    finally:
        release.set()


async def test_completed_setup_reports_are_not_cached(monkeypatch, tmp_path):
    from examples.studio_setup import SetupChecks
    from tools import studio_doctor

    outcomes = iter(("missing", "pass"))
    monkeypatch.setattr(
        studio_doctor,
        "run_checks",
        lambda root: {
            "version": 1,
            "checks": [{"id": "livekit", "status": next(outcomes), "message": "", "action": ""}],
        },
    )
    checks = SetupChecks(tmp_path)
    first = await checks.get()
    second = await checks.get()
    assert next(c for c in first["checks"] if c["id"] == "livekit")["status"] == "missing"
    assert next(c for c in second["checks"] if c["id"] == "livekit")["status"] == "pass"
