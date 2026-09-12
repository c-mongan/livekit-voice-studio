# Reuse and model choices

## Frontend candidates

| Project | Fit |
| --- | --- |
| [LiveKit React agent starter](https://github.com/livekit-examples/agent-starter-react) | Chosen. Official starter with supported session, voice, transcript and text APIs; MIT licensed. Current source inspected and pinned. |
| [LiveKit Agents Playground](https://github.com/livekit/agents-playground) | Useful reference, but broader diagnostic controls and documented mobile/layout limitations make it a poorer fit for a focused local conversation app. |

The starter revision is `44b8a0ce82039018a1feb2c1bda43b5ada2ab24e`.
Studio reuses a small component boundary and the supported React/RTC APIs.
It does not copy the entire starter dependency tree. Vite replaces Next.js
because this app already has a Python server and does not need server rendering,
avatars, screen sharing or video.

The UI records exactly which files were adapted and carries the full upstream
license in `web/`. No upstream install scripts were blindly executed.

## Voicebox contracts

The inspected Voicebox revision is
`51f49dea198384b4eb6087b72c17057c6eb1c1cd`.
`backend/routes/generations.py` shows the completed-WAV path and direct inference
outside the normal generation queue. `backend/services/profiles.py` and
`backend/models.py` define profile compatibility and request defaults.
`backend/backends/mlx_backend.py` establishes that yielded model results are
accumulated rather than forwarded as live audio.

No Voicebox source was changed. The adapter's cancellation/drain policy exists
because a client disconnect does not prove the backend thread has stopped.

## Azure model decision

The Azure-backed example was validated with `gpt-4.1-nano` and Azure Speech.
Model availability, authentication support and quotas depend on the configured
deployment and region; check them for the environment where the example runs.

Three short synthetic prompts on the tested deployment returned first text in
2.034, 1.848 and 1.857 seconds. Two full Studio checks measured LLM first tokens
around 1.7–1.8 seconds while TTS took materially longer. Nano is therefore
retained as the Azure example's default. Adding a larger model is not an evidenced fix
for the observed audio latency.

No model deployment or infrastructure change was made. Existing service usage
can incur charges. Azure's public pricing page returned region/currency price
placeholders during this check, so no account-specific price is claimed here.
Check the signed-in Azure estimate before creating a new deployment.

A later two-prompt comparison found an alternative Azure deployment faster on
that small sample: first text at 1.36/1.17 seconds versus nano at 2.39/1.87 seconds.
These are configuration-specific observations, not a reproducible comparison of
named models. The alternative had a lower configured token-rate limit.
Conversation history consumes tokens repeatedly, so a lower first-token number
alone is not enough to select a sustained-conversation backend. Check both token
and request limits for the intended workload. Nano remains the Azure example's
default. No deployment quota was changed.

- [Azure performance and latency guidance](https://learn.microsoft.com/azure/foundry/openai/how-to/latency)
- [Azure OpenAI pricing](https://azure.microsoft.com/pricing/details/azure-openai/)
- [LiveKit Azure plugin guidance](https://docs.livekit.io/agents/models/llm/azure-openai/)
