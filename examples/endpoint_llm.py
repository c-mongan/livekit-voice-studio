"""Owned HTTP transport for explicitly configured reasoning endpoints."""

from typing import Any

import httpx
import openai as sdk
from livekit.agents import APIConnectOptions
from livekit.agents.types import NOT_GIVEN
from livekit.plugins import openai

from examples.component_endpoints import validate_endpoint


class EndpointLLM(openai.LLM):
    def __init__(self, *, provider: str, model: str, base_url: str, api_key: str) -> None:
        endpoint = validate_endpoint(provider, base_url, model)
        self.endpoint_client = sdk.AsyncClient(
            api_key=api_key,
            base_url=endpoint,
            max_retries=0,
            http_client=httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(connect=5, read=60, write=10, pool=5),
            ),
        )
        super().__init__(
            model=model,
            client=self.endpoint_client,
            max_completion_tokens=256,
            reasoning_effort="none" if provider == "ollama" else NOT_GIVEN,
            extra_body={"think": False} if provider == "ollama" else NOT_GIVEN,
        )

    def chat(self, **kwargs: Any) -> openai.llm.LLMStream:
        # The LiveKit stream replaces the HTTP client's timeout with this option.
        # A cold local model can take tens of seconds; never retry or fall back.
        kwargs["conn_options"] = APIConnectOptions(timeout=60, max_retry=0)
        return super().chat(**kwargs)

    async def aclose(self) -> None:
        try:
            await super().aclose()
        finally:
            await self.endpoint_client.close()
