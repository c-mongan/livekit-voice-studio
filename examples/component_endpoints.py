"""Nonsecret component configuration and bounded, read-only endpoint checks."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Mapping, MutableMapping
from typing import Any
from urllib.parse import urlsplit

DEFAULT_LLM_URL = "http://127.0.0.1:11434/v1"
ENDPOINT_PROVIDERS = ("ollama", "openai-compatible")
LOCAL_LIVEKIT = {
    "LIVEKIT_URL": "ws://127.0.0.1:7880",
    "LIVEKIT_API_KEY": "devkey",
    "LIVEKIT_API_SECRET": "secret",
}


class LiveKitConfiguration:
    """Process-owned credentials captured once after dotenv, before any local override."""

    def __init__(self) -> None:
        self.original: dict[str, str] | None = None

    def capture(self, env: Mapping[str, str]) -> dict[str, str]:
        if self.original is None:
            self.original = {name: env[name] for name in LOCAL_LIVEKIT if name in env}
        return dict(self.original)


livekit_configuration = LiveKitConfiguration()


def is_loopback_url(url: str) -> bool:
    try:
        hostname = urlsplit(url).hostname
        return hostname == "localhost" or bool(
            hostname and ipaddress.ip_address(hostname).is_loopback
        )
    except ValueError:
        return False


def validate_endpoint(provider: str, url: Any, model: Any, effort: Any = "none") -> str:
    if (
        not isinstance(model, str)
        or not 1 <= len(model) <= 200
        or model != model.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in model)
        or effort != "none"
    ):
        raise ValueError("Choose a model name of 1–200 characters and no reasoning preset.")
    if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() for c in url):
        raise ValueError("Choose a valid HTTP(S) model endpoint.")
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port == 0
            or any(ord(c) < 32 or ord(c) == 127 for c in url)
            or "?" in url
            or "#" in url
        ):
            raise ValueError
    except ValueError:
        raise ValueError(
            "Use an HTTP(S) endpoint without credentials, query or fragment."
        ) from None
    if provider == "ollama" and not is_loopback_url(url):
        raise ValueError("Ollama requires a loopback endpoint on this computer.")
    return url.rstrip("/")


def apply_livekit(mode: str, env: MutableMapping[str, str], configured: Mapping[str, str]) -> None:
    if mode not in ("local", "configured"):
        raise ValueError("Choose local or configured LiveKit.")
    for name in LOCAL_LIVEKIT:
        value = LOCAL_LIVEKIT.get(name) if mode == "local" else configured.get(name)
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    env["VOICEBOX_LIVEKIT_MODE"] = mode


async def list_llm_models(provider: str, url: str, api_key: str = "") -> list[str]:
    """Read the bounded model catalog; never pull or generate."""
    import aiohttp

    endpoint = validate_endpoint(provider, url, "catalog")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5), trust_env=False
        ) as client:
            async with client.get(
                endpoint + "/models", headers=headers, allow_redirects=False
            ) as response:
                if response.status in (401, 403):
                    raise RuntimeError(
                        "The reasoning endpoint rejected authentication. Check its server-side "
                        "credential and model access, then retry."
                    )
                if response.status == 404:
                    raise RuntimeError(
                        "The reasoning endpoint has no model-list route. In Settings, check "
                        "the API base address, including /v1 when required, then retry."
                    )
                if response.status >= 500:
                    raise RuntimeError(
                        "The reasoning service is unavailable. Restart or repair the selected "
                        "service, then retry. Studio has not switched providers."
                    )
                if response.status != 200:
                    raise ValueError
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > 256 * 1024:
                        raise ValueError
                payload = json.loads(raw)
                entries = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(entries, list):
                    raise ValueError
                models: set[str] = set()
                for entry in entries:
                    model = entry.get("id") if isinstance(entry, dict) else None
                    if not isinstance(model, str):
                        continue
                    try:
                        validate_endpoint(provider, url, model)
                    except ValueError:
                        continue
                    models.add(model)
                return sorted(models)
    except (aiohttp.ClientError, TimeoutError, ValueError, UnicodeError):
        raise RuntimeError(
            "The reasoning endpoint could not be verified. "
            + (
                "Start Ollama with ollama serve, then check its API address in Settings. "
                if provider == "ollama"
                else "Check the API address, running service and server-side credential. "
            )
            + "Retry when ready; Studio never falls back to another provider."
        ) from None


async def check_llm_endpoint(provider: str, url: str, model: str, api_key: str = "") -> None:
    """Require the exact model without downloading or switching providers."""
    validate_endpoint(provider, url, model)
    if model not in await list_llm_models(provider, url, api_key):
        raise RuntimeError(
            "The selected model is unavailable at the reasoning endpoint. "
            + (
                "Run ollama list and choose an installed chat model in Settings. "
                if provider == "ollama"
                else "Check the server's model list and update the model in Settings. "
            )
            + "Studio never downloads or falls back."
        )
