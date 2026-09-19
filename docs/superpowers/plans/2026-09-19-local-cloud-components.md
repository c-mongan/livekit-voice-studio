# Independent local/cloud components

User approved implementing independent component choices and checking this Mac's
capacity. Preserve existing settings and private references. New libraries default
to local LiveKit, Nemotron, Ollama and Qwen. Existing libraries migrate to configured
LiveKit and keep their provider. Never silently fall back to a remote provider.

Contract: settings add `livekitMode` (`local` or `configured`), `llmBaseUrl`
(default `http://127.0.0.1:11434/v1`). LLM providers add `ollama` and
`openai-compatible`; these accept an operator-selected model and no reasoning
preset. Ollama is loopback-only; custom endpoints accept validated HTTP(S) URLs
without credentials/query/fragments. Custom credentials remain server-side in
`VOICEBOX_CUSTOM_LLM_API_KEY`. Configured LiveKit uses the original server-side
LIVEKIT_* settings, restored when switching back from local dev credentials.
Local default model is a small explicitly prepared Ollama model, initially
`qwen3:1.7b`, subject to local measurement. No automatic model downloads.

1. Backend: migrate/validate settings; resolve endpoints without losing cloud
   credentials; provider adapters, bounded readiness, accurate locality/status;
   focused regression tests, doctor updates.
2. Frontend: independent connection and reasoning choices; editable local/custom
   model and endpoint; explanatory labels/privacy disclosure matching actual
   routing; rename Learn LiveKit to How it works; regression tests.
3. Runtime: inspect memory/model inventory; explicitly prepare a small local model
   and loopback LiveKit dev server; validate all-local and local transport with
   cloud reasoning sequentially. Do not interrupt unrelated processes.
4. Integration: run relevant suites/build/browser flows, independent review, fix
   findings, write setup/matrix/capacity docs, commit locally. No push or deployment.

Acceptance: old settings survive; switching restores configured credentials;
secrets never returned; unsupported/unavailable endpoints fail clearly; no implicit
cloud fallback; actual local-room audio proves local LLM integration; settings
blocked during active sessions; docs distinguish same-machine development from
public production hosting. Claims of offline operation require separate proof.
