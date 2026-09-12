import json

import pytest

from benchmarks import benchmark_qwen_streaming as benchmark


def configure(monkeypatch, profiles, samples):
    requests = []

    def read(base, route, limit):
        requests.append(route)
        if route == "/profiles":
            return json.dumps(profiles).encode()
        if route.endswith("/samples"):
            return json.dumps(samples).encode()
        return b"synthetic-reference-bytes"

    monkeypatch.setattr(benchmark, "read_local", read)
    return requests


def test_reference_reads_only_the_explicit_single_voice(monkeypatch):
    requests = configure(
        monkeypatch,
        [{"id": "voice/id", "name": "Selected", "voice_type": "cloned"}],
        [{"id": "sample/id", "reference_text": "Authorized synthetic reference."}],
    )
    audio, text = benchmark.reference("http://127.0.0.1:17493", "Selected")
    assert audio == b"synthetic-reference-bytes"
    assert text == "Authorized synthetic reference."
    assert requests == ["/profiles", "/profiles/voice%2Fid/samples", "/samples/sample%2Fid"]


@pytest.mark.parametrize(
    "profiles,samples",
    [
        ([], []),
        ([{"id": "one", "name": "Selected", "voice_type": "preset"}], []),
        (
            [
                {"id": "one", "name": "Selected", "voice_type": "cloned"},
                {"id": "two", "name": "Selected", "voice_type": "cloned"},
            ],
            [],
        ),
        ([{"id": "one", "name": "Selected", "voice_type": "cloned"}], []),
        (
            [{"id": "one", "name": "Selected", "voice_type": "cloned"}],
            [{"id": "a", "reference_text": " "}, {"id": "b", "reference_text": "Other"}],
        ),
        (
            [{"id": "one", "name": "Selected", "voice_type": "cloned"}],
            [{"id": "a", "reference_text": " "}],
        ),
    ],
)
def test_reference_never_falls_back_or_selects_arbitrary_samples(monkeypatch, profiles, samples):
    configure(monkeypatch, profiles, samples)
    with pytest.raises(ValueError):
        benchmark.reference("http://127.0.0.1:17493", "Selected")
