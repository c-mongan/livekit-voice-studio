import json
from unittest.mock import AsyncMock

import pytest
from livekit.plugins.voicebox.errors import ConfigurationError
from test_fast_qwen import make_wav, snapshot  # noqa: F401 - shared complete-snapshot fixture

from examples.fast_qwen import FastQwenTTS
from examples.voice_bundle import load_bundle, save_bundle


def test_private_bundle_round_trip_and_no_overwrite(tmp_path):
    root = tmp_path / "voice"
    save_bundle(
        root, name="Authorized test voice", audio=make_wav(), transcript="Synthetic reference."
    )
    bundle = load_bundle(root)
    assert bundle.name == "Authorized test voice"
    assert bundle.audio == make_wav()
    assert bundle.profile.sample_count == 1
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "voice.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ConfigurationError, match="already exists"):
        save_bundle(root, name="Replace", audio=make_wav(), transcript="Different.")


@pytest.mark.parametrize("change", ["checksum", "version", "authorization", "transcript", "audio"])
def test_invalid_bundle_fails_closed(tmp_path, change):
    root = tmp_path / "voice"
    save_bundle(root, name="Test voice", audio=make_wav(), transcript="Synthetic reference.")
    path = root / "voice.json"
    data = json.loads(path.read_text())
    if change == "checksum":
        data["audio_sha256"] = "not-the-reference"
    elif change == "version":
        data["version"] = 2
    elif change == "authorization":
        data["authorized"] = False
    elif change == "transcript":
        data["transcript"] = "x" * 4001
    else:
        (root / "reference.wav").write_bytes(b"broken")
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigurationError):
        load_bundle(root)


async def test_local_bundle_never_contacts_voicebox(tmp_path, snapshot, monkeypatch):  # noqa: F811
    root = tmp_path / "voice"
    save_bundle(root, name="Test voice", audio=make_wav(), transcript="Synthetic reference.")
    provider = FastQwenTTS(
        profile="Local voice",
        model_path=snapshot,
        voice_bundle=root,
        base_url="http://127.0.0.1:1",
    )
    http = AsyncMock(side_effect=AssertionError("No Voicebox request is allowed."))
    monkeypatch.setattr(provider._client, "health", http)
    monkeypatch.setattr(provider._client, "resolve_profile", http)
    monkeypatch.setattr(provider._client, "model_readiness", http)
    monkeypatch.setattr(provider._client, "check_idle", http)
    try:
        assert (await provider.health()).ok
        assert (await provider.resolve_profile()).name == "Test voice"
        readiness = await provider.model_readiness()
        assert readiness.downloaded and not readiness.loaded
        await provider.check_idle()
        assert await provider._fetch_reference("Local voice") == (
            make_wav(),
            "Synthetic reference.",
        )
        http.assert_not_awaited()
    finally:
        await provider.aclose()


def test_failed_import_leaves_no_partial_bundle(tmp_path):
    with pytest.raises(ConfigurationError):
        save_bundle(tmp_path / "voice", name="Test", audio=b"corrupt", transcript="Reference")
    assert list(tmp_path.iterdir()) == []
