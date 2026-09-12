"""Local, bounded narration of existing reply text. No language-model requests."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import importlib
import io
import json
import os
import re
import select
import signal
import stat
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp

URL = "http://127.0.0.1:8765"
MAX_INPUT = 65536
MAX_WAV = 24000 * 2 * 30 + 44


class ReadAloudError(Exception):
    pass


def spoken_excerpt(text: str) -> str:
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > MAX_INPUT:
        raise ReadAloudError("Provide a nonempty reply of at most 64 KiB.")
    lines = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced:
            lines.append(re.sub(r"^\s*(?:#+\s*|[-*>]\s+|\d+\.\s+)", "", line))
    plain = " ".join(lines)
    plain = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", plain)
    plain = re.sub(r"https?://\S+", "link", plain)
    plain = re.sub(r"`[^`]*`", "code", plain)
    plain = " ".join(plain.replace("*", "").replace("_", " ").split())
    if not plain:
        raise ReadAloudError("The reply has no prose to read; code blocks are not narrated.")
    if len(plain) > 300:
        suffix = " The reply continues in your agent."
        beginning = plain[: 300 - len(suffix)]
        beginning = beginning.rsplit(" ", 1)[0] if " " in beginning else beginning
        plain = beginning.rstrip(".,;:") + "." + suffix
        if len(plain) > 300:
            plain = plain[: 299 - len(suffix)].rstrip() + "." + suffix
    return plain


def notification_text(payload: str) -> str | None:
    if len(payload.encode()) > MAX_INPUT:
        raise ReadAloudError("Notification exceeds 64 KiB.")
    try:
        data = json.loads(payload)
    except (ValueError, RecursionError):
        raise ReadAloudError("Expected a JSON completion notification.") from None
    if not isinstance(data, dict):
        raise ReadAloudError("Expected a JSON notification object.")
    if data.get("type") != "agent-turn-complete":
        return None
    value = data.get("last-assistant-message")
    if "last-assistant-message" in data and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ReadAloudError("Completion notification has no assistant reply.")
    return value


def private_json(path: Path, data: Any) -> None:
    if path.is_symlink():
        raise ReadAloudError("Private read-aloud state must not contain symbolic links.")
    fd, temporary = tempfile.mkstemp(prefix=".read-aloud-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def reserve_notification(payload: str, directory: Path) -> bool:
    """At most one attempt per recent turn, even if playback later fails."""
    data = json.loads(payload)
    identifiers = [data.get("thread-id"), data.get("turn-id")]
    if any(not isinstance(value, str) or not 1 <= len(value) <= 256 for value in identifiers):
        raise ReadAloudError("Codex notification requires bounded thread-id and turn-id fields.")
    digest = hashlib.sha256(json.dumps(identifiers).encode()).hexdigest()
    ledger = directory / "notification-receipts.json"
    with (directory / "notification.lock").open("a") as lock:
        os.chmod(directory / "notification.lock", 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReadAloudError(
                "Another notification is being admitted; this one was not queued."
            ) from None
        seen: list[str] = []
        if ledger.exists():
            try:
                if ledger.is_symlink():
                    raise ValueError
                with ledger.open("rb") as handle:
                    raw = handle.read(8193)
                if len(raw) > 8192:
                    raise ValueError
                seen = json.loads(raw)
                if (
                    not isinstance(seen, list)
                    or len(seen) > 64
                    or any(
                        not isinstance(x, str) or not re.fullmatch("[a-f0-9]{64}", x) for x in seen
                    )
                ):
                    raise ValueError
            except (ValueError, OSError, RecursionError):
                raise ReadAloudError(
                    "Notification receipts are invalid; no reply was replayed."
                ) from None
        if digest in seen:
            return False
        private_json(ledger, [*seen, digest][-64:])
        return True


def record_status(directory: Path, state: str, message: str | None = None) -> None:
    private_json(
        directory / "read-aloud-status.json",
        {"state": state, "message": message, "time": time.time()},
    )


def decode_wav(data: bytes) -> bytes:
    if len(data) > MAX_WAV:
        raise ReadAloudError("Generated audio exceeded its bound.")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != 24000
                or audio.getcomptype() != "NONE"
                or not 0 < audio.getnframes() <= 24000 * 30
            ):
                raise ValueError
            pcm = audio.readframes(audio.getnframes())
            if len(pcm) != audio.getnframes() * 2 or not any(pcm):
                raise ValueError
            return pcm
    except (wave.Error, EOFError, ValueError):
        raise ReadAloudError("The server returned unsupported or silent audio.") from None


def output_stream(**kwargs: Any) -> Any:
    try:
        return importlib.import_module("sounddevice").RawOutputStream(**kwargs)
    except ImportError:
        raise ReadAloudError("Install the playback extra before using read-aloud.") from None


def play_pcm(pcm: bytes, stop: threading.Event) -> None:
    if stop.is_set():
        return
    try:
        with output_stream(samplerate=24000, channels=1, dtype="int16", blocksize=480) as output:
            for offset in range(0, len(pcm), 960):
                if stop.is_set():
                    output.abort()
                    break
                output.write(pcm[offset : offset + 960])
    except Exception as error:
        if isinstance(error, ReadAloudError):
            raise
        raise ReadAloudError("Could not play audio through the local output device.") from None


async def api(
    http: aiohttp.ClientSession,
    route: str,
    body: dict[str, Any] | None = None,
    *,
    base_url: str = URL,
    audio: bool = False,
) -> Any:
    try:
        async with http.request(
            "GET" if body is None else "POST",
            base_url + route,
            json=body,
            headers={"X-Voicebox-Studio": "1"},
        ) as response:
            if not 200 <= response.status < 300:
                raise ReadAloudError(
                    f"Studio refused read-aloud (HTTP {response.status}). "
                    "Check setup or wait for the current activity; no request was replayed."
                )
            limit = MAX_WAV if audio else 16384
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                if len(data) + len(chunk) > limit:
                    raise ReadAloudError("Studio response exceeded its allocation limit.")
                data.extend(chunk)
            if audio:
                return bytes(data)
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError
            return value
    except (aiohttp.ClientError, TimeoutError, ValueError, RecursionError):
        raise ReadAloudError(
            "Cannot confirm local Studio state. Use ./studio status; no request was replayed."
        ) from None


async def speak(
    text: str,
    stop: asyncio.Event,
    *,
    base_url: str = URL,
    player: Callable[[bytes, threading.Event], None] = play_pcm,
) -> None:
    excerpt = spoken_excerpt(text)
    handle = None
    playback: asyncio.Task[None] | None = None
    stop_audio = threading.Event()
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=8), trust_env=False
    ) as http:
        try:
            async with asyncio.timeout(80):
                settings = await api(http, "/api/settings", base_url=base_url)
                voice = settings.get("voiceId")
                if not isinstance(voice, str) or not voice:
                    raise ReadAloudError("Choose an authorized saved voice in Studio first.")
                if stop.is_set():
                    return
                job = await api(
                    http,
                    "/api/audition",
                    {"voiceId": voice, "text": excerpt, "holdPlayback": True},
                    base_url=base_url,
                )
                identifier = job.get("auditionId")
                if not isinstance(identifier, str):
                    raise ReadAloudError("Studio did not return a read-aloud handle.")
                handle = {"auditionId": identifier}
                while not stop.is_set():
                    state = await api(http, "/api/audition/status", handle, base_url=base_url)
                    if state["state"] == "ready":
                        break
                    if state["state"] != "generating" or state["phase"] == "blocked":
                        raise ReadAloudError("Local speech generation stopped or failed.")
                    await asyncio.sleep(0.2)
                if stop.is_set():
                    return
                data = await api(http, "/api/audition/audio", handle, base_url=base_url, audio=True)
                pcm = decode_wav(data)
                playback = asyncio.create_task(asyncio.to_thread(player, pcm, stop_audio))
                while not playback.done() and not stop.is_set():
                    state = await api(http, "/api/audition/status", handle, base_url=base_url)
                    if state["state"] != "ready" or state["phase"] != "active":
                        raise ReadAloudError("Playback ownership ended; speech was stopped.")
                    await asyncio.sleep(0.2)
                stop_audio.set()
                await asyncio.shield(playback)
        except TimeoutError:
            raise ReadAloudError(
                "Read-aloud exceeded its 80-second bound and was stopped."
            ) from None
        finally:
            stop_audio.set()
            try:
                if playback is not None:
                    await asyncio.shield(playback)
            finally:
                if handle is not None:
                    await api(http, "/api/audition/end", handle, base_url=base_url)


def private_directory() -> Path:
    path = Path.home() / ".local/share/voicebox-studio/service"
    if path.is_symlink():
        raise ReadAloudError("Read-aloud control directory must not be a symbolic link.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


async def stop_speaking(directory: Path) -> bool:
    try:
        async with asyncio.timeout(2):
            reader, writer = await asyncio.open_unix_connection(str(directory / "read-aloud.sock"))
            try:
                writer.write(b"stop\n")
                await writer.drain()
                return await reader.readline() == b"stopping\n"
            finally:
                writer.close()
                await writer.wait_closed()
    except (FileNotFoundError, ConnectionRefusedError):
        return False
    except (OSError, TimeoutError):
        raise ReadAloudError("Could not confirm the local playback stop request.") from None


async def run_narration(text: str, directory: Path) -> None:
    if (directory / "muted").exists():
        record_status(directory, "muted")
        print("Read-aloud is muted; no synthesis requested.")
        return
    with (directory / "narration.lock").open("a") as lock:
        os.chmod(directory / "narration.lock", 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReadAloudError("Read-aloud is busy. The new reply was not queued.") from None
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        socket_path = directory / "read-aloud.sock"
        if socket_path.exists():
            if not stat.S_ISSOCK(socket_path.lstat().st_mode):
                raise ReadAloudError("Unexpected file at the private playback socket path.")
            if await stop_speaking(directory):
                raise ReadAloudError("Another playback controller is still stopping.")
            socket_path.unlink()

        async def control(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                async with asyncio.timeout(2):
                    if await reader.read(16) == b"stop\n":
                        stop.set()
                        writer.write(b"stopping\n")
                        await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_unix_server(control, path=str(socket_path))
        os.chmod(socket_path, 0o600)
        try:
            async with server:
                print("Reading locally. Use ./studio stop-speaking or Ctrl+C to stop.")
                record_status(directory, "reading")
                await speak(text, stop)
                record_status(directory, "stopped" if stop.is_set() else "finished")
        finally:
            server.close()
            await server.wait_closed()
            socket_path.unlink(missing_ok=True)


def stdin_text() -> str:
    if sys.stdin.isatty():
        raise ReadAloudError("Pipe a completed reply to ./studio speak; no interactive input wait.")
    deadline = time.monotonic() + 5
    result = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([sys.stdin], [], [], remaining)[0]:
            raise ReadAloudError("Input did not finish within five seconds.")
        block = os.read(sys.stdin.fileno(), MAX_INPUT + 1 - len(result))
        if not block:
            break
        result.extend(block)
        if len(result) > MAX_INPUT:
            raise ReadAloudError("Input exceeded 64 KiB.")
    try:
        return result.decode("utf-8")
    except UnicodeDecodeError:
        raise ReadAloudError("Input must be UTF-8 text.") from None


def main(action: str, payload: str | None = None) -> None:
    try:
        directory = private_directory()
        if action == "read-aloud-status":
            path = directory / "read-aloud-status.json"
            state = {"state": "not used"}
            if path.exists():
                try:
                    if path.is_symlink():
                        raise ValueError
                    with path.open("rb") as handle:
                        raw = handle.read(4097)
                    if len(raw) > 4096:
                        raise ValueError
                    state = json.loads(raw)
                    if not isinstance(state, dict) or set(state) != {"state", "message", "time"}:
                        raise ValueError
                except (ValueError, RecursionError):
                    raise ReadAloudError("Private read-aloud status is invalid.") from None
            print(json.dumps({"muted": (directory / "muted").exists(), "lastResult": state}))
            return
        if action in ("mute", "unmute"):
            if action == "mute":
                (directory / "muted").touch(mode=0o600)
                asyncio.run(stop_speaking(directory))
            else:
                (directory / "muted").unlink(missing_ok=True)
            print("Read-aloud " + ("muted." if action == "mute" else "unmuted."))
            return
        if action == "stop-speaking":
            print(
                "Stop requested."
                if asyncio.run(stop_speaking(directory))
                else "No read-aloud active."
            )
            return
        if action == "codex-notify":
            notification = payload or stdin_text()
            text = notification_text(notification)
            if text is None:
                return
            if not reserve_notification(notification, directory):
                print("Duplicate notification ignored; no synthesis requested.")
                return
        else:
            text = stdin_text()
        if text is not None:
            asyncio.run(run_narration(text, directory))
    except (ReadAloudError, OSError) as error:
        message = str(error) if isinstance(error, ReadAloudError) else error.strerror or str(error)
        if "directory" in locals():
            try:
                record_status(directory, "failed", message)
            except (OSError, ReadAloudError):
                print("Read-aloud: could not write its private status.", file=sys.stderr)
        print(f"Read-aloud: {message}", file=sys.stderr)
        raise SystemExit(1) from None
