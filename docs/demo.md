# Tomorrow's demo

## Before showing it

1. Run `uv run --no-sync python -m examples.studio --check`.
2. Start `./studio start --open`; the command should finish with a running status.
3. Open `http://127.0.0.1:8765`.
4. In Settings, use **Nemotron + Copilot Luna low** for the default demo.
5. Start the session before speaking; the first connection prepares the model
   and voice. Keep other local model jobs stopped.

Voicebox itself can remain closed. Do not run the old standalone worker
alongside Studio. Model downloads and native builds are separate setup steps,
not part of the live demonstration.
Use `./studio stop` when finished, not the Stop control on an old Copilot command card.

## A short, honest demonstration

1. Type a short question. Point out that the answer uses your locally selected
   voice while the reasoning model is remote.
2. Turn on the microphone and speak naturally. Nemotron transcribes on the CPU.
3. Interrupt the answer, then ask a different question.
4. End the session. Open Voices and show recording, exact transcript, naming and
   generated audition. Make clear which control plays the reference and which
   plays new synthesized words, then choose the voice.
5. Optionally choose Codex Luna low. Read the restricted-agent disclosure and
   explicitly acknowledge it; do not describe Codex as tool-free.
6. Show Azure as an explicit alternative, not an automatic fallback.

Only use your own voice or another voice with permission. Do not present a
synthetic test recording as a person's clone.

## What we can accurately claim

- Recording uses a real browser AudioWorklet and saves a private local WAV.
- The selected model and reasoning effort are checked against the runtime.
- Copilot has zero initialized tools; Codex uses a verified restricted workspace
  and disables external capabilities, with trusted global instructions disclosed.
- Local Nemotron and local Qwen work together with either agent backend.
- Five synthetic-spoken turns with local VAD and no speculative reasoning received
  response audio in **3.18 s median**, **3.63 s slowest observed**, using Copilot.
- This is one repeated synthetic phrase in one room on one M4 Mac, not a provider
  ranking, accent assessment or p90 benchmark.
- No voice reference is uploaded to the LLM, but recognized conversation text
  goes to the reasoning provider and LiveKit carries conversation media.

## What still needs listening

Do not claim perfect accent accuracy, exact voice identity, seamless human
turn-taking, or identical speed on every PC. The controlled spoken tests used
an installed stock synthetic voice so they could be reproduced without recording
someone. Human accent, microphone, room noise and voice likeness need user testing.

## If something fails

- Check the exact error rather than repeatedly starting rooms.
- End the session and wait for drain before changing providers.
- Never clear an unresolved-work marker based only on a healthy HTTP endpoint.
- Do not enable unrestricted Codex, public tunnels, or silent cloud STT fallback
  to make the demonstration appear successful.
- Keep the existing configured Azure option available, and say explicitly when
  you switch to it.
