import asyncio
import io
import json
import tempfile
import threading
import wave
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from tools import read_aloud
from tools.read_aloud import ReadAloudError, decode_wav, notification_text, spoken_excerpt


def test_spoken_excerpt_is_deterministic_and_does_not_narrate_code_or_urls():
    text = (
        "# Result\n\n**Done.** See [the guide](https://example.test).\n"
        "```sh\nrm -rf example\n```\nNext step."
    )
    result = spoken_excerpt(text)
    assert result == "Result Done. See the guide. Next step."
    assert "rm" not in result
    assert "http" not in result


def test_long_reply_is_explicitly_an_excerpt():
    result = spoken_excerpt("A useful sentence. " * 100)
    assert len(result) <= 300
    assert result.endswith("The reply continues in your agent.")


@pytest.mark.parametrize("text", ["", "```py\nprint('code only')\n```", "x" * 65537])
def test_empty_or_unbounded_input_is_rejected(text):
    with pytest.raises(ReadAloudError):
        spoken_excerpt(text)


def test_notification_reads_only_completed_assistant_text():
    payload = {
        "type": "agent-turn-complete",
        "last-assistant-message": "The work is complete.",
        "cwd": "/must/not/be/read",
        "input-messages": ["private user input must not be spoken"],
    }
    assert notification_text(json.dumps(payload)) == "The work is complete."
    assert notification_text(json.dumps({"type": "unrelated-event"})) is None


@pytest.mark.parametrize("payload", ["invalid", "[]", '{"type":"agent-turn-complete"}'])
def test_invalid_completed_notifications_fail_explicitly(payload):
    with pytest.raises(ReadAloudError):
        notification_text(payload)


def test_wav_decoder_preserves_pcm_and_enforces_contract():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x01\x00" * 480)
    assert decode_wav(output.getvalue()) == b"\x01\x00" * 480
    with pytest.raises(ReadAloudError):
        decode_wav(b"not a wav")


def test_playback_stops_without_waiting_for_remaining_audio(monkeypatch):
    from tools import read_aloud

    stop = threading.Event()
    writes = []

    class Output:
        def __init__(self, **kwargs):
            assert kwargs["samplerate"] == 24000

        def __enter__(self):
            return self

        def write(self, data):
            writes.append(data)
            stop.set()

        def abort(self):
            writes.append("aborted")

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(read_aloud, "output_stream", Output)
    read_aloud.play_pcm(b"\x01\x00" * 4800, stop)
    assert len(writes) == 2
    assert writes[-1] == "aborted"


@pytest.fixture
async def narration_api():
    calls = []
    stop = asyncio.Event()
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 480)

    async def endpoint(request):
        calls.append(request.path)
        assert request.headers["X-Voicebox-Studio"] == "1"
        if request.path == "/api/settings":
            return web.json_response({"voiceId": "a" * 32})
        if request.path == "/api/audition":
            body = await request.json()
            assert body["holdPlayback"] is True
            return web.json_response({"auditionId": "private-control-handle"})
        if request.path.endswith("/audio"):
            return web.Response(body=output.getvalue(), content_type="audio/wav")
        if request.path.endswith("/end"):
            return web.json_response({"phase": "idle"})
        return web.json_response({"state": "ready", "phase": "active"})

    app = web.Application()
    app.router.add_route("*", "/api/{tail:.*}", endpoint)
    async with TestServer(app) as server:
        yield str(server.make_url("")).rstrip("/"), calls, stop


async def test_narration_uses_only_local_api_and_releases_after_playback(narration_api):
    url, calls, stop = narration_api
    played = []
    await read_aloud.speak(
        "Already generated.", stop, base_url=url, player=lambda pcm, _: played.append(pcm)
    )
    assert played == [b"\x01\x00" * 480]
    assert calls.count("/api/audition") == 1
    assert calls.count("/api/audition/audio") == 1
    assert calls[-1] == "/api/audition/end"


async def test_playback_device_failure_still_releases_admission(narration_api):
    url, calls, stop = narration_api

    def failed_player(pcm, stopped):
        raise ReadAloudError("Audio device unavailable.")

    with pytest.raises(ReadAloudError, match="Audio device"):
        await read_aloud.speak("Words.", stop, base_url=url, player=failed_player)
    assert calls[-1] == "/api/audition/end"


async def test_mute_prevents_any_generation(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    (tmp_path / "muted").touch()
    mock = AsyncMock()
    monkeypatch.setattr(read_aloud, "speak", mock)
    await read_aloud.run_narration("Words.", tmp_path)
    mock.assert_not_called()


async def test_private_stop_socket_cancels_current_narration(monkeypatch):
    stopped = asyncio.Event()
    ready = asyncio.Event()

    async def wait_for_stop(text, stop):
        ready.set()
        await stop.wait()
        stopped.set()

    monkeypatch.setattr(read_aloud, "speak", wait_for_stop)
    with tempfile.TemporaryDirectory(prefix="voicebox-test-", dir="/tmp") as short:
        directory = Path(short)
        task = asyncio.create_task(read_aloud.run_narration("Words.", directory))
        async with asyncio.timeout(2):
            await ready.wait()
            assert await read_aloud.stop_speaking(directory)
            await task
        assert stopped.is_set()
        assert not (directory / "read-aloud.sock").exists()


def test_codex_nullable_reply_is_not_spoken():
    assert (
        notification_text(
            json.dumps({"type": "agent-turn-complete", "last-assistant-message": None})
        )
        is None
    )


def test_notification_receipts_are_bounded_private_and_prevent_replay(tmp_path):
    payload = json.dumps(
        {
            "type": "agent-turn-complete",
            "thread-id": "thread-a",
            "turn-id": "turn-1",
            "last-assistant-message": "Private example text must not be stored.",
        }
    )
    assert read_aloud.reserve_notification(payload, tmp_path) is True
    assert read_aloud.reserve_notification(payload, tmp_path) is False
    ledger = tmp_path / "notification-receipts.json"
    assert "Private example" not in ledger.read_text()
    assert "thread-a" not in ledger.read_text()
    assert ledger.stat().st_mode & 0o777 == 0o600
    for i in range(70):
        read_aloud.reserve_notification(
            json.dumps({"thread-id": "thread-a", "turn-id": str(i)}), tmp_path
        )
    assert len(json.loads(ledger.read_text())) == 64


def test_corrupt_receipts_fail_closed_without_reset(tmp_path):
    (tmp_path / "notification-receipts.json").write_text("not valid")
    with pytest.raises(ReadAloudError, match="invalid"):
        read_aloud.reserve_notification(
            json.dumps({"thread-id": "thread-a", "turn-id": "turn-1"}), tmp_path
        )
    assert (tmp_path / "notification-receipts.json").read_text() == "not valid"
