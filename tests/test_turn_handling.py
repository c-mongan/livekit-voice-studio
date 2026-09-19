import pytest


def test_default_turn_handling_keeps_existing_contract():
    from examples.turn_handling import configured_turn_handling

    assert configured_turn_handling({}) == {
        "turn_detection": "vad",
        "interruption": {"mode": "vad"},
        "preemptive_generation": {"enabled": False},
    }


@pytest.mark.parametrize("value", ["", "cloud", "audio", "VAD", "/private/secret"])
def test_invalid_turn_handling_is_rejected_without_echoing_input(value):
    from examples.turn_handling import configured_turn_handling

    with pytest.raises(ValueError, match="VOICEBOX_TURN_DETECTION must be vad or audio-local"):
        configured_turn_handling({"VOICEBOX_TURN_DETECTION": value})


def test_audio_local_explicitly_initializes_local_runtime(monkeypatch):
    from livekit import local_inference

    from examples.turn_handling import configured_turn_handling

    initialized = []
    monkeypatch.setattr(local_inference, "init_eot", lambda: initialized.append(True))
    result = configured_turn_handling({"VOICEBOX_TURN_DETECTION": "audio-local"})
    assert result["turn_detection"].model == "turn-detector-v1-mini"
    assert initialized == [True]
    assert result["interruption"] == {"mode": "vad"}
    assert result["preemptive_generation"] == {"enabled": False}


def test_audio_local_unavailable_fails_with_sanitized_action(monkeypatch):
    from livekit import local_inference

    from examples.turn_handling import configured_turn_handling

    def fail():
        raise RuntimeError("/private/model-secret")

    monkeypatch.setattr(local_inference, "init_eot", fail)
    with pytest.raises(RuntimeError, match="use VOICEBOX_TURN_DETECTION=vad") as error:
        configured_turn_handling({"VOICEBOX_TURN_DETECTION": "audio-local"})
    assert "secret" not in str(error.value)
