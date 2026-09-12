"""Explicit macOS Studio service control. Commands return; launchd owns the server."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import platform
import plistlib
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LABEL = "io.github.livekit-voice-studio"
URL = "http://127.0.0.1:8765"


class ServiceError(Exception):
    pass


def make_plist(root: Path, directory: Path, *, confirm: bool = False) -> dict[str, Any]:
    arguments = [
        str(root / ".venv/bin/python"),
        "-m",
        "examples.studio",
        "--log-file",
        str(directory / "studio.log"),
    ]
    if confirm:
        arguments.append("--confirm-backend-restarted")
    return {
        "Label": LABEL,
        "ProgramArguments": arguments,
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PATH": os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"),
        },
        "RunAtLoad": True,
        "KeepAlive": False,
        "AbandonProcessGroup": False,
        "ExitTimeOut": 180,
        "StandardOutPath": str(directory / "bootstrap.log"),
        "StandardErrorPath": str(directory / "bootstrap.log"),
        "Umask": 0o077,
    }


class StudioService:
    def __init__(self, root: Path, directory: Path, *, uid: int | None = None) -> None:
        self.root = root.resolve()
        self.directory = directory.expanduser().absolute()
        self.plist = self.directory / "studio.plist"
        self.stopping = self.directory / "stopping"
        self.domain = f"gui/{os.getuid() if uid is None else uid}"
        self.target = self.domain + "/" + LABEL

    def run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(arguments, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            raise ServiceError(
                "The macOS service manager did not respond; no blind retry."
            ) from None

    def prepare_directory(self) -> None:
        if self.directory.is_symlink():
            raise ServiceError("The service directory must not be a symbolic link.")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)

    @contextlib.contextmanager
    def control(self) -> Iterator[None]:
        self.prepare_directory()
        descriptor = os.open(self.directory / "control.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ServiceError("Another Start/Stop command is already in progress.") from None
            yield

    def check_owner(self) -> None:
        if not self.plist.exists():
            return
        try:
            if self.plist.is_symlink():
                raise ValueError
            with self.plist.open("rb") as handle:
                raw = handle.read(32769)
            if len(raw) > 32768:
                raise ValueError
            data = plistlib.loads(raw)
            if (
                data.get("Label") != LABEL
                or data.get("WorkingDirectory") != str(self.root)
                or data.get("ProgramArguments", [])[:3]
                != [str(self.root / ".venv/bin/python"), "-m", "examples.studio"]
            ):
                raise ServiceError(
                    "This service belongs to another checkout. Stop it from that checkout first."
                )
        except (OSError, ValueError, plistlib.InvalidFileException, AttributeError):
            raise ServiceError("The private service configuration is invalid.") from None

    def job(self) -> dict[str, Any]:
        result = self.run(["launchctl", "print", self.target])
        if result.returncode:
            if "could not find service" in result.stderr.lower():
                return {"loaded": False, "pid": None, "exitCode": None}
            raise ServiceError("Cannot inspect the macOS service; no state change attempted.")
        pid = re.search(r"^\s*pid = (\d+)", result.stdout, re.MULTILINE)
        code = re.search(r"^\s*last exit code = (-?\d+)", result.stdout, re.MULTILINE)
        return {
            "loaded": True,
            "pid": int(pid[1]) if pid else None,
            "exitCode": int(code[1]) if code else None,
        }

    def listening(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 8765), timeout=0.5):
                return True
        except ConnectionRefusedError:
            return False
        except OSError:
            raise ServiceError("Cannot determine whether port 8765 is occupied.") from None

    def status(self) -> dict[str, Any]:
        self.check_owner()
        job = self.job()
        if not job["pid"]:
            if self.listening():
                return {"state": "unmanaged", "ready": False, "url": URL}
            return {
                "state": "failed" if job["exitCode"] not in (0, None) else "stopped",
                "ready": False,
                "exitCode": job["exitCode"],
                "url": URL,
            }
        if not self.plist.exists():
            raise ServiceError("An existing service is not owned by this launcher.")
        if self.stopping.exists():
            return {"state": "stopping", "ready": False, "pid": job["pid"], "url": URL}
        try:
            with urllib.request.urlopen(URL + "/api/status", timeout=1) as response:
                raw = response.read(16385)
            if len(raw) > 16384:
                raise ValueError
            state = json.loads(raw)
            if not isinstance(state, dict) or type(state.get("ready")) is not bool:
                raise ValueError
            return {
                "state": "running",
                "ready": state["ready"],
                "phase": state["phase"],
                "pid": job["pid"],
                "url": URL,
            }
        except (OSError, ValueError, KeyError):
            return {
                "state": "starting"
                if time.time() - self.plist.stat().st_mtime < 15
                else "unresponsive",
                "ready": False,
                "pid": job["pid"],
                "url": URL,
            }

    def write_config(self, *, confirm: bool = False) -> None:
        self.prepare_directory()
        self.check_owner()
        fd, name = tempfile.mkstemp(prefix=".launch-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                plistlib.dump(make_plist(self.root, self.directory, confirm=confirm), handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.plist)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        for filename in ("bootstrap.log", "studio.log"):
            path = self.directory / filename
            if path.is_symlink():
                raise ServiceError("Service logs must not be symbolic links.")
            with path.open("w" if filename == "bootstrap.log" else "a"):
                os.chmod(path, 0o600)
        self.stopping.unlink(missing_ok=True)

    def wait_ready(self) -> dict[str, Any]:
        deadline = time.monotonic() + 8
        while True:
            state = self.status()
            if state["state"] not in ("starting", "stopped") or time.monotonic() >= deadline:
                return state
            time.sleep(0.2)

    def start(self, *, confirm: bool = False) -> dict[str, Any]:
        with self.control():
            self.check_owner()
            job = self.job()
            if job["loaded"] and not self.plist.exists():
                raise ServiceError("An existing service is not owned by this launcher.")
            if job["pid"]:
                if self.stopping.exists():
                    raise ServiceError("Studio is still stopping. Wait for a stopped status.")
                return self.status()
            if self.listening():
                raise ServiceError(
                    "Studio or another app is running outside this launcher on port 8765. "
                    "Stop that instance before starting a managed one."
                )
            if not (self.root / ".venv/bin/python").is_file():
                raise ServiceError("Install the Python dependencies first; see docs/quickstart.md.")
            if not (self.root / "web/dist/index.html").is_file():
                raise ServiceError("Build the frontend first: npm --prefix web run build.")
            if job["loaded"] and self.run(["launchctl", "bootout", self.target]).returncode:
                raise ServiceError("Could not unload the stopped service.")
            self.write_config(confirm=confirm)
            if self.run(["launchctl", "bootstrap", self.domain, str(self.plist)]).returncode:
                raise ServiceError("Could not start the user service; inspect the private logs.")
            return self.wait_ready()

    def stop(self) -> dict[str, Any]:
        with self.control():
            self.check_owner()
            job = self.job()
            if job["loaded"] and not self.plist.exists():
                raise ServiceError("An existing service is not owned by this launcher.")
            if not job["pid"]:
                if self.listening():
                    raise ServiceError(
                        "Port 8765 is owned outside this launcher; that process was not stopped."
                    )
                if job["loaded"] and self.run(["launchctl", "bootout", self.target]).returncode:
                    raise ServiceError("Could not unload the stopped service.")
                self.stopping.unlink(missing_ok=True)
                return {"state": "stopped", "ready": False, "url": URL}
            self.stopping.touch(mode=0o600)
            if self.run(["launchctl", "kill", "SIGTERM", self.target]).returncode:
                raise ServiceError("Could not request graceful shutdown. Check Studio status.")
            return {"state": "stopping", "ready": False, "url": URL}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "start",
            "status",
            "stop",
            "speak",
            "codex-notify",
            "stop-speaking",
            "mute",
            "unmute",
            "read-aloud-status",
        ),
    )
    parser.add_argument(
        "payload", nargs="?", help="Completion JSON for an explicit notification hook."
    )
    parser.add_argument("--open", action="store_true", help="Open the browser after Start.")
    parser.add_argument("--json", action="store_true", help="Print a nonsecret status object.")
    parser.add_argument(
        "--confirm-backend-restarted",
        action="store_true",
        help="Recovery only: explicitly confirm the inference backend was stopped/restarted.",
    )
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error(
            "Managed launch currently supports macOS. Use make studio-foreground on Linux."
        )
    if args.action in (
        "speak",
        "codex-notify",
        "stop-speaking",
        "mute",
        "unmute",
        "read-aloud-status",
    ):
        from tools.read_aloud import main as read_aloud_main

        read_aloud_main(args.action, args.payload)
        return
    service = StudioService(ROOT, Path.home() / ".local/share/voicebox-studio/service")
    try:
        state = (
            service.start(confirm=args.confirm_backend_restarted)
            if args.action == "start"
            else service.stop()
            if args.action == "stop"
            else service.status()
        )
        if args.json:
            print(json.dumps(state))
        else:
            print(f"Studio: {state['state']}. {URL}")
            if state["state"] == "running":
                print("The server is managed separately. This command is finished.")
                print(f"Activity: {state.get('phase', 'unknown')}. Ready: {state['ready']}.")
            elif state["state"] == "stopping":
                print("Graceful drain requested. Run ./studio status to confirm it has stopped.")
            elif state["state"] in ("failed", "starting", "unresponsive"):
                print(f"Logs: {service.directory / 'studio.log'} and bootstrap.log")
        if args.open and args.action == "start" and state["state"] == "running":
            webbrowser.open(URL)
        if state["state"] in ("failed", "unmanaged", "unresponsive"):
            raise SystemExit(1)
    except ServiceError as error:
        print(f"Studio: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except OSError as error:
        print(f"Studio service files are unavailable: {error.strerror}.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
