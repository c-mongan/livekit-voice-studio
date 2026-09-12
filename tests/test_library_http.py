import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_studio_library import recording

from examples.backend_lease import BackendLease
from examples.studio import Studio, create_app
from examples.studio_library import StudioLibrary

HEADERS = {"Host": "127.0.0.1:8765", "X-Voicebox-Studio": "1"}


@pytest.fixture
async def library_client(tmp_path):
    lease = BackendLease(tmp_path / "locks", "http://127.0.0.1:17493")
    studio = Studio(lease)
    studio.library = StudioLibrary(tmp_path / "library")
    async with TestClient(TestServer(create_app(studio))) as client:
        yield client, studio


def form():
    data = aiohttp.FormData()
    data.add_field("name", "Browser voice")
    data.add_field("transcript", "These are the exact spoken words.")
    data.add_field("authorized", "true")
    data.add_field("audio", recording(), filename="reference.wav", content_type="audio/wav")
    return data


async def test_create_list_preview_rename_and_delete(library_client):
    client, studio = library_client
    response = await client.post("/api/voices", data=form(), headers=HEADERS)
    assert response.status == 201
    voice = (await response.json())["voice"]
    assert voice["name"] == "Browser voice"
    identifier = voice["id"]
    response = await client.get("/api/voices", headers=HEADERS)
    assert (await response.json())["voices"][0]["id"] == identifier
    response = await client.get(f"/api/voices/{identifier}/audio", headers=HEADERS)
    assert response.status == 200 and await response.read() == recording()
    assert response.headers["Cache-Control"] == "no-store"
    response = await client.patch(
        f"/api/voices/{identifier}", json={"name": "Renamed"}, headers=HEADERS
    )
    assert response.status == 200
    response = await client.delete(
        f"/api/voices/{identifier}", json={"confirm": False}, headers=HEADERS
    )
    assert response.status == 400
    response = await client.delete(
        f"/api/voices/{identifier}", json={"confirm": True}, headers=HEADERS
    )
    assert response.status == 200 and studio.library.list_voices() == []


async def test_enrollment_blocked_during_session(library_client):
    client, studio = library_client
    studio.phase = "active"
    response = await client.post("/api/voices", data=form(), headers=HEADERS)
    assert response.status == 409
    assert studio.library.list_voices() == []


async def test_multipart_without_csrf_header_rejected(library_client):
    client, _ = library_client
    response = await client.post("/api/voices", data=form(), headers={"Host": "127.0.0.1:8765"})
    assert response.status == 403


async def test_settings_are_nonsecret_and_idle_only(library_client, monkeypatch):
    client, studio = library_client
    monkeypatch.setattr(studio.library, "apply_environment", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "private-credential-must-not-leak")
    response = await client.get("/api/settings", headers=HEADERS)
    data = await response.json()
    assert response.status == 200
    assert data["llmProvider"] == "copilot"
    assert "providers" in data
    assert "private-credential-must-not-leak" not in json.dumps(data)
    assert "apiKey" not in data
    studio.metrics["llmFirstTokenSeconds"] = 1.5
    response = await client.post("/api/settings", json={"sttProvider": "azure"}, headers=HEADERS)
    assert response.status == 200
    assert all(value is None for value in studio.metrics.values())
    studio.phase = "draining"
    response = await client.post("/api/settings", json={"sttProvider": "openai"}, headers=HEADERS)
    assert response.status == 409
    assert studio.library.settings()["sttProvider"] == "azure"


async def test_switching_away_from_local_stt_releases_owned_runtime(library_client, monkeypatch):
    client, studio = library_client
    monkeypatch.setattr(studio.library, "apply_environment", lambda: None)
    service = SimpleNamespace(close=AsyncMock())
    studio.recognizer_service = service
    response = await client.post("/api/settings", json={"sttProvider": "azure"}, headers=HEADERS)
    assert response.status == 200
    service.close.assert_awaited_once()
