import io
import json

import numpy as np
import pytest
import soundfile as sf

from examples.studio_library import LibraryError, StudioLibrary


def recording(seconds=6, value=0.1, channels=1, rate=24000):
    output = io.BytesIO()
    signal = np.sin(np.arange(int(seconds * rate)) * 2 * np.pi * 180 / rate) * value
    audio = np.tile(signal[:, None], (1, channels)).astype("float32")
    sf.write(output, audio, rate, format="WAV", subtype="PCM_16")
    return output.getvalue()


def test_create_select_rename_and_delete_are_private(tmp_path):
    library = StudioLibrary(tmp_path)
    first = library.create_voice("My voice", "The exact words I recorded.", recording(), True)
    assert len(first["id"]) == 32
    assert first["durationSeconds"] == 6
    assert (tmp_path / "voices" / first["id"]).stat().st_mode & 0o777 == 0o700
    assert "transcript" not in first
    assert "path" not in first
    settings = library.update_settings({"voiceId": first["id"]})
    assert settings["voiceId"] == first["id"]
    with pytest.raises(LibraryError, match="selected"):
        library.delete_voice(first["id"])
    with pytest.raises(LibraryError, match="clearing"):
        library.update_settings({"voiceId": None})
    library.rename_voice(first["id"], "Natural")
    assert library.list_voices()[0]["name"] == "Natural"
    second = library.create_voice("Second", "Other exact words.", recording(value=0.2), True)
    library.update_settings({"voiceId": second["id"]})
    library.delete_voice(first["id"])
    assert [voice["id"] for voice in library.list_voices()] == [second["id"]]
    assert library.read_audio(second["id"]) == recording(value=0.2)


@pytest.mark.parametrize(
    "name,text,audio,authorized",
    [
        ("", "words", recording(), True),
        ("x" * 101, "words", recording(), True),
        ("Voice", "", recording(), True),
        ("Voice", "words", recording(), False),
        ("Voice", "words", recording(seconds=2), True),
        ("Voice", "words", recording(seconds=31), True),
        ("Voice", "words", recording(value=0), True),
        ("Voice", "words", b"not audio", True),
        ("Voice", "words", b"x" * (4 * 1024 * 1024 + 1), True),
    ],
)
def test_bad_recordings_leave_no_partial_voice(tmp_path, name, text, audio, authorized):
    library = StudioLibrary(tmp_path)
    with pytest.raises(LibraryError):
        library.create_voice(name, text, audio, authorized)
    assert library.list_voices() == []


@pytest.mark.parametrize("identifier", ["../secret", "/tmp/voice", "", "a" * 33, "xyz"])
def test_voice_ids_are_not_paths(tmp_path, identifier):
    library = StudioLibrary(tmp_path)
    with pytest.raises(LibraryError):
        library.read_audio(identifier)


@pytest.mark.parametrize(
    "updates",
    [
        {"llmProvider": "unknown"},
        {"sttProvider": "evil"},
        {"apiKey": "secret"},
        {"llmProvider": "copilot", "llmModel": "auto", "reasoningEffort": "low"},
        {"llmProvider": "codex", "llmModel": "gpt-5.6-luna", "reasoningEffort": "high"},
        {"voiceId": "f" * 32},
    ],
)
def test_settings_reject_unsupported_or_secret_inputs(tmp_path, updates):
    library = StudioLibrary(tmp_path)
    original = library.settings()
    with pytest.raises(LibraryError):
        library.update_settings(updates)
    assert library.settings() == original


def test_independent_provider_choices_persist(tmp_path):
    library = StudioLibrary(tmp_path)
    settings = library.update_settings(
        {
            "sttProvider": "azure",
            "llmProvider": "codex",
            "llmModel": "gpt-5.6-luna",
            "reasoningEffort": "low",
            "codexRestrictedApproved": True,
        }
    )
    assert settings["sttProvider"] == "azure"
    assert StudioLibrary(tmp_path).settings() == settings
    assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600
    assert "secret" not in json.dumps(settings)


def test_codex_requires_explicit_restricted_agent_consent(tmp_path):
    library = StudioLibrary(tmp_path)
    with pytest.raises(LibraryError, match="restricted"):
        library.update_settings(
            {
                "llmProvider": "codex",
                "llmModel": "gpt-5.6-luna",
                "reasoningEffort": "low",
            }
        )


def test_local_defaults_are_explicit_not_a_silent_azure_fallback(tmp_path):
    settings = StudioLibrary(tmp_path).settings()
    assert settings["sttProvider"] == "nemotron"
    assert settings["llmProvider"] == "copilot"
    assert settings["llmModel"] == "gpt-5.6-luna"
    assert settings["reasoningEffort"] == "low"


def test_azure_preset_accepts_no_effort_and_revokes_codex_consent(tmp_path):
    library = StudioLibrary(tmp_path)
    library.update_settings(
        {
            "llmProvider": "codex",
            "llmModel": "gpt-5.6-luna",
            "reasoningEffort": "low",
            "codexRestrictedApproved": True,
        }
    )
    settings = library.update_settings(
        {
            "llmProvider": "azure",
            "llmModel": "gpt-4.1-nano",
            "reasoningEffort": "",
        }
    )
    assert settings["reasoningEffort"] == "none"
    assert settings["codexRestrictedApproved"] is False


def test_symlink_voice_directory_is_not_followed(tmp_path):
    library = StudioLibrary(tmp_path / "library")
    outside = tmp_path / "outside"
    outside.mkdir()
    (library.voices / ("a" * 32)).symlink_to(outside)
    with pytest.raises(LibraryError):
        library.read_audio("a" * 32)


def test_deletion_preserves_unexpected_user_files(tmp_path):
    library = StudioLibrary(tmp_path)
    voice = library.create_voice("Test", "Exact words.", recording(), True)
    path = library.voice_path(voice["id"])
    (path / "notes.txt").write_text("User notes")
    with pytest.raises(LibraryError, match="unexpected files"):
        library.delete_voice(voice["id"])
    assert (path / "notes.txt").is_file()
    assert (path / "reference.wav").is_file()
