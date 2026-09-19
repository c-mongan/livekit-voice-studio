"""Public, allowlisted projection of the offline configuration doctor."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tools import studio_doctor
from tools.studio_doctor import Report, Status

# Never copy messages, actions, identifiers or exception text from inspected data.
_CHECKS = {
    "environment": ("Environment configuration", "Check the .env syntax using ./studio doctor."),
    "settings": ("Saved settings", "Review your choices in Studio Settings."),
    "dependencies": (
        "Installed dependencies",
        "Follow the dependency steps in docs/quickstart.md.",
    ),
    "frontend": ("Built interface", "Run npm --prefix web ci and npm --prefix web run build."),
    "hardware": ("Computer compatibility", "Local voice generation requires an Apple Silicon Mac."),
    "backend": ("Voice backend selection", "Choose mlx or voicebox in VOICEBOX_TTS_BACKEND."),
    "exclusive": (
        "Exclusive voice backend ownership",
        "Stop competing voice consumers, then explicitly set VOICEBOX_EXCLUSIVE=1.",
    ),
    "qwen": ("Local voice model", "Follow the model preparation steps in docs/quickstart.md."),
    "speech": (
        "Speech recognition configuration",
        "Review the speech provider in Studio Settings.",
    ),
    "livekit": (
        "LiveKit conversation configuration",
        "Set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env for conversations.",
    ),
    "reasoning": ("Reasoning configuration", "Review the reasoning provider in Studio Settings."),
    "voice": ("Selected voice", "Create and select your voice in the voice library."),
    "authentication": (
        "Account sign-in and connectivity",
        "Verify your selected provider sign-in and account access before starting a conversation.",
    ),
    "model-readiness": (
        "Model loading and audible output",
        "After preparation, explicitly generate a local voice sample to check the result.",
    ),
}


def setup_report(root: Path) -> Report:
    """Inspect local configuration only; never load models or contact providers."""
    statuses: dict[str, Status] = {}
    try:
        report = studio_doctor.run_checks(root)
        for check in report["checks"]:
            if check["id"] in _CHECKS and check["status"] in ("pass", "missing", "unverified"):
                statuses[check["id"]] = check["status"]
    except (OSError, ValueError, RuntimeError, ImportError):
        # A diagnostic failure must not expose filesystem paths through HTTP.
        statuses = {}
    result: Report = {"version": 1, "checks": []}
    descriptions = {
        "pass": "Configuration check passed; live operation has not been verified.",
        "missing": "Needs attention before this capability is ready.",
        "unverified": "Not verified by this offline check.",
    }
    for identifier, (label, action) in _CHECKS.items():
        status = statuses.get(identifier, "unverified")
        result["checks"].append(
            {
                "id": identifier,
                "status": status,
                "message": f"{label}: {descriptions[status]}",
                "action": action if status != "pass" else "",
            }
        )
    return result


class SetupUnavailable(RuntimeError):
    """A fixed public diagnostic; never include filesystem exception details."""


class SetupChecks:
    """At most one offline inspection thread per app, including after timeout.

    Timing out a request cannot stop a blocked filesystem call. Shield its task
    so later requests join the same work instead of exhausting the thread pool.
    Completed results are never cached across requests.
    """

    def __init__(self, root: Path, *, timeout: float = 3.0) -> None:
        self._root = root
        self._timeout = timeout
        self._pending: asyncio.Task[Report | None] | None = None

    async def _inspect(self) -> Report | None:
        try:
            return await asyncio.to_thread(setup_report, self._root)
        except Exception:
            # A timed-out caller may already have left; keep detached failures
            # from logging private paths as unhandled task exceptions.
            return None

    async def get(self) -> Report:
        if self._pending is None or self._pending.done():
            self._pending = asyncio.create_task(self._inspect())
        try:
            result = await asyncio.wait_for(asyncio.shield(self._pending), self._timeout)
        except TimeoutError:
            result = None
        if result is None:
            raise SetupUnavailable(
                "Setup checks could not finish because local files are slow or unavailable. "
                "Make sure the project and model files are available on this computer, "
                "then retry. Studio is still running."
            )
        return result
