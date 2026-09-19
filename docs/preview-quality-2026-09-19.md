# Preview quality pass — 19 September 2026

## Changes

- Conversation instructions use the constructed reasoning adapter's model and
  selected provider. Unsafe labels are omitted; credentials and endpoint URLs are
  not copied into the prompt. The model is instructed to distinguish configured
  identity from independently verified weights. This is prompt guidance, not a
  guarantee against hallucination.
- Nemotron's heartbeat pong allowance now accommodates the existing finalization
  deadline. The native WebSocket reader cannot answer pings while finishing ASR.
  A regression reproduced the old premature disconnect and now passes both before
  and after the new ping is sent. Finalization deadlines remain unchanged; idle
  connection failure detection is slower, as documented in recognition diagnostics.
- First-run documentation names Homebrew, supplies an acceptance checklist and
  explains service restart order. Contributor instructions preserve optional MLX
  dependencies. Azure comments describe independent provider selection correctly.
- Recovery errors refer to the configured synthesis backend and workers, rather
  than implying that the separate Voicebox application is always required.
- GitHub private vulnerability reporting was enabled and read back as enabled.
  SECURITY.md links to that private route.

## Verification

- Offline Python: 555 passed, 5 opt-in integrations deselected.
- Ruff lint/format and mypy passed. Changed relative documentation links passed.
- Three synthetic local reasoning prompts correctly returned Qwen3 1.7B via
  Ollama, including a question suggesting GPT-3.5 Turbo. This is a small identity
  check, not a general factuality benchmark.
- A fresh three-turn synthetic spoken test passed with local LiveKit, Nemotron,
  Ollama and Qwen. Speech-end-to-first-nonzero-reply-audio was 4.651, 2.556 and
  2.583 seconds (median 2.583). Each reply finished before the next turn, with
  five-second pauses. Cleanup returned Studio to idle/ready with no reported error.

The spoken test used a freshly generated system-voice fixture and captured no
physical microphone. It ran after a host reboot and service recovery with more
available memory than the interrupted earlier run. These changing conditions mean
we cannot attribute every improvement to one code change. The interrupted run's
temporary log was unavailable and is not counted as a result.

## Remaining acceptance work

A separate person's clean installation, varied accents, noisy rooms, natural
interruptions, long conversations and performance under competing workloads still
need evaluation. This remains an Apple Silicon developer preview, not a claim of
universal reliability or a stable cross-platform release. Models, private voice
references and conversation transcripts are excluded from source distribution.
