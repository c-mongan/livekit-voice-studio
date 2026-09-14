"""Opt-in two-turn reasoning check. Synthetic text only; no audio or saved replies."""

import json
import os
import time

import pytest
from livekit.agents import APIConnectOptions, llm

from examples.agent_llm import AgentLLM

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("STUDIO_AGENT_INTEGRATION") != "1",
        reason="Explicitly enable the real reasoning-provider check.",
    ),
]


async def test_real_reasoning_remembers_synthetic_word():
    provider = os.environ.get("STUDIO_TEST_PROVIDER", "copilot")
    assert provider in ("copilot", "codex")
    restricted = os.environ.get("STUDIO_TEST_RESTRICTED_CODEX") == "1"
    assert provider != "codex" or restricted, "Explicit restricted Codex consent is required."
    context = llm.ChatContext()
    context.add_message(role="system", content="Follow the user's short reply format exactly.")
    timings = []
    async with AgentLLM(provider=provider, allow_restricted_agent=restricted) as adapter:
        metadata = await adapter.validate()
        if provider == "copilot":
            assert metadata["tools_enabled"] is False
        else:
            assert metadata["external_tools"] == 0
            assert metadata["command_network_enabled"] is False
        for prompt, expected in [
            ("Remember the word tulip. Reply only with Ready.", "ready"),
            ("What word did I ask you to remember? Reply only with that word.", "tulip"),
        ]:
            context.add_message(role="user", content=prompt)
            started = time.perf_counter()
            first = None
            parts = []
            async with adapter.chat(
                chat_ctx=context, conn_options=APIConnectOptions(timeout=45, max_retry=0)
            ) as stream:
                async for chunk in stream:
                    if chunk.delta and chunk.delta.content:
                        if first is None:
                            first = time.perf_counter() - started
                        parts.append(chunk.delta.content)
            reply = "".join(parts)
            assert reply.strip().lower().rstrip(".! ") == expected
            assert first is not None
            timings.append(round(first, 3))
            context.add_message(role="assistant", content=reply)
    print(
        json.dumps(
            {
                "reasoning_check": {
                    "provider": provider,
                    "turns": 2,
                    "memory_correct": True,
                    "first_text_seconds": timings,
                    "restricted_codex": provider == "codex",
                }
            }
        )
    )
