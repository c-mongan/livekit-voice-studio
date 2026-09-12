import pytest
from livekit.plugins.voicebox.errors import ConfigurationError, VoiceboxAPIError
from livekit.plugins.voicebox.models import GenerationOptions, VoiceProfile, validate_text


def test_profile_extra_fields(profile):
    parsed = VoiceProfile.parse(dict(profile, future=True))
    assert parsed.id == profile["id"]
    assert parsed.sample_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", None),
        ("name", 123),
        ("sample_count", -1),
        ("sample_count", True),
        ("voice_type", None),
        ("language", None),
    ],
)
def test_bad_profile(profile, field, value):
    with pytest.raises(VoiceboxAPIError):
        VoiceProfile.parse(dict(profile, **{field: value}))


@pytest.mark.parametrize(
    "changes",
    [
        {"engine": "kokoro"},
        {"model_size": "1.7B"},
        {"language": "he"},
        {"profile": ""},
        {"instruct": "x" * 501},
    ],
)
def test_bad_options(changes):
    with pytest.raises(ConfigurationError):
        GenerationOptions(**dict({"profile": "voice"}, **changes)).validate()


def test_effective_defaults(profile):
    options = GenerationOptions("Test Voice", engine=None, model_size=None)
    result = options.effective(VoiceProfile.parse(profile))
    assert result.engine == "qwen"
    assert result.model_size == "0.6B"
    assert result.profile == "voice-1"


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_count": 0},
        {"voice_type": "preset", "preset_engine": "kokoro"},
        {"voice_type": "designed"},
        {"default_engine": "chatterbox"},
    ],
)
def test_incompatible_profiles(profile, changes):
    with pytest.raises(ConfigurationError):
        GenerationOptions("voice", engine=None).effective(
            VoiceProfile.parse(dict(profile, **changes))
        )


@pytest.mark.parametrize("text", ["", " ", "x" * 801])
def test_text_bounds(text):
    with pytest.raises(ConfigurationError):
        validate_text(text)
