# Local coding-agent conversation providers

`examples.agent_llm.AgentLLM` implements the LiveKit Agents 1.8.1 `LLM` and
`LLMStream` interfaces. These adapters are for **conversation only**, not for
continuing coding work or accessing a repository. Existing Azure/OpenAI
providers are unaffected.

## Availability

| Provider | Status | Optional dependency |
| --- | --- | --- |
| Copilot | Implemented; exact account/model/tool preflight required | `github-copilot-sdk==1.0.13` |
| Codex | Restricted-agent opt-in; macOS preflight and two-turn model smoke passed | None |

Defaults are exactly `model="gpt-5.6-luna"` and `reasoning_effort="low"`.
There is no automatic model fallback. Copilot model metadata must advertise
the requested effort and must not carry a disabled or unknown policy.
`validate()` starts an owned runtime, lists models, creates a fresh session,
and verifies the provider's capability boundary. Copilot must report an empty
initialized tool list. Restricted Codex instead verifies scoped command
execution and zero external capabilities. Neither sends a model prompt.
Successful metadata validation is not proof that a subsequent billable
inference will be authorized by the account.

The integrated Copilot implementation has since completed two short real
requests with persistent conversational memory: first nonempty text arrived
in **1.37 s and 0.74 s**. A real Studio room also rendered the Copilot reply,
generated local Qwen speech, and completed **interrupt → next reply → end**
without replaying the interrupted text. Those small checks are not a broad
latency or account-availability guarantee.

The installed Copilot CLI 1.0.84-4 has passed that token-free preflight with
SDK 1.0.13. The SDK connects to the executable on `PATH`, or `cli_path` when
specified, rather than choosing its bundled executable.

### Why Codex requires explicit restricted-agent consent

The installed Codex 0.153.4 app-server and Python SDK 0.154.0 have not been
verified to support a blanket tool-disable policy. `approvalPolicy="never"` means no approval
prompts, **not** no tools; read-only sandboxing still permits reads. Empty
dynamic tools do not remove built-in tools, MCP, or configured extensions.
Consequently selecting Codex without `allow_restricted_agent=True` raises a
non-retryable preflight error **before spawning a process**. There is no
silent read-only fallback. The restricted adapter uses installed
`codex app-server` directly over bounded stdio JSON-RPC; no Codex SDK is needed.
Inspection of SDK 0.154.0 confirms that `ApprovalMode.deny_all` maps to
`AskForApprovalValue.never`, and its low-level client's default approval handler
accepts approval requests. Neither behavior is a substitute for no-tools
enforcement. The adapter declines command/file approval requests explicitly
and rejects every other server-originated action request; it does not use the
SDK's default approval handler.

### Restricted-agent mode

```python
model = AgentLLM(provider="codex", allow_restricted_agent=True)
capabilities = await model.validate()  # No model inference.
```

This opt-in is a different capability boundary from tool-free Copilot. Frontend
and backend integration must label it **Restricted agent — private workspace
and runtime files; command network disabled**, obtain explicit consent, and
preserve `tools_enabled=True`. Do not automatically set the opt-in merely
because someone selected Codex.

A narrower **restricted-agent** mode is technically different from tool-free
conversation. Codex's documented
[permission profiles](https://developers.openai.com/codex/permissions) can deny
root reads, allow minimum runtime paths and a private work directory, and
disable command networking. This is more restrictive than legacy `read-only`,
which permits broad filesystem reads.

Token-free probes against installed Codex 0.153.4 confirmed:

- `model/list` advertises exactly `gpt-5.6-luna` with `low` effort.
- An ephemeral `thread/start` selects the requested custom
  `activePermissionProfile`, exact model/effort, and `approvalPolicy="never"`.
  Its compatibility sandbox reports network disabled and system temporary
  directories excluded.
- An OS sandbox using `:root="deny"`, `:minimal="read"`, home-directory deny,
  and a private-workdir write grant can read an owned inside canary but cannot
  read an owned outside canary. No user file contents were read.
- An owned loopback listener is reachable in an unsandboxed control but not
  from the scoped command sandbox.
- Replacing `:minimal` with a few manually chosen OS directories was not a
  viable restriction: even a harmless executable aborted. Minimum runtime
  support cannot be omitted and still described as a working command sandbox.

Normal-auth thread startup reports an unowned `AGENTS.md`
instruction source even with zero project-document budget, empty developer
instructions, a supplied base prompt, disabled host skill discovery, and
disabled plugin/hook features. A follow-up with `project_root_markers=[]`
confirmed that source belongs to the normal global Codex configuration, not
the application repository or private workspace.

The first exploratory MCP status query exceeded the probe's 64-KiB transport line limit.
A follow-up with a bounded 1-MiB line limit confirmed **20 configured servers,
14 advertising tools, and 259 tools**, despite `mcp_servers={}`, `plugins={}`,
and the disabled feature flags. Thus an empty map does not remove the inherited
external capability set in this runtime/configuration. Only counts were
reported; no server names, configuration secrets, instruction contents, or
tool outputs were printed. No inference followed these probes.

**Explicit named overrides resolved that MCP restriction.** The adapter lists
public server/plugin names, stops its discovery process, then launches a new
owned runtime with `enabled=false` for every discovered server and installed
plugin. After restart, the installed runtime reports **zero advertised MCP
tools** (the disabled server entries can still appear in metadata). It verifies
tools, resources, resource templates, and installed plugin status again after
thread creation, rejecting an incomplete listing or remaining capabilities.

Every restricted preflight:

1. Selects a fresh permission profile with root/home reads denied, private
   workspace writes permitted, minimal runtime reads permitted, system
   temporary directories denied, and command networking disabled.
2. Verifies inside/outside reads and writes using owned synthetic canaries,
   plus a blocked loopback connection with a successful host control.
3. Disables browser/computer-use, apps, plugins, hooks, memory, additional
   agents, image tools, host skills, login shells, and shell snapshots.
   Tool subprocesses inherit no host environment; their `HOME`, `TMPDIR`, and
   `PATH` are explicitly restricted.
4. Starts one ephemeral thread with exact model/effort, no model fallback,
   explicit permission profile/runtime root, empty environment/capability
   selections, and no dynamic tools. It rejects inherited repository metadata.
5. Reports `mode="restricted-agent"`, `tools_enabled=True`,
   `external_tools=0`, `command_network_enabled=False`, and
   `filesystem_scope="private-workdir-and-minimal-runtime-paths"`.

The runtime still loads trusted **global** Codex instructions; this is reported
as `inherits_global_instructions=True` rather than concealed. A source outside
the normal global Codex configuration is rejected. No instruction contents or
credentials are read by the adapter or copied into private storage. Normal
CLI authentication remains enabled. Opt in only when that global configuration
is trusted.

Profiles apply to sandboxed commands, **not** to every capability. The official
[scope documentation](https://developers.openai.com/codex/permissions#scope-and-enforcement)
explicitly excludes MCP servers, connectors, browser/computer use, cloud tools,
and client/model/authentication requests. A successful command sandbox probe
alone therefore cannot establish “no machine or repository authority” for the
entire agent. The external capability disabling and re-attestation above are
therefore mandatory, separate from the command sandbox. Model/authentication
and discovery traffic remain CLI service traffic, not tool-command networking.
This is a restricted coding-agent runtime, **not** a general-purpose security
container or a tool-free language-model API.

The installed 0.153.4 runtime has passed complete restricted preflight, including
the OS canaries, exact Luna/low selection, zero external tools, and absence of
inherited repository metadata. Model inference remains a separately authorized
smoke check; passing preflight is not a claim that two conversational turns
have already succeeded.

A subsequent explicitly restricted synthetic smoke completed two real Luna/low
requests with correct conversational memory. First nonempty text arrived in
**3.59 s and 2.53 s**. Copilot was faster in the small comparison, so Copilot
remains the default. Studio requires a separate restricted-mode checkbox when
selecting Codex and persists that consent independently of the provider name.
Changing to another provider resets the consent.

## Isolation and lifecycle

1. One adapter owns one persistent subprocess and one new session. Restricted
   Codex additionally owns a short-lived discovery process, which it stops
   before launching the restricted runtime. It never connects to another
   server, resumes an existing session, or terminates another CLI.
2. Copilot uses SDK `mode="copilot-cli"` with normal logged-in-user authentication
   enabled and a newly created private
   `.voicebox-agent-<random-id>` directory. Working, configuration, and state
   directories are separate from the application working directory.
   Ancestor/config/instruction discovery, skills, file hooks, host Git
   operations, memory, extension loading, plugins, built-in MCPs, and
   session-store integration are disabled.
   The public session-options API also explicitly clears installed plugins and
   built-in agent/skill allowlists before initializing the tool set. These
   restrictions are independent of the SDK's multi-tenant `empty` mode.
3. For Copilot, `available_tools=[]`, source-qualified exclusions for all built-in/MCP/custom
   tools, and a permission handler returning `user-not-available` enforce
   conversation-only operation. Before any prompt, the runtime initializes its
   tool set and must attest that it is empty. A prompt saying “do not use tools”
   is only supplementary guidance, not the safety boundary.
4. `aclose()` first cancels owned streams and awaits cancellation settlement,
   then stops the owned runtime and removes only its private directory.
   If stopping cannot be confirmed, state is retained rather than deleting
   files beneath a potentially running process.

**Authentication:** the adapter does not read, copy, print, or symlink credential
stores. Authentication remains the CLI's responsibility. Normal
`use_logged_in_user=True` authentication is enabled; the adapter neither selects
SDK `empty` mode (which disables keychain probing) nor sets a keychain-disable
environment variable. The CLI can use its documented keychain/environment
authentication while conversation state remains private. An account whose only
credential is stored in a different filesystem-based CLI home may still require
normal CLI authentication setup; the adapter does not copy that home.
Installed-CLI metadata/session/tool preflight has passed in this normal-auth mode.

## Streaming and interruption

- LiveKit `AgentHandoff` lifecycle metadata is skipped rather than mistaken for
  tool history. `AgentConfigUpdate` instructions remain model-visible; updates
  that add tools are rejected. Function calls, function outputs, and unexpected
  context payloads still fail closed.
- Only nonempty assistant text is emitted. Completed-message events are
  deduplicated against deltas; tool output and runtime diagnostics are never
  speech. Verified Copilot input/output token counts produce LiveKit usage;
  Codex currently reports text timing without translating token usage.
- Turns are serialized. Ordinary append-only chat contexts send only their
  new messages into the persistent conversation. An edited/truncated context
  or an interrupted response sends a full authoritative context snapshot.
  This is a textual history correction, **not physical erasure of earlier
  runtime context**; it retains the same session. Snapshot correction can
  increase prompt cost and is not a guarantee of provider-side cache hits.
- Closing a queued stream does not abort another stream. Closing an active
  stream waits for any in-flight send acknowledgment, abort acknowledgment,
  and an idle event before releasing the turn lock. Unknown settlement
  permanently poisons the adapter and closes its runtime.
- Generation checks suppress callbacks from old turns; recently completed
  message identifiers are rejected if forwarded into a later subscription.
  Unexpected capabilities or model switching poison the runtime. Restricted
  Codex suppresses command/file-change output, rejects other tool activity,
  and requires `turn/interrupt` acknowledgment plus completion before reuse.
- Input is text-only, bounded at 200,000 characters; assistant output is
  bounded at 32,000 characters, with a 128-event callback queue. Turn deadlines
  use LiveKit connection options. Adapter errors are sanitized and
  non-retryable; there are no adapter retries. The vendor SDK/runtime can have
  its own internal buffering and network retry behavior.

## Deterministic validation

```sh
.venv/bin/python -m pytest tests/test_agent_llm.py -q
.venv/bin/ruff check examples/agent_llm.py tests/test_agent_llm.py
.venv/bin/mypy examples/agent_llm.py
```

## Explicit, synthetic live check

Run only after authorizing **two short model requests**. This does not start
Studio, TTS, STT, or a repository-capable agent. It records first-text latency
without printing generated text, source code, runtime stderr, or credentials.
An exact-answer check makes failures visible rather than reporting latency
for an unusable stream.

The command below checks tool-free Copilot. To explicitly authorize the
restricted Codex smoke check instead, change its constructor to:

```python
AgentLLM(provider="codex", allow_restricted_agent=True)
```

Keep the same two synthetic prompts and `max_retry=0`. Check that its preflight
reports `external_tools=0` and `command_network_enabled=False`. Do not mistake
`tools_enabled=True` for a failure: the restricted mode deliberately permits
sandboxed local operations and reports that honestly.

```sh
.venv/bin/python - <<'PY'
import asyncio
import json
import logging
import time

from livekit.agents import APIConnectOptions, llm
from examples.agent_llm import AgentLLM

logging.disable(logging.CRITICAL)

async def main():
    rows = []
    async with AgentLLM(provider="copilot") as model:
        metadata = await model.validate()
        ctx = llm.ChatContext()
        for prompt in (
            "Reply with exactly the single word Hello.",
            "What word did you just say? Reply with only that same word.",
        ):
            ctx.add_message(role="user", content=prompt)
            start = time.perf_counter()
            ttft = None
            parts = []
            async with model.chat(
                chat_ctx=ctx,
                conn_options=APIConnectOptions(timeout=60, max_retry=0),
            ) as stream:
                async for chunk in stream:
                    if chunk.delta and chunk.delta.content:
                        if ttft is None:
                            ttft = time.perf_counter() - start
                        parts.append(chunk.delta.content)
            answer = "".join(parts)
            ok = answer.strip().rstrip(".!").casefold() == "hello"
            rows.append({"turn": len(rows) + 1, "ttft_seconds": ttft, "answer_ok": ok})
            ctx.add_message(role="assistant", content=answer)
        print(json.dumps({"preflight": metadata, "turns": rows}))
        if not all(row["answer_ok"] for row in rows):
            raise SystemExit("Synthetic conversation check failed.")

asyncio.run(main())
PY
```
