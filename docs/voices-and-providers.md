# Record a voice and choose the conversation providers

Studio keeps the LiveKit connection independent from three processing stages:

- **Listen:** turn microphone audio into text.
- **Reason:** produce an answer from that text.
- **Speak:** generate speech with the selected local Qwen voice.

Choosing a locally installed Copilot or Codex runtime does not make its model
inference offline. Conversation text still goes to the configured provider.
Local Nemotron and Qwen keep speech recognition and speech generation on the
machine, after their explicit installation.

## Voice enrollment

Open **Voices**, choose **Record a voice**, and read the displayed passage
naturally. Record 5–30 seconds in a quiet place. Studio shows the measured duration,
checks for silence/clipping, and lets you listen back before saving.

The transcript must match what you actually said. Correct it if you departed
from the passage; Studio does not send the reference to a cloud transcription
service. Confirm that you own the voice or have permission to use it.

Saved voices live outside the repository in a private local library. Each has a
reference WAV, its exact transcript, an authorization declaration and checksum.
Names and durations are exposed to the browser; filesystem paths and transcripts
are not included in library listings. Playback is an explicit preview action.

Select a voice before starting a conversation. Recording, rename, deletion and
provider changes are disabled while a session starts, runs or drains. You must
select another voice before deleting the currently selected one, and deletion
requires confirmation. Existing private voice bundles are imported into a new
library once rather than discarded.

### Try generated speech before choosing

The original-recording preview and **Generated audition** are different controls.
An audition uses your chosen saved voice and editable new text in the actual
local Qwen backend. It does not change the selected conversation voice.
No cloud credentials or reasoning request are needed for an audition.

Only one room or audition can own inference. Close the conversation before
generating, and wait for drain after cancellation. Audition output is bounded,
kept in memory only and expires after 60 seconds; the browser clears its temporary
audio when you change or leave the audition. Voice/settings mutations invalidate
any unclaimed generated result. No generated clip is added to the voice library.
See the [listening checklist](voice-quality.md) for a repeatable comparison.

This is **reference-conditioned synthesis**, not a claim that the app trains or
finetunes a new model for every voice. Likeness and pronunciation vary with the
reference and must be judged by listening.

## Provider settings

Existing cloud integrations use fixed presets. Ollama and custom endpoints let
you choose the installed model explicitly:

| Reasoning provider | Requested model | Reasoning |
| --- | --- | --- |
| Ollama | `qwen3:1.7b` initially; editable | Disabled for voice latency |
| OpenAI-compatible | Your model identifier | None requested |
| Copilot runtime | `gpt-5.6-luna` | `low` |
| Codex runtime | `gpt-5.6-luna` | `low` |
| Azure OpenAI | Existing configured `gpt-4.1-nano` deployment | None requested |
| OpenAI | `gpt-4.1-mini` | None requested |

Providers must validate account/runtime availability. A CLI on disk is not proof
of sign-in or model access. Unsupported models or effort settings are errors,
not permission to switch silently to Auto, a larger model or another provider.

Speech recognition is selected independently: Nemotron local CPU, Azure Speech,
or OpenAI transcription. Availability messages explain missing setup. Local
model installation is an explicit operator step, never a side effect of opening
the app or changing a dropdown.

## Configuration precedence

The workspace `.env` holds credentials and machine-specific paths. The private
library `settings.json` stores nonsecret providers, model, validated model endpoint,
LiveKit mode and voice ID. Saved UI settings take precedence for those choices.
No browser API accepts credentials, executable paths or command-line arguments.
Custom endpoint tokens stay in `VOICEBOX_CUSTOM_LLM_API_KEY` on the server.
See [local/cloud setup](local-cloud-components.md).

The default library location is:

```text
~/.local/share/voicebox-studio/library/
```

Set `VOICEBOX_LIBRARY_DIR` to use a different private directory. Preserve this
directory if you want to keep voices across checkouts. Do not commit it or
include it in a public demo archive.

## Agent mode is not an approval shortcut

Using an agent runtime for conversation must not silently grant repository,
shell, network-tool or external-plugin access. The demo is a conversational
interface, not a general voice-operated coding agent. Runtime restrictions and
their limitations are documented with each adapter.

Copilot attests that its initialized tool set is empty. Codex is different:
it requires a separate checkbox acknowledging **restricted agent** mode. Its
commands are confined to a fresh private workspace and minimum runtime files;
command networking and external capabilities are disabled and verified.
Trusted global Codex instructions still apply. The UI never calls this
tool-free, and switching away clears the saved restricted-mode consent.
Both agent providers are tested with `gpt-5.6-luna` and `low`. New libraries default
to Ollama; existing provider choices are preserved.

Stopping speech and interrupting an agent turn are separate operations. The
adapter must suppress stale deltas and settle the prior turn before reuse.
Existing user coding sessions are never selected by “most recent” or taken over
without an explicit session choice.

## Bundled voices and attribution

No public-figure clones or copyrighted recordings are bundled. Open-source code
does not grant rights to redistribute somebody else's recording or voice model.
Use original recordings with permission, or appropriately licensed assets with
their required attribution. Fictional character names do not automatically
make an underlying person's cloned voice authorized.
