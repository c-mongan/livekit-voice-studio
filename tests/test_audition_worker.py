import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples import audition_worker


class Stream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.sent:
            raise StopAsyncIteration
        self.sent = True
        return SimpleNamespace(frame=SimpleNamespace(data=b"\x01\x00" * 2000))

    sent = False
    closed = False


async def test_worker_prepares_same_provider_and_transfers_bounded_pcm(monkeypatch):
    reports = []
    stream = Stream()
    provider = SimpleNamespace(prepare=AsyncMock(), synthesize=lambda text: stream)
    monkeypatch.setattr(
        audition_worker, "report", lambda name, **values: reports.append((name, values))
    )
    await audition_worker.generate(provider, "A test sentence.")
    provider.prepare.assert_awaited_once()
    assert stream.closed
    assert [name for name, _ in reports[:2]] == ["model_loaded", "ready"]
    chunks = [values["pcm"] for name, values in reports if name == "audition_audio"]
    assert all(len(chunk) <= 2560 for chunk in chunks)
    assert b"".join(base64.b64decode(chunk) for chunk in chunks) == b"\x01\x00" * 2000
    assert "A test sentence." not in str(reports)


async def test_worker_propagates_provider_failure_without_generated_fallback(monkeypatch):
    reports = []
    provider = SimpleNamespace(prepare=AsyncMock(side_effect=RuntimeError("fixture")))
    monkeypatch.setattr(audition_worker, "report", lambda name, **values: reports.append(name))
    with pytest.raises(RuntimeError):
        await audition_worker.generate(provider, "Words.")
    assert reports == []
