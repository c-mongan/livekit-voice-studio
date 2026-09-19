import json

import pytest


def test_dry_run_never_reads_references_or_creates_output(tmp_path, monkeypatch):
    from tools import expressive_compare as tool

    monkeypatch.setattr(tool, "run_worker", lambda *args: pytest.fail("render started"))
    result = tool.compare(
        tmp_path / "secret-neutral",
        tmp_path / "secret-style",
        tmp_path / "model",
        tmp_path / "output",
    )
    assert result["rendered"] is False
    assert result["samples"] == 6
    assert "secret" not in json.dumps(result)
    assert list(tmp_path.iterdir()) == []


def test_render_refuses_existing_output_without_overwrite(tmp_path):
    from tools import expressive_compare as tool

    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(ValueError, match="new output directory"):
        tool.compare(tmp_path / "a", tmp_path / "b", tmp_path / "model", output, render=True)


def test_render_uses_same_sentences_private_outputs_and_blind_labels(tmp_path, monkeypatch):
    from tools import expressive_compare as tool

    seen = []

    def worker(bundle, model, output, label):
        seen.append((bundle, label))
        return [
            {"sample": f"{label}-{index + 1}", "text": text, "generationSeconds": 1.0}
            for index, text in enumerate(tool.SENTENCES)
        ]

    # Unit rendering must never load a developer's real provider configuration.
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setattr(tool, "run_worker", worker)
    output = tmp_path / "results"
    result = tool.compare(
        tmp_path / "neutral-secret",
        tmp_path / "style-secret",
        tmp_path / "model-secret",
        output,
        render=True,
    )
    assert result["rendered"] is True
    assert {label for _, label in seen} == {"A", "B"}
    report = json.loads((output / "report.json").read_text())
    assert len(report["samples"]) == 6
    assert "secret" not in json.dumps(report)
    assert (output.stat().st_mode & 0o777) == 0o700
    assert ((output / "report.json").stat().st_mode & 0o777) == 0o600
    worksheet = (output / "listening.csv").read_text()
    assert "identity" in worksheet and "expression" in worksheet
    assert "neutral" not in worksheet


async def test_worker_refuses_competing_studio_before_model_load(tmp_path, monkeypatch):
    from examples.backend_lease import BackendLease
    from tools import expressive_compare as tool

    monkeypatch.setenv("VOICEBOX_RUNTIME_DIR", str(tmp_path / "runtime"))
    owner = BackendLease(tmp_path / "runtime", "http://127.0.0.1:17493")
    owner.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another local"):
            await tool.render_reference(
                tmp_path / "absent", tmp_path / "absent-model", tmp_path, "A"
            )
    finally:
        owner.release()


async def test_render_writes_pcm_bytes_and_drains_under_real_lease(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from examples import fast_qwen
    from tools import expressive_compare as tool

    monkeypatch.setenv("VOICEBOX_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("VOICEBOX_URL", "http://127.0.0.1:17493")
    output = tmp_path / "output"
    output.mkdir()
    closed = []

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def __aiter__(self):
            return self.events()

        async def events(self):
            yield SimpleNamespace(
                frame=SimpleNamespace(
                    sample_rate=24000, num_channels=1, data=memoryview(b"\x00\x80").cast("h")
                )
            )

    class Provider:
        def __init__(self, **kwargs):
            assert kwargs["voice_bundle"] == tmp_path / "bundle"

        async def prepare(self):
            pass

        def synthesize(self, text):
            assert text in tool.SENTENCES
            return Stream()

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(fast_qwen, "FastQwenTTS", Provider)
    samples = await tool.render_reference(tmp_path / "bundle", tmp_path / "model", output, "A")
    assert len(samples) == 3
    assert len(list(output.glob("*.wav"))) == 3
    assert closed == [True]
    assert not list((tmp_path / "runtime").glob("*.unresolved"))
