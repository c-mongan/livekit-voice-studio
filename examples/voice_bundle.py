"""Private, portable reference bundles. No model weights or account credentials."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from livekit.plugins.voicebox.errors import ConfigurationError
from livekit.plugins.voicebox.models import MAX_JSON_BYTES, MAX_WAV_BYTES, VoiceProfile


@dataclass(frozen=True)
class VoiceBundle:
    name: str
    audio: bytes
    transcript: str
    digest: str

    @property
    def profile(self) -> VoiceProfile:
        return VoiceProfile(
            id="local-" + self.digest[:20],
            name=self.name,
            language="en",
            voice_type="cloned",
            default_engine="qwen",
            preset_engine=None,
            sample_count=1,
        )


def bounded_file(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        raise ConfigurationError("Cannot read the configured local voice bundle.") from None
    if len(data) > limit:
        raise ConfigurationError("Local voice bundle exceeds its allocation limit.")
    return data


def load_bundle(directory: Path | str) -> VoiceBundle:
    root = Path(directory).expanduser().resolve()
    try:
        metadata = json.loads(bounded_file(root / "voice.json", MAX_JSON_BYTES))
    except (ValueError, RecursionError):
        raise ConfigurationError("Local voice bundle metadata is invalid.") from None
    if (
        not isinstance(metadata, dict)
        or metadata.get("version") != 1
        or metadata.get("authorized") is not True
        or not isinstance(metadata.get("name"), str)
        or not 1 <= len(metadata["name"].strip()) <= 100
        or not isinstance(metadata.get("transcript"), str)
        or not 1 <= len(metadata["transcript"].strip()) <= 4000
    ):
        raise ConfigurationError("Expected an authorized version-1 local voice bundle.")
    audio = bounded_file(root / "reference.wav", MAX_WAV_BYTES)
    digest = hashlib.sha256(audio).hexdigest()
    if metadata.get("audio_sha256") != digest:
        raise ConfigurationError("Local voice reference checksum does not match its metadata.")
    # Reuse the exact synthesis decoder's reference limits without loading a model.
    from examples.fast_qwen import FastQwenTTS

    try:
        FastQwenTTS._decode_reference(audio)
    except (ValueError, RuntimeError):
        raise ConfigurationError("Local voice reference is not supported audio.") from None
    return VoiceBundle(metadata["name"], audio, metadata["transcript"], digest)


def save_bundle(directory: Path, *, name: str, audio: bytes, transcript: str) -> None:
    """Write a new private directory atomically; never overwrite an existing voice."""
    root = directory.expanduser().absolute()
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.exists():
        raise ConfigurationError("Destination already exists. Choose a new voice bundle directory.")
    temporary = Path(tempfile.mkdtemp(prefix=".voice-import-", dir=root.parent))
    try:
        (temporary / "reference.wav").write_bytes(audio)
        (temporary / "voice.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "name": name,
                    "authorized": True,
                    "transcript": transcript,
                    "audio_sha256": hashlib.sha256(audio).hexdigest(),
                },
                indent=2,
            )
            + "\n"
        )
        for path in temporary.iterdir():
            path.chmod(0o600)
        load_bundle(temporary)
        # A competing import must never be replaced.
        if root.exists():
            raise ConfigurationError("Destination was created during import; no files replaced.")
        temporary.rename(root)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


async def import_voice(args: argparse.Namespace) -> None:
    from examples.fast_qwen import FastQwenTTS

    origin = urlsplit(args.base_url)
    if (
        origin.scheme != "http"
        or origin.hostname not in ("localhost", "127.0.0.1", "::1")
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
    ):
        raise ConfigurationError("Voice import requires the explicitly configured loopback server.")
    if args.output.expanduser().exists():
        raise ConfigurationError("Destination already exists. Choose a new voice bundle directory.")
    provider = FastQwenTTS(
        profile=args.profile,
        model_path=args.model_path,
        base_url=args.base_url,
    )
    try:
        await provider.check_idle()
        profile = await provider.resolve_profile()
        audio, transcript = await provider._fetch_reference(args.profile)
        save_bundle(args.output, name=profile.name, audio=audio, transcript=transcript)
    finally:
        await provider.aclose()


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("VOICEBOX_PROFILE"))
    parser.add_argument("--model-path", default=os.environ.get("VOICEBOX_MLX_MODEL_PATH"))
    parser.add_argument(
        "--base-url", default=os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--authorized", action="store_true")
    args = parser.parse_args()
    if not args.authorized or not args.profile or not args.model_path:
        parser.error("Select --profile, the existing --model-path, and confirm --authorized.")
    try:
        asyncio.run(import_voice(args))
    except ConfigurationError as error:
        parser.exit(1, f"Voice import failed: {error}\n")
    print("Private voice bundle imported. No model loaded, inference run, or reference uploaded.")


if __name__ == "__main__":
    main()
