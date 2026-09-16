"""Read-only, offline Studio configuration preflight; never a readiness probe."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import sys
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit

Status = Literal["pass", "missing", "unverified"]


class Check(TypedDict):
    id: str
    status: Status
    message: str
    action: str


class Report(TypedDict):
    version: int
    checks: list[Check]


INSTALL = "Follow docs/quickstart.md; sync the required extras together with uv sync --frozen."
CONFIGURE = "Edit the worktree .env or Studio Settings; then run ./studio doctor again."
BASE_PACKAGES = (
    "livekit-plugins-voicebox",
    "livekit-agents",
    "livekit",
    "aiohttp",
    "numpy",
    "soundfile",
    "python-dotenv",
    "livekit-plugins-openai",
    "livekit-plugins-silero",
)
# Static format contracts only. Runtime decoding/loading remains authoritative;
# importing the runtime validators would load native libraries during preflight.
MAX_JSON_BYTES = 1024 * 1024
MAX_WAV_BYTES = 16 * 1024 * 1024
PRESETS = {
    "hermes": ("profile-default", "none"),
    "copilot": ("gpt-5.6-luna", "low"),
    "codex": ("gpt-5.6-luna", "low"),
    "azure": ("gpt-4.1-nano", "none"),
    "openai": ("gpt-4.1-mini", "none"),
}


def _read(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError
    return data


def _json(path: Path, limit: int = MAX_JSON_BYTES) -> dict[str, Any]:
    value = json.loads(_read(path, limit))
    if not isinstance(value, dict):
        raise ValueError
    return value


def _voice_path(library: Path, identifier: object) -> Path:
    if not isinstance(identifier, str) or not re.fullmatch("[a-f0-9]{32}", identifier):
        raise ValueError
    path = library / "voices" / identifier
    if (
        path.is_symlink()
        or not path.is_dir()
        or any((path / name).is_symlink() for name in ("voice.json", "reference.wav"))
    ):
        raise ValueError
    return path


def package_installed(name: str) -> bool:
    """Inspect distribution metadata without importing optional model runtimes."""
    try:
        version(name)
        return True
    except (PackageNotFoundError, ValueError, OSError):
        return False


def _path(value: str, root: Path, env: Mapping[str, str]) -> Path:
    if value == "~" or value.startswith("~/"):
        value = env.get("HOME", str(Path.home())) + value[1:]
    path = Path(value)
    return path if path.is_absolute() else root / path


def _environment(root: Path, process: Mapping[str, str]) -> dict[str, str]:
    path = root / ".env"
    if not path.exists() and not path.is_symlink():
        return dict(process)
    from dotenv.parser import parse_stream
    from dotenv.variables import parse_variables

    with path.open("rb") as handle:
        raw = handle.read(65537)
    if len(raw) > 65536 or b"\0" in raw:
        raise ValueError
    values: dict[str, str] = {}
    # Parse without the dotenv logger (which can disclose malformed input), shell
    # evaluation, or mutations to os.environ. Match load_dotenv(override=False).
    for binding in parse_stream(io.StringIO(raw.decode("utf-8"))):
        if binding.error:
            raise ValueError
        if binding.key is not None and binding.value is not None:
            context = {**values, **process}
            values[binding.key] = "".join(
                atom.resolve(context) for atom in parse_variables(binding.value)
            )
    return {**values, **process}


def _saved_environment(root: Path, env: dict[str, str]) -> bool:
    library_path = _path(
        env.get("VOICEBOX_LIBRARY_DIR", "~/.local/share/voicebox-studio/library"), root, env
    )
    settings_path = library_path / "settings.json"
    if not settings_path.exists() and not settings_path.is_symlink():
        return False
    if not settings_path.is_file():
        raise ValueError
    settings = _json(settings_path, 4096)
    settings.setdefault("codexRestrictedApproved", False)
    settings.setdefault("hermesProfile", "default")
    settings.setdefault("hermesBaseUrl", "http://127.0.0.1:8642")
    provider = settings.get("llmProvider")
    if (
        set(settings)
        != {
            "sttProvider",
            "llmProvider",
            "llmModel",
            "reasoningEffort",
            "hermesProfile",
            "hermesBaseUrl",
            "voiceId",
            "codexRestrictedApproved",
        }
        or settings["sttProvider"] not in ("nemotron", "azure", "openai")
        or not isinstance(provider, str)
        or provider not in PRESETS
        or (settings["llmModel"], settings["reasoningEffort"]) != PRESETS[provider]
        or not isinstance(settings["hermesProfile"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", settings["hermesProfile"])
        or not isinstance(settings["hermesBaseUrl"], str)
        or not _hermes_origin(settings["hermesBaseUrl"])
        or type(settings["codexRestrictedApproved"]) is not bool
        or (provider == "codex" and not settings["codexRestrictedApproved"])
    ):
        raise ValueError
    voice = (
        _voice_path(library_path, settings["voiceId"]) if settings["voiceId"] is not None else None
    )
    env.update(
        VOICEBOX_STT_PROVIDER=settings["sttProvider"],
        VOICEBOX_LLM_PROVIDER=settings["llmProvider"],
        VOICEBOX_LLM_MODEL=settings["llmModel"],
        VOICEBOX_REASONING_EFFORT=settings["reasoningEffort"],
        HERMES_PROFILE=settings["hermesProfile"],
        HERMES_API_BASE_URL=settings["hermesBaseUrl"],
        VOICEBOX_CODEX_RESTRICTED="1" if settings["codexRestrictedApproved"] else "0",
    )
    if voice is not None:
        env["VOICEBOX_VOICE_BUNDLE"] = str(voice)
        env["VOICEBOX_TTS_BACKEND"] = "mlx"
    return True


def _snapshot(path: Path) -> None:
    required = (
        "config.json",
        "model.safetensors",
        "tokenizer_config.json",
        "speech_tokenizer/config.json",
        "speech_tokenizer/model.safetensors",
    )
    tokenizer = (path / "tokenizer.json").is_file() or all(
        (path / name).is_file() for name in ("vocab.json", "merges.txt")
    )
    if not tokenizer or any(not (path / name).is_file() for name in required):
        raise ValueError
    config = _json(path / "config.json")
    if any(
        config.get(key) != value
        for key, value in (
            ("model_type", "qwen3_tts"),
            ("tts_model_type", "base"),
            ("tts_model_size", "0b6"),
        )
    ):
        raise ValueError


def _bundle(path: Path) -> None:
    metadata = _json(path / "voice.json")
    if (
        metadata.get("version") != 1
        or metadata.get("authorized") is not True
        or not isinstance(metadata.get("name"), str)
        or not 1 <= len(metadata["name"].strip()) <= 100
        or not isinstance(metadata.get("transcript"), str)
        or not 1 <= len(metadata["transcript"].strip()) <= 4000
    ):
        raise ValueError
    audio = _read(path / "reference.wav", MAX_WAV_BYTES)
    if (
        audio[:4] != b"RIFF"
        or audio[8:12] != b"WAVE"
        or metadata.get("audio_sha256") != hashlib.sha256(audio).hexdigest()
    ):
        raise ValueError


def _url(value: str, schemes: tuple[str, ...]) -> bool:
    try:
        url = urlsplit(value)
        return bool(
            url.scheme in schemes
            and url.hostname
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and (url.port is None or 0 < url.port <= 65535)
        )
    except ValueError:
        return False


def _hermes_origin(value: str) -> bool:
    try:
        url = urlsplit(value)
        _ = url.port
        return bool(
            url.scheme in {"http", "https"}
            and url.hostname
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and not url.path.rstrip("/")
            and (url.scheme == "https" or url.hostname in {"127.0.0.1", "localhost", "::1"})
        )
    except ValueError:
        return False


def _native(root: Path, env: Mapping[str, str]) -> bool:
    from tools.setup_nemotron import MODEL_SIZE, sidecar_command

    if not env.get("NEMOTRON_SERVER_BINARY") or not env.get("NEMOTRON_MODEL_PATH"):
        return False
    binary = _path(env["NEMOTRON_SERVER_BINARY"], root, env).resolve()
    model = _path(env["NEMOTRON_MODEL_PATH"], root, env)
    origin = urlsplit(env.get("NEMOTRON_URL", "http://127.0.0.1:8766"))
    port = origin.port or 8766
    return (
        origin.scheme == "http"
        and origin.hostname == "127.0.0.1"
        and not any((origin.path, origin.username, origin.password, origin.query, origin.fragment))
        and 1024 <= port <= 65535
        and binary.is_file()
        and os.access(binary, os.X_OK)
        and model.is_file()
        and model.stat().st_size == MODEL_SIZE
        and str(binary) == sidecar_command(binary.parent.parent.parent, model, port)[0]
    )


def run_checks(root: Path, environ: Mapping[str, str] | None = None) -> Report:
    report: Report = {"version": 1, "checks": []}

    def add(identifier: str, status: Status, message: str, action: str = "") -> None:
        report["checks"].append(
            {"id": identifier, "status": status, "message": message, "action": action}
        )

    def check(identifier: str, ready: bool, message: str, action: str) -> None:
        add(identifier, "pass" if ready else "missing", message, "" if ready else action)

    env = dict(os.environ if environ is None else environ)
    configuration_valid = True
    try:
        env = _environment(root, env)
        add(
            "environment",
            "pass",
            "Worktree environment read; existing process values take precedence.",
        )
    except (OSError, ValueError, ImportError, RecursionError):
        configuration_valid = False
        add(
            "environment",
            "missing",
            "Cannot safely parse the worktree .env.",
            "Install python-dotenv and repair .env syntax/encoding "
            "(maximum 64 KiB); never source it.",
        )
    try:
        if configuration_valid:
            saved = _saved_environment(root, env)
            add(
                "settings",
                "pass",
                "Saved settings take precedence."
                if saved
                else "No saved settings; checking environment without creating a library.",
            )
        else:
            add("settings", "unverified", "Saved settings not inspected because .env is invalid.")
    except (OSError, ValueError, TypeError, ImportError, RecursionError):
        configuration_valid = False
        add(
            "settings",
            "missing",
            "Saved settings are unreadable or invalid; no fallback was used.",
            "Restore valid private settings and installed dependencies. See docs/launching.md.",
        )

    legacy = env.get("VOICEBOX_AI_PROVIDER", "openai")
    speech = env.get("VOICEBOX_STT_PROVIDER", legacy)
    reasoning = env.get("VOICEBOX_LLM_PROVIDER", legacy)
    backend = env.get("VOICEBOX_TTS_BACKEND", "voicebox")
    packages = list(BASE_PACKAGES)
    if configuration_valid:
        if backend == "mlx":
            packages.extend(("mlx", "mlx-audio"))
        if reasoning == "copilot":
            packages.append("github-copilot-sdk")
        if "azure" in (speech, reasoning):
            packages.append("livekit-plugins-azure")
    missing = [name for name in packages if not package_installed(name)]
    python_ok = (3, 11) <= sys.version_info[:2] < (3, 14)
    check(
        "dependencies",
        not missing and python_ok,
        "Installed package metadata and supported Python found; imports are not runtime proof."
        if not missing and python_ok
        else "Missing distributions: " + ", ".join(missing) + ". Python 3.11–3.13 is required.",
        INSTALL,
    )
    frontend_ready = (root / "web/dist/index.html").is_file() and (
        root / "web/dist/assets"
    ).is_dir()
    check(
        "frontend",
        frontend_ready,
        "Frontend build entry point and assets directory exist."
        if frontend_ready
        else "Frontend build entry point or assets directory is missing.",
        "Run npm --prefix web ci && npm --prefix web run build (Node.js 22.12+).",
    )

    if not configuration_valid:
        for identifier in (
            "hardware",
            "backend",
            "exclusive",
            "qwen",
            "speech",
            "livekit",
            "reasoning",
            "voice",
        ):
            add(
                identifier,
                "unverified",
                "Blocked by invalid environment or saved settings.",
                "Repair the reported configuration error; no defaults were substituted.",
            )
    else:
        supported = platform.system() == "Darwin" and platform.machine() == "arm64"
        if backend == "mlx":
            check(
                "hardware",
                supported,
                "Apple Silicon is required for the selected MLX backend.",
                "Use an Apple Silicon Mac for MLX; see docs/quickstart.md.",
            )
        else:
            add(
                "hardware",
                "unverified",
                "External Voicebox hardware is not inspected.",
                "Verify the external backend separately; "
                "generated Studio auditions require Apple Silicon.",
            )
        check(
            "backend",
            backend in ("mlx", "voicebox"),
            "TTS backend selection must be mlx or voicebox.",
            CONFIGURE,
        )
        check(
            "exclusive",
            env.get("VOICEBOX_EXCLUSIVE") == "1",
            "Exclusive backend ownership must be explicitly acknowledged.",
            "Stop competing consumers yourself, then set VOICEBOX_EXCLUSIVE=1; "
            "doctor stops nothing.",
        )
        if backend == "mlx":
            try:
                if not env.get("VOICEBOX_MLX_MODEL_PATH", "").strip():
                    raise ValueError
                _snapshot(_path(env["VOICEBOX_MLX_MODEL_PATH"], root, env))
                add("qwen", "pass", "Local Qwen snapshot structure and model identity validated.")
            except ImportError:
                add("qwen", "missing", "Snapshot validator dependencies are missing.", INSTALL)
            except (OSError, ValueError, RuntimeError, RecursionError):
                add(
                    "qwen",
                    "missing",
                    "A complete local Qwen 0.6B base snapshot is required.",
                    "Set VOICEBOX_MLX_MODEL_PATH to an existing snapshot; see docs/quickstart.md.",
                )
        else:
            add(
                "qwen",
                "unverified",
                "External cached/loaded Qwen model was not queried.",
                "Verify Voicebox model readiness separately; doctor never probes or downloads.",
            )

        def populated(*names: str) -> bool:
            return all(env.get(name, "").strip() for name in names)

        def cli(name: str) -> bool:
            return shutil.which(name, path=env.get("PATH", os.defpath)) is not None

        def azure(kind: str) -> bool:
            names = (
                ("AZURE_OPENAI_ACCOUNT", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")
                if kind == "reasoning"
                else ("AZURE_SPEECH_ACCOUNT", "AZURE_SPEECH_REGION")
            )
            return (
                populated("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", *names)
                and cli("az")
                and (kind != "reasoning" or _url(env["AZURE_OPENAI_ENDPOINT"], ("https",)))
            )

        speech_ok = False
        speech_action = "Choose Nemotron, Azure or OpenAI for speech in Studio Settings."
        if speech == "nemotron":
            try:
                speech_ok = _native(root, env)
            except (OSError, ValueError, RuntimeError):
                pass
            speech_action = (
                "Follow docs/local-stt.md; configure executable NEMOTRON_SERVER_BINARY in the "
                "supported build/bin layout, NEMOTRON_MODEL_PATH and loopback NEMOTRON_URL."
            )
        elif speech == "openai":
            speech_ok = populated("OPENAI_API_KEY")
            speech_action = "Set OPENAI_API_KEY for the selected OpenAI transcription provider."
        elif speech == "azure":
            speech_ok = azure("speech")
            speech_action = (
                "Install Azure CLI; set AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, "
                "AZURE_SPEECH_ACCOUNT and AZURE_SPEECH_REGION."
            )
        check(
            "speech",
            speech_ok,
            "Selected speech configuration is present; runtime not verified."
            if speech_ok
            else "Selected speech configuration is incomplete or unsupported.",
            speech_action,
        )
        livekit_ok = populated("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") and _url(
            env.get("LIVEKIT_URL", ""), ("ws", "wss")
        )
        check(
            "livekit",
            livekit_ok,
            "LiveKit URL and credential fields configured; not authenticated."
            if livekit_ok
            else "LiveKit conversation configuration is incomplete or invalid.",
            "Set LIVEKIT_URL (ws/wss), LIVEKIT_API_KEY and LIVEKIT_API_SECRET "
            "in the worktree .env.",
        )
        reasoning_ok = False
        reasoning_action = "Choose a supported reasoning provider in Studio Settings."
        if reasoning == "hermes":
            profile = env.get("HERMES_PROFILE", "default")
            base_url = env.get("HERMES_API_BASE_URL", "http://127.0.0.1:8642")
            reasoning_ok = bool(
                populated("HERMES_API_SERVER_KEY")
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", profile)
                and _hermes_origin(base_url)
            )
            reasoning_action = (
                "Set HERMES_API_SERVER_KEY on the Studio server and configure a valid Hermes "
                "profile with loopback HTTP or HTTPS."
            )
        elif reasoning in ("copilot", "codex"):
            model = env.get("VOICEBOX_LLM_MODEL", "gpt-5.6-luna").strip()
            effort = env.get("VOICEBOX_REASONING_EFFORT", "low").strip()
            reasoning_ok = cli(reasoning) and bool(model and model != "auto" and effort)
            if reasoning == "codex":
                reasoning_ok = (
                    reasoning_ok
                    and platform.system() == "Darwin"
                    and (env.get("VOICEBOX_CODEX_RESTRICTED") == "1")
                )
            reasoning_action = (
                "Install the selected CLI and agents extra; configure explicit model/effort. "
                "Codex additionally requires macOS and restricted-agent consent; "
                "see docs/agent-providers.md."
            )
        elif reasoning == "openai":
            reasoning_ok = populated("OPENAI_API_KEY")
            reasoning_action = "Set OPENAI_API_KEY for the selected OpenAI reasoning provider."
        elif reasoning == "azure":
            reasoning_ok = azure("reasoning")
            reasoning_action = (
                "Install Azure CLI; set AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, "
                "AZURE_OPENAI_ACCOUNT, AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT."
            )
        check(
            "reasoning",
            reasoning_ok,
            "Selected reasoning configuration and CLI (if needed) found; not authenticated."
            if reasoning_ok
            else "Selected reasoning configuration is incomplete or unsupported.",
            reasoning_action,
        )
        if backend == "mlx" and populated("VOICEBOX_VOICE_BUNDLE"):
            try:
                _bundle(_path(env["VOICEBOX_VOICE_BUNDLE"], root, env))
                add(
                    "voice",
                    "pass",
                    "Selected local voice metadata, WAV signature and checksum checked; "
                    "audio decoding remains unverified.",
                )
            except ImportError:
                add("voice", "missing", "Voice validator dependencies are missing.", INSTALL)
            except (OSError, ValueError, RuntimeError, RecursionError):
                add(
                    "voice",
                    "missing",
                    "Selected local voice bundle is missing or invalid.",
                    "Restore the authorized bundle or record and select a voice in Voice library.",
                )
        else:
            check(
                "voice",
                populated("VOICEBOX_PROFILE"),
                "Voicebox profile selector configured; existence/authorization not verified."
                if populated("VOICEBOX_PROFILE")
                else "No authorized voice has been selected.",
                "Record and select a voice in Voice library, or configure an authorized "
                "VOICEBOX_PROFILE / local VOICEBOX_VOICE_BUNDLE. See docs/quickstart.md.",
            )

    add(
        "account-readiness",
        "unverified",
        "Authentication, account entitlement, exact model/effort access and LiveKit connectivity "
        "were not checked. An installed CLI or credential field is not authentication proof.",
        "Verify the selected provider's sign-in and account model access yourself; "
        "see docs/agent-providers.md.",
    )
    add(
        "model-readiness",
        "unverified",
        "Model loading, weight integrity, native runtime compatibility, audio decoding, "
        "available RAM and audible "
        "output were not checked. No model, service or inference was started.",
        "Native model checksum validation remains at explicit setup/start. After setup, explicitly "
        "start Studio and authorize an audition/conversation; see docs/quickstart.md.",
    )
    return report


def exit_code(report: Report) -> int:
    return int(any(item["status"] == "missing" for item in report["checks"]))


def main(root: Path, *, json_output: bool = False) -> None:
    report = run_checks(root)
    if json_output:
        print(json.dumps(report))
    else:
        for item in report["checks"]:
            print(f"{item['status'].upper()} [{item['id']}]: {item['message']}")
            if item["action"]:
                print(f"  Action: {item['action']}")
        print(
            "Configuration incomplete."
            if exit_code(report)
            else "Configuration ready; account and runtime readiness remain unverified."
        )
    raise SystemExit(exit_code(report))
