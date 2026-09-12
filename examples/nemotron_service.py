"""Own an already provisioned CPU recognizer; never install or borrow another process."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

from examples.nemotron_stt import NemotronSTT
from tools.setup_nemotron import sidecar_command, verify_model


class NemotronService:
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self.url = os.environ.get("NEMOTRON_URL", "http://127.0.0.1:8766")
        origin = urlsplit(self.url)
        if origin.scheme != "http" or origin.hostname != "127.0.0.1" or origin.path:
            raise RuntimeError(
                "The managed recognizer requires http://127.0.0.1 with a local port."
            )
        if origin.username or origin.password or origin.query or origin.fragment:
            raise RuntimeError("The recognizer URL must not contain credentials or query fields.")
        self.port = origin.port or 8766
        if not 1024 <= self.port <= 65535:
            raise RuntimeError("Choose an unprivileged recognizer port.")

    def _port_is_busy(self) -> bool:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            return probe.connect_ex(("127.0.0.1", self.port)) == 0

    @staticmethod
    def _verify(path: Path) -> None:
        verify_model(path)

    async def _ready(self) -> bool:
        provider = NemotronSTT(base_url=self.url)
        try:
            return await provider.is_ready()
        finally:
            await provider.aclose()

    async def start(self) -> None:
        async with self.lock:
            if self.process is not None and self.process.returncode is None:
                if not await self._ready():
                    raise RuntimeError("The owned local speech recognizer is not ready.")
                return
            if self._port_is_busy():
                raise RuntimeError("Nemotron port is already in use; stop that service explicitly.")
            binary = Path(os.environ.get("NEMOTRON_SERVER_BINARY", "")).expanduser().resolve()
            model = Path(os.environ.get("NEMOTRON_MODEL_PATH", "")).expanduser().resolve()
            if not binary.is_file() or not model.is_file():
                raise RuntimeError("Run the explicit Nemotron setup before selecting local speech.")
            await asyncio.to_thread(self._verify, model)
            runtime = binary.parent.parent.parent
            command = sidecar_command(runtime, model, self.port)
            if Path(command[0]) != binary:
                raise RuntimeError(
                    "The configured native binary is not in the supported runtime layout."
                )
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("NEMO_SPEECH_")
            }
            self.process = await asyncio.create_subprocess_exec(
                *command,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                async with asyncio.timeout(25):
                    for _ in range(100):
                        if self.process.returncode is not None:
                            raise RuntimeError("The native recognizer exited during startup.")
                        if await self._ready():
                            return
                        await asyncio.sleep(0.2)
                    raise RuntimeError("The native recognizer did not become ready.")
            except (Exception, asyncio.CancelledError):
                await self.close()
                raise

    async def close(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            async with asyncio.timeout(8):
                await process.wait()
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
