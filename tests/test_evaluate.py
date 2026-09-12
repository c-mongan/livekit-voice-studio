import json
import subprocess

import pytest

from tools.evaluate import evaluate, parse_spoken


def test_spoken_summary_keeps_config_and_all_samples():
    output = (
        "\n".join(
            [
                json.dumps({"turn": i + 1, "seconds": value, "nonzero_samples": 2500})
                for i, value in enumerate([4.0, 3.0, 5.0])
            ]
        )
        + "\n"
        + json.dumps(
            {
                "stt": "nemotron",
                "llm": "copilot",
                "speech_end_to_reply": {"turns": 3},
                "human_accent_evaluation": False,
            }
        )
    )
    result = parse_spoken(output, 3)
    assert result["samples_seconds"] == [4.0, 3.0, 5.0]
    assert result["median_seconds"] == 4.0
    assert result["slowest_seconds"] == 5.0
    assert result["config"] == {"stt": "nemotron", "llm": "copilot"}
    assert "p90" not in result


@pytest.mark.parametrize("output", ["", '{"turn":1,"seconds":-2}', '{"turn":1,"seconds":0.2}'])
def test_incomplete_spoken_results_are_not_success(output):
    with pytest.raises(ValueError):
        parse_spoken(output, 3)


def test_offline_report_is_structured_and_has_no_latency_claim(tmp_path, monkeypatch):
    def run(command, **kwargs):
        junit = next(x.split("=", 1)[1] for x in command if x.startswith("--junitxml="))
        from pathlib import Path

        Path(junit).write_text(
            '<testsuites><testsuite tests="20" failures="0" errors="0" skipped="0"/></testsuites>'
        )
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        return subprocess.CompletedProcess(command, 0, "private local output not for report", "")

    monkeypatch.setattr("tools.evaluate.subprocess.run", run)
    result = evaluate(mode="offline", turns=3, audio=None)
    assert result["passed"] is True
    assert result["functional"] == {"tests": 20, "failures": 0, "errors": 0, "skipped": 0}
    assert result["latency"] is None
    assert result["human_evaluation"] is False
    assert "private local output" not in json.dumps(result)


def test_failed_suite_is_preserved_as_a_failed_outcome(monkeypatch):
    monkeypatch.setattr(
        "tools.evaluate.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "secret body", ""),
    )
    result = evaluate(mode="offline", turns=3, audio=None)
    assert result["passed"] is False
    assert result["functional"] is None
    assert result["errors"]
    assert "secret body" not in json.dumps(result)


def test_live_mode_requires_explicit_input_before_subprocess(monkeypatch):
    called = False

    def run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("tools.evaluate.subprocess.run", run)
    with pytest.raises(ValueError, match="audio"):
        evaluate(mode="spoken", turns=3, audio=None)
    assert called is False
