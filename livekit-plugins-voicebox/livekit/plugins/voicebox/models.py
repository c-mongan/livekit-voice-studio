from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .errors import ConfigurationError, VoiceboxAPIError

MAX_TEXT_CHARS = 800
MAX_WAV_BYTES = 16 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
OUTPUT_RATES = frozenset({8000, 16000, 22050, 24000, 32000, 44100, 48000})
QWEN_LANGUAGES = frozenset({"zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"})


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise VoiceboxAPIError("Voicebox returned an invalid JSON object.")
    return value


def _string(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value or len(value) > 500:
        raise VoiceboxAPIError(f"Voicebox returned an invalid {key} field.")
    return value


def _optional_string(data: Mapping[str, object], key: str) -> str | None:
    return None if data.get(key) is None else _string(data, key)


def _boolean(data: Mapping[str, object], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise VoiceboxAPIError(f"Voicebox returned an invalid {key} field.")
    return value


@dataclass(frozen=True)
class VoiceProfile:
    id: str
    name: str
    language: str
    voice_type: str
    default_engine: str | None
    preset_engine: str | None
    sample_count: int | None = None

    @classmethod
    def parse(cls, value: object) -> VoiceProfile:
        data = _object(value)
        count = data.get("sample_count")
        if count is not None and (type(count) is not int or count < 0):
            raise VoiceboxAPIError("Voicebox returned an invalid sample_count field.")
        return cls(
            id=_string(data, "id"),
            name=_string(data, "name"),
            language=_string(data, "language"),
            voice_type=_string(data, "voice_type"),
            default_engine=_optional_string(data, "default_engine"),
            preset_engine=_optional_string(data, "preset_engine"),
            sample_count=count,
        )


@dataclass(frozen=True)
class HealthResult:
    """Connectivity only, never a selected-model readiness guarantee."""

    ok: bool
    status: str | None
    model_loaded: bool | None
    raw: Mapping[str, Any]

    @classmethod
    def parse(cls, value: object) -> HealthResult:
        data = _object(value)
        loaded = _boolean(data, "model_loaded") if "model_loaded" in data else None
        return cls(True, _optional_string(data, "status"), loaded, MappingProxyType(data))


@dataclass(frozen=True)
class ModelReadiness:
    model_name: str
    downloaded: bool
    loaded: bool
    downloading: bool

    @classmethod
    def parse(cls, value: object) -> ModelReadiness:
        data = _object(value)
        return cls(
            _string(data, "model_name"),
            _boolean(data, "downloaded"),
            _boolean(data, "loaded"),
            _boolean(data, "downloading"),
        )


@dataclass(frozen=True)
class GenerationOptions:
    profile: str
    engine: str | None = "qwen"
    model_size: str | None = "0.6B"
    language: str = "en"
    instruct: str | None = None

    def validate(self) -> None:
        if not self.profile.strip() or len(self.profile) > 500:
            raise ConfigurationError(
                "Choose a nonempty profile ID or name (at most 500 characters)."
            )
        if self.engine not in (None, "qwen") or self.model_size not in (None, "0.6B"):
            raise ConfigurationError(
                "This prototype supports only engine='qwen', model_size='0.6B'."
            )
        if self.language not in QWEN_LANGUAGES:
            raise ConfigurationError(
                "Unsupported Qwen language; use zh/en/ja/ko/de/fr/ru/pt/es/it."
            )
        if self.instruct is not None and len(self.instruct) > 500:
            raise ConfigurationError("Delivery instruction exceeds 500 characters.")

    def effective(self, profile: VoiceProfile) -> GenerationOptions:
        self.validate()
        engine = self.engine or profile.default_engine or profile.preset_engine or "qwen"
        if engine != "qwen":
            raise ConfigurationError("The selected profile defaults to an unsupported engine.")
        if profile.voice_type != "cloned" or profile.preset_engine is not None:
            raise ConfigurationError(
                "Qwen 0.6B requires a cloned profile, not a preset/designed voice."
            )
        if profile.sample_count is None or profile.sample_count < 1:
            raise ConfigurationError("The selected cloned profile needs a usable reference sample.")
        return GenerationOptions(
            profile.id, engine, self.model_size or "0.6B", self.language, self.instruct
        )


def validate_text(text: str) -> None:
    if not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise ConfigurationError(
            f"Synthesis requires 1-{MAX_TEXT_CHARS} characters of nonblank text."
        )


@dataclass
class RequestTimings:
    """Monotonic elapsed seconds; no text, audio, or profile identifiers."""

    profile_resolution: float = 0.0
    queue_wait: float = 0.0
    http_first_byte: float | None = None
    http_complete: float | None = None
    decode: float = 0.0
