import argparse
import runpy

import pytest
from aiohttp import web
from conftest import make_wav

harness = runpy.run_path("benchmarks/benchmark_tts.py")


def test_summary_excludes_warmup_and_keeps_failures():
    rows = [
        {"phrase": "short", "warmup": True, "success": True, "elapsed_seconds": 100},
        {"phrase": "short", "warmup": False, "success": False},
    ]
    for value in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
        rows.append(
            {
                "phrase": "short",
                "warmup": False,
                "success": True,
                "elapsed_seconds": value,
                "first_frame_seconds": value,
                "end_to_end_request_rtf": value,
            }
        )
    result = harness["summarize"](rows)["short"]
    assert result["samples"] == 11
    assert result["failures"] == 1
    assert result["elapsed_seconds"]["p90"] == 9
    assert result["elapsed_seconds"]["median"] == 5.5


@pytest.mark.parametrize("status", [200, 500])
async def test_harness_mocked_end_to_end(server, profile, status):
    posts = []

    async def handle(request):
        if request.path == "/profiles":
            return web.json_response([profile])
        if request.path == "/health":
            return web.json_response({"status": "healthy", "backend_type": "synthetic-test"})
        if request.path == "/tasks/active":
            return web.json_response({"generations": [], "downloads": []})
        if request.path == "/models/status":
            return web.json_response(
                {
                    "models": [
                        {
                            "model_name": "qwen-tts-0.6B",
                            "downloaded": True,
                            "loaded": True,
                            "downloading": False,
                        }
                    ]
                }
            )
        posts.append(await request.json())
        return web.Response(body=make_wav(), status=status)

    args = argparse.Namespace(
        profile="voice-1",
        base_url=await server(handle),
        hardware="SYNTHETIC TEST",
        voicebox_version="mock",
        backend_revision="mock",
        model_revision="mock",
        profile_effects="none",
        power_settings="mock",
        phrase="short",
        iterations=2,
    )
    result = await harness["benchmark"](args)
    if status == 200:
        assert len(posts) == 3
        assert result["summary"]["short"]["samples"] == 2
        assert all(row["audio_seconds"] == pytest.approx(0.1) for row in result["results"])
    else:
        assert len(posts) == 1
        assert not result["results"][0]["success"]
        assert result["shutdown_error"] == "BackendUncertainError"
    assert "voice-1" not in str(result)
    assert "Hello. How can I help?" not in str(result)
