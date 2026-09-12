"""Bounded reproducible checks; offline results never masquerade as voice-quality scores."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from benchmarks.conversation_stats import summarize

ROOT = Path(__file__).resolve().parents[1]


def parse_spoken(output: str, turns: int) -> dict[str, Any]:
    samples: list[float] = []
    config: dict[str, str] | None = None
    for line in output.splitlines():
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(value, dict):
            continue
        if "turn" in value:
            seconds = value.get("seconds")
            nonzero = value.get("nonzero_samples")
            if (
                value["turn"] != len(samples) + 1
                or not isinstance(seconds, (float, int))
                or isinstance(seconds, bool)
                or not math.isfinite(seconds)
                or seconds < 0
                or not isinstance(nonzero, int)
                or isinstance(nonzero, bool)
                or nonzero < 2400
            ):
                raise ValueError("Invalid spoken measurement.")
            samples.append(float(seconds))
        if "speech_end_to_reply" in value:
            stt, llm = value.get("stt"), value.get("llm")
            if (
                stt not in ("nemotron", "azure", "openai")
                or llm not in ("copilot", "codex", "azure", "openai")
                or value.get("human_accent_evaluation") is not False
            ):
                raise ValueError("Unknown spoken evaluation configuration.")
            config = {"stt": stt, "llm": llm}
    if len(samples) != turns or config is None:
        raise ValueError("Spoken run did not produce every requested measurement.")
    return {
        **summarize(samples),
        "samples_seconds": samples,
        "config": config,
        "measure": "speech-end to first nonzero remote audio",
        "input": "same explicitly supplied fixture repeated",
        "turn_detection": "local VAD",
        "speculative_reasoning": False,
    }


def provenance() -> dict[str, Any]:
    versions: dict[str, str] = {}
    for package in ("livekit-agents", "livekit", "mlx-audio", "github-copilot-sdk"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=5
    )
    return {
        "source_commit": commit.stdout.strip() if commit.returncode == 0 else "unavailable",
        "source_modified": bool(status.stdout.strip()) if status.returncode == 0 else None,
        "python": platform.python_version(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "packages": versions,
    }


def evaluate(*, mode: str, turns: int, audio: Path | None) -> dict[str, Any]:
    if mode not in ("offline", "spoken") or not 1 <= turns <= 5:
        raise ValueError("Choose offline or spoken mode, with 1–5 turns.")
    if mode == "spoken" and (audio is None or not audio.is_file()):
        raise ValueError("Spoken mode requires an explicitly chosen authorized audio fixture.")
    result: dict[str, Any] = {
        "schema_version": 1,
        "mode": mode,
        "passed": False,
        "requested_turns": turns if mode == "spoken" else 0,
        "human_evaluation": False,
        "functional": None,
        "latency": None,
        "errors": [],
    }
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", OTEL_SDK_DISABLED="true")
    with tempfile.TemporaryDirectory(prefix="voicebox-eval-") as temporary:
        junit = Path(temporary) / "results.xml"
        command = [sys.executable, "-m", "pytest", "-q", "--tb=short", f"--junitxml={junit}"]
        if mode == "offline":
            command.extend(["-m", "not integration"])
        else:
            assert audio is not None
            env.update(
                STUDIO_SPOKEN_INTEGRATION="1",
                STUDIO_TEST_AUDIO=str(audio.resolve()),
                STUDIO_TEST_TURNS=str(turns),
            )
            command.extend(["tests/integration/test_spoken_studio.py", "-s"])
        try:
            run = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=900 if mode == "spoken" else 180,
            )
        except subprocess.TimeoutExpired:
            result["errors"].append(
                "Evaluation timed out. Inspect Studio state before another live run."
            )
            return result
        if junit.is_file():
            try:
                suites = ET.parse(junit).getroot().iter("testsuite")
                totals = dict.fromkeys(("tests", "failures", "errors", "skipped"), 0)
                for suite in suites:
                    for key in totals:
                        count = int(suite.get(key, "0"))
                        if count < 0:
                            raise ValueError
                        totals[key] += count
                result["functional"] = totals
            except (ET.ParseError, ValueError):
                result["errors"].append("Test report was invalid.")
        else:
            result["errors"].append("Test report was not produced.")
        totals = result["functional"]
        result["passed"] = bool(
            run.returncode == 0
            and totals
            and totals["tests"] > totals["skipped"]
            and totals["failures"] == 0
            and totals["errors"] == 0
            and not result["errors"]
        )
        if mode == "spoken":
            try:
                result["latency"] = parse_spoken(run.stdout, turns)
            except ValueError:
                result["passed"] = False
                result["errors"].append("Spoken measurements were incomplete or invalid.")
        if not result["passed"] and not result["errors"]:
            result["errors"].append(
                "Functional checks failed; run the documented pytest command for details."
            )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "spoken"), default="offline")
    parser.add_argument("--turns", type=int, default=3)
    parser.add_argument("--audio", type=Path, help="Explicit short authorized synthetic input.")
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Permit the configured speech/LLM services and their normal usage costs.",
    )
    args = parser.parse_args()
    if args.mode == "spoken" and not args.allow_live:
        parser.error("Spoken mode needs --allow-live; it uses the configured providers.")
    try:
        report = evaluate(mode=args.mode, turns=args.turns, audio=args.audio)
        report["provenance"] = provenance()
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "passed": False,
                    "errors": [
                        str(error)
                        if isinstance(error, ValueError)
                        else "Evaluation tooling unavailable."
                    ],
                }
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
