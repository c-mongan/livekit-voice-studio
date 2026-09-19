# Reporting a security issue

This is a single-user technical prototype, not a hosted multi-user service.
Keep Studio bound to loopback. Do not expose it through a public tunnel or use
the development token broker as an internet authentication service.

Report suspected vulnerabilities through [GitHub private vulnerability reporting](https://github.com/c-mongan/livekit-voice-studio/security/advisories/new)
(**Security → Report a vulnerability**). This private reporting route is enabled
for this repository.
Do not open a public issue containing credentials, voice samples or exploit data.

Include the affected version, a minimal reproduction using synthetic input,
expected versus observed behavior, and relevant redacted errors. Do not attach
your `.env`, voice library, model cache, agent session history or raw service logs.
Voice references and transcripts are personal data.

There is no promised response SLA or production support window. Only the current
development version is maintained.

## Important boundaries

- Loopback Host/Origin checks and request headers protect the browser API from
  cross-origin requests. They do not isolate Studio from other processes running
  as the same OS user.
- Never treat a client disconnect as proof that inference stopped. Uncertain
  completion deliberately blocks new work until the backend is confirmed stopped.
- Copilot is configured with zero tools. Codex is a separately approved restricted
  agent, not tool-free; its documented global-instruction boundary still applies.
- Local speech processing does not make cloud reasoning or LiveKit transport
  offline. Use account credentials and voices only with permission.
- Keep dependencies and installed agent runtimes current, but revalidate their
  capabilities before trusting a changed runtime.
- The optional Codex read-aloud notification is an explicitly configured host
  command, not a sandboxed model tool. It does not change coding permissions,
  but its argv payload can be visible to local process inspection. Do not enable
  it for content that should not be spoken or exposed through that boundary.
