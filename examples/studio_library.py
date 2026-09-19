"""Private local voices and nonsecret demo settings; browser IDs are never paths."""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from livekit.plugins.voicebox.errors import ConfigurationError

from examples.component_endpoints import (
    DEFAULT_LLM_URL,
    ENDPOINT_PROVIDERS,
    apply_livekit,
    livekit_configuration,
    validate_endpoint,
)
from examples.voice_bundle import load_bundle, save_bundle

MAX_RECORDING_BYTES = 4 * 1024 * 1024
GUIDED_TEXT = (
    "Hello. This is a recording of my natural voice. I am speaking clearly at a comfortable "
    "pace, with a little space between sentences. This sample will help my local assistant "
    "speak with a voice like mine."
)
PRESETS = {
    "copilot": ("gpt-5.6-luna", "low"),
    "codex": ("gpt-5.6-luna", "low"),
    "azure": ("gpt-4.1-nano", "none"),
    "openai": ("gpt-4.1-mini", "none"),
}


class LibraryError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def private_json(path: Path, value: dict[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def recording_duration(audio: bytes, *, enrollment: bool = True) -> float:
    if not audio or len(audio) > MAX_RECORDING_BYTES:
        raise LibraryError("Recording must be a WAV file no larger than 4 MiB.", 413)
    try:
        with sf.SoundFile(io.BytesIO(audio)) as source:
            if (
                source.format not in ("WAV", "WAVEX")
                or not 8000 <= source.samplerate <= 96000
                or not 1 <= source.channels <= 2
                or not 0 < source.frames <= source.samplerate * 30
            ):
                raise LibraryError("Record 5–30 seconds of mono or stereo audio.")
            duration = source.frames / source.samplerate
            if enrollment and duration < 5:
                raise LibraryError("Record at least 5 seconds so the voice has enough reference.")
            samples = source.read(dtype="float32", always_2d=True)
        if not np.isfinite(samples).all():
            raise LibraryError("Recording contains invalid audio samples.")
        if enrollment:
            level = np.sqrt(np.mean(np.square(samples, dtype=np.float64)))
            if level < 0.002:
                raise LibraryError("The recording is too quiet. Move closer to the microphone.")
            if np.mean(np.abs(samples) >= 0.995) > 0.03:
                raise LibraryError("The recording is clipping. Lower the microphone level.")
        return float(duration)
    except (sf.LibsndfileError, ValueError, OverflowError):
        raise LibraryError("The recording is not a supported WAV file.") from None


class StudioLibrary:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.voices = self.root / "voices"
        self.voices.mkdir(exist_ok=True, mode=0o700)
        self.settings_path = self.root / "settings.json"
        self._configured_livekit = livekit_configuration.capture(os.environ)

    def voice_path(self, identifier: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise LibraryError("Voice not found.", 404)
        path = self.voices / identifier
        if path.is_symlink() or not path.is_dir():
            raise LibraryError("Voice not found.", 404)
        if any((path / name).is_symlink() for name in ("voice.json", "reference.wav")):
            raise LibraryError("Voice bundle cannot contain external links.")
        return path

    def settings(self) -> dict[str, Any]:
        if not self.settings_path.exists():
            return {
                "sttProvider": "nemotron",
                "llmProvider": "ollama",
                "llmModel": "qwen3:1.7b",
                "reasoningEffort": "none",
                "livekitMode": "local",
                "llmBaseUrl": DEFAULT_LLM_URL,
                "voiceId": None,
                "codexRestrictedApproved": False,
            }
        try:
            with self.settings_path.open("rb") as handle:
                raw = handle.read(4097)
            if len(raw) > 4096:
                raise ValueError
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("codexRestrictedApproved", False)
                data.setdefault("livekitMode", "configured")
                data.setdefault("llmBaseUrl", DEFAULT_LLM_URL)
            self._validate_settings(data)
            return dict(data)
        except (OSError, ValueError, TypeError, LibraryError):
            raise LibraryError(
                "Saved Studio settings are invalid; restore the private settings file."
            ) from None

    def _validate_settings(self, settings: dict[str, Any]) -> None:
        required = {
            "sttProvider",
            "llmProvider",
            "llmModel",
            "reasoningEffort",
            "voiceId",
            "codexRestrictedApproved",
            "livekitMode",
            "llmBaseUrl",
        }
        if not isinstance(settings, dict) or set(settings) != required:
            raise LibraryError("Unsupported settings fields.")
        if settings["sttProvider"] not in ("nemotron", "azure", "openai"):
            raise LibraryError("Choose a supported speech recognition provider.")
        provider = settings["llmProvider"]
        if not isinstance(provider, str) or provider not in (*PRESETS, *ENDPOINT_PROVIDERS):
            raise LibraryError("Choose a supported reasoning provider.")
        if (
            provider in PRESETS
            and (settings["llmModel"], settings["reasoningEffort"]) != PRESETS[provider]
        ):
            raise LibraryError("The selected model and reasoning preset is not supported.")
        if settings["livekitMode"] not in ("local", "configured"):
            raise LibraryError("Choose local or configured LiveKit.")
        try:
            validate_endpoint(provider, settings["llmBaseUrl"], settings["llmModel"])
            if provider in ENDPOINT_PROVIDERS and settings["reasoningEffort"] != "none":
                raise ValueError("Local/custom models require no reasoning preset.")
        except ValueError as error:
            raise LibraryError(str(error)) from None
        if type(settings["codexRestrictedApproved"]) is not bool:
            raise LibraryError("Restricted agent consent must be a boolean.")
        if provider == "codex" and not settings["codexRestrictedApproved"]:
            raise LibraryError(
                "Confirm the restricted Codex workspace and global-instruction boundary."
            )
        identifier = settings["voiceId"]
        if identifier is not None:
            if not isinstance(identifier, str):
                raise LibraryError("Choose a valid voice.")
            self.voice_path(identifier)

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings()
        if not isinstance(updates, dict) or any(key not in settings for key in updates):
            raise LibraryError("Only nonsecret provider and voice settings can be changed here.")
        if "voiceId" in updates and updates["voiceId"] is None and settings["voiceId"] is not None:
            raise LibraryError("Choose another voice rather than clearing the selected voice.")
        settings.update(updates)
        if settings["llmProvider"] in ("azure", "openai") and settings["reasoningEffort"] == "":
            settings["reasoningEffort"] = "none"
        if settings["llmProvider"] != "codex":
            settings["codexRestrictedApproved"] = False
        self._validate_settings(settings)
        private_json(self.settings_path, settings)
        return settings

    def _voice(self, identifier: str) -> dict[str, Any]:
        try:
            voice = load_bundle(self.voice_path(identifier))
            duration = recording_duration(voice.audio, enrollment=False)
        except ConfigurationError:
            raise LibraryError(
                "A saved voice bundle is invalid. Restore it from your private backup."
            ) from None
        return {
            "id": identifier,
            "name": voice.name,
            "durationSeconds": duration,
            "source": "local",
            "selected": self.settings()["voiceId"] == identifier,
        }

    def list_voices(self) -> list[dict[str, Any]]:
        paths = sorted(p for p in self.voices.iterdir() if re.fullmatch(r"[a-f0-9]{32}", p.name))
        if len(paths) > 64:
            raise LibraryError("The local library exceeds the 64-voice limit.")
        return [self._voice(path.name) for path in paths]

    def create_voice(
        self, name: str, transcript: str, audio: bytes, authorized: bool
    ) -> dict[str, Any]:
        if (
            authorized is not True
            or not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 100
            or not isinstance(transcript, str)
            or not 1 <= len(transcript.strip()) <= 4000
        ):
            raise LibraryError(
                "Confirm permission, name the voice, and provide the exact spoken words."
            )
        if len(self.list_voices()) >= 64:
            raise LibraryError("The library holds at most 64 voices. Remove an unused voice first.")
        recording_duration(audio)
        identifier = secrets.token_hex(16)
        save_bundle(
            self.voices / identifier, name=name.strip(), audio=audio, transcript=transcript.strip()
        )
        return self._voice(identifier)

    def seed_bundle(self, path: Path) -> None:
        """Copy only an explicitly configured existing voice into a new library once."""
        if self.settings_path.exists() or self.list_voices():
            return
        voice = load_bundle(path)
        identifier = secrets.token_hex(16)
        save_bundle(
            self.voices / identifier,
            name=voice.name,
            audio=voice.audio,
            transcript=voice.transcript,
        )
        self.update_settings({"voiceId": identifier})

    def rename_voice(self, identifier: str, name: str) -> dict[str, Any]:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise LibraryError("Voice name must be 1–100 characters.")
        path = self.voice_path(identifier)
        voice = load_bundle(path)
        private_json(
            path / "voice.json",
            {
                "version": 1,
                "name": name.strip(),
                "authorized": True,
                "transcript": voice.transcript,
                "audio_sha256": voice.digest,
            },
        )
        return self._voice(identifier)

    def delete_voice(self, identifier: str) -> None:
        path = self.voice_path(identifier)
        if self.settings()["voiceId"] == identifier:
            raise LibraryError("Choose another voice before deleting the selected voice.", 409)
        if {entry.name for entry in path.iterdir()} != {"voice.json", "reference.wav"}:
            raise LibraryError("Voice folder contains unexpected files; no files were deleted.")
        # Only these two validated files are removed; never recurse over user paths.
        (path / "reference.wav").unlink()
        (path / "voice.json").unlink()
        path.rmdir()

    def read_audio(self, identifier: str) -> bytes:
        return load_bundle(self.voice_path(identifier)).audio

    def apply_environment(self) -> None:
        settings = self.settings()
        apply_livekit(settings["livekitMode"], os.environ, self._configured_livekit)
        os.environ["VOICEBOX_LLM_BASE_URL"] = settings["llmBaseUrl"]
        os.environ["VOICEBOX_STT_PROVIDER"] = settings["sttProvider"]
        os.environ["VOICEBOX_LLM_PROVIDER"] = settings["llmProvider"]
        os.environ["VOICEBOX_LLM_MODEL"] = settings["llmModel"]
        os.environ["VOICEBOX_REASONING_EFFORT"] = settings["reasoningEffort"]
        os.environ["VOICEBOX_CODEX_RESTRICTED"] = (
            "1" if settings["codexRestrictedApproved"] else "0"
        )
        if settings["voiceId"] is not None:
            os.environ["VOICEBOX_VOICE_BUNDLE"] = str(self.voice_path(settings["voiceId"]))
            os.environ["VOICEBOX_TTS_BACKEND"] = "mlx"
