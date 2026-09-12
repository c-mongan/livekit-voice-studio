"""Cross-process exclusive backend lease with a durable uncertain-work marker."""

from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
from typing import IO
from urllib.parse import urlsplit, urlunsplit


def runtime_root() -> Path:
    return Path(
        os.environ.get("VOICEBOX_RUNTIME_DIR", str(Path.home() / ".local/state/livekit-voicebox"))
    )


class BackendLease:
    def __init__(self, root: Path, base_url: str) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        parsed = urlsplit(base_url)
        host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        origin = urlunsplit((parsed.scheme, f"{host}:{port}", "", "", ""))
        key = hashlib.sha256(origin.encode()).hexdigest()[:20]
        self.lock_path = root / f"{key}.lock"
        self.marker = root / f"{key}.unresolved"
        self._handle: IO[str] | None = None

    def acquire(self, *, confirmed_backend_restart: bool = False) -> None:
        handle = self.lock_path.open("a+")
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise RuntimeError(
                "Another local Voicebox worker owns this backend. Stop it before starting Studio."
            ) from None
        self._handle = handle
        if confirmed_backend_restart:
            self.marker.unlink(missing_ok=True)
        if self.marker.exists():
            self.release()
            raise RuntimeError(
                "Previous inference completion is unresolved. Confirm Voicebox has been "
                "stopped/restarted, then run Studio with --confirm-backend-restarted."
            )

    def mark_active(self) -> None:
        if self._handle is None:
            raise RuntimeError("Cannot mark backend work without its lease.")
        with self.marker.open("w") as handle:
            os.chmod(self.marker, 0o600)
            handle.write(
                "Backend work may still be active. Confirm backend stop before recovery.\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        self._sync_directory()

    def mark_safe(self) -> None:
        if self._handle is None:
            raise RuntimeError("Cannot clear backend work without its lease.")
        self.marker.unlink(missing_ok=True)
        self._sync_directory()

    def _sync_directory(self) -> None:
        directory = os.open(self.marker.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
