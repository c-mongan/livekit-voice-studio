import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from examples.nemotron_service import NemotronService


async def test_service_starts_only_installed_cpu_runtime(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    binary = runtime / "build/bin/nemo-speech"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fake")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"fake")
    process = Mock(returncode=None)
    process.wait = AsyncMock(return_value=0)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("NEMOTRON_SERVER_BINARY", str(binary))
    monkeypatch.setenv("NEMOTRON_MODEL_PATH", str(model))
    monkeypatch.setenv("NEMO_SPEECH_ASR_MODEL", "unapproved")
    service = NemotronService()
    monkeypatch.setattr(service, "_port_is_busy", lambda: False)
    monkeypatch.setattr(service, "_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_verify", lambda _: None)
    await service.start()
    args = spawn.call_args.args
    assert "--device" in args and args[args.index("--device") + 1] == "cpu"
    assert "--host" in args and args[args.index("--host") + 1] == "127.0.0.1"
    assert not any(key.startswith("NEMO_SPEECH_") for key in spawn.call_args.kwargs["env"])
    await service.start()
    spawn.assert_awaited_once()
    await service.close()
    process.terminate.assert_called_once()


async def test_service_never_steals_occupied_port(monkeypatch):
    service = NemotronService()
    monkeypatch.setattr(service, "_port_is_busy", lambda: True)
    with pytest.raises(RuntimeError, match="already in use"):
        await service.start()
    await service.close()


async def test_missing_assets_do_not_download(monkeypatch):
    monkeypatch.setenv("NEMOTRON_SERVER_BINARY", "/does/not/exist")
    monkeypatch.setenv("NEMOTRON_MODEL_PATH", "/does/not/exist")
    service = NemotronService()
    monkeypatch.setattr(service, "_port_is_busy", lambda: False)
    spawn = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="explicit"):
        await service.start()
    spawn.assert_not_awaited()
