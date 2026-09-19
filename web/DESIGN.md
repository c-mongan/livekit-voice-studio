# Voicebox Studio design

## Product context and assumptions

This is a product workspace for a developer at a desk, in normal daylight,
testing a local voice agent while reading its transcript. The approved brief
supplies the product purpose, users, platform (responsive web), privacy boundary,
and visual direction; no design interview or generated imagery is needed.

The primary job is to start one owned session and converse by text or voice.
Text must remain useful without microphone permission. Server readiness and
measured timings are diagnostic context, not marketing claims. Measured latency
uses a closed-by-default native disclosure; connection and setup status stay
visible without expanding diagnostics.

Assumptions: one local owner/browser session at a time. The settings drawer
changes allowlisted providers and private voice selection only while idle;
the backend owns credentials, availability and validation. Ending does not clear the visible transcript. Clearing only the
transcript does not reset the agent conversation. Reloading clears the view.

## Visual system

Light mode uses a bright white main surface and restrained cobalt action color
`oklch(.55 .149 250)`, and subtly blue-tinted neutral inspector. Tokens live in
`src/tokens.css`. Ink and muted text are deliberately dark enough for small
diagnostic labels. Semantic green/amber/red appear only for genuine state.

System sans fonts only, without downloads. Fixed rem typography, tight but
readable headings, regular prose, compact diagnostic text. Spacing uses a
4px-based scale. Controls share 8px corners and at least 44px height.

Desktop: transcript/composer on the left and a compact pipeline inspector on the
right. At 780px the inspector stacks below the conversation. At 480px controls
wrap and composer hints occupy their own row. No horizontal page overflow,
decorative card grid, illustrations, shadows, or fabricated data.

The workspace is conversation-first: a 64px header, compact heading, and a
36px audio meter alongside the voice controls. On desktop viewports at least
700px tall, the workspace is bounded to the available viewport and the
transcript takes the remaining space; the composer does not shrink away.
The composer has a sticky bottom position as a scrolling fallback. Mobile
omits the nonessential subtitle and reduces the empty transcript area rather
than reducing touch-target sizes or hiding the privacy disclosure.

## Interaction and truthful state

- Explicit privacy acknowledgement before a session starts.
- Changing the speech or reasoning provider requires renewed acknowledgement.
- Start never asks for microphone permission; the mic button does.
- Real connection and agent SDK state drives ready/connecting/listening/
  thinking/speaking/reconnecting/blocked labels. Listening is never shown when
  the microphone is off.
- Thirty-six audio bars use LiveKit's real multiband track analysis. Silence or
  no track is a flat line. Reduced-motion users see a static line with text state.
- The broker's draining state blocks restart. Only the owner sends heartbeat,
  interrupt, and end requests. All handles and transcripts stay in memory.
- API failures have inline recovery instructions. Typed drafts survive send
  failure. SDK errors/tokens are not printed by application code.
- Keyboard Enter sends; Shift+Enter inserts a newline; IME composition is
  respected. Native focus, checkbox, details, textarea and button semantics
  support keyboard access. Transcript auto-follow stops when the user scrolls up.
  Empty transcripts start at the top so onboarding guidance is not clipped;
  clearing history resets that position and resumes following new messages.

## Settings and private voices

One native modal drawer keeps the conversation surface compact. **Voice library**
opens directly to Voices; the quieter **Settings** action opens Providers.
Voices comes first in the keyboard-addressable tabs, with a pinned heading/close
control and a scrolling body. Escape returns focus to the specific opening
trigger and discards unsaved voice
audio. Desktop uses a 520px drawer; mobile uses the full viewport width.
No nested cards, decorative waves or new fonts are introduced.

Provider options and unavailable reasons come from the server. Copilot and
Codex are explicitly labeled remote models, with the fixed Luna/low preset.
Azure and OpenAI have their own fixed model presets. The data-route disclosure
distinguishes local Nemotron recognition from cloud recognition and explains
that LiveKit still transports conversation audio/text. Selection affects the
next room only; mutations are disabled during start, active use and drain.

Voice enrollment begins only after an explicit microphone gesture. Web Audio
captures mono PCM at 24 kHz and encodes 16-bit WAV without MediaRecorder or a
decoder dependency. The waveform and duration derive from actual captured
samples. Capture ends at 30 seconds; cancel, close, pagehide, session activation,
and unmount stop all tracks, including late permission grants. Review flags
short, silent and clipped samples. The user previews the audio, edits/verifies
the transcript and authorizes the voice before uploading to loopback.
The private reference is never sent to cloud STT. Browser storage is not used.
Deletion requires confirmation and cannot remove the currently selected voice.

### Reference → generated audition → chat

The compact entry names the real sequence without adding a second onboarding
screen. Recording and its permission disclosure precede saved voices, so adding
a reference does not require scrolling past the library. Voices progressively
disclose local readiness and one inline audition
at a time. A saved reference opens its audition, not a new modal. Exact-transcript
guidance and recording-quality advice use the existing field-help treatment.

**Original recording** always means reference playback. **Generated clone**
means new speech from editable text (300-character maximum). Controls never
suggest reference playback demonstrates clone quality. The generated result
has an explicit native Play action, no autoplay, and no invented quality scores
or synthesis controls. Choosing remains a separate deliberate action.

Local auditions do not require LiveKit or a reasoning provider. The UI gates on
idle/no conversation rather than overall chat readiness; local model and hardware
are validated by the generation endpoint with actionable errors. Busy and cleanup states
lock mutations and chat starts immediately. Closing or changing focus/text
discards playback and cancels pending generation; cleanup stays owned outside the
drawer view. Errors preserve that lock until drain is confirmed. Audio and job
identifiers exist only in memory.

Isolated browser verification used explicitly mocked API state, not private
configuration: the composer remained above the fold at 1200x830 and 390x844,
with no horizontal overflow. A synthetic oscillator (not hardware microphone
input) exercised the real AudioWorklet, stopped tracks, blob playback under
the production-style CSP and explicit multipart upload. This is not evidence
of real provider, reference-quality, or live-room behavior.

## Verification boundary

Unit tests cover API privacy headers/error handling, state mapping, message
validation and transcript merge/clear semantics. DOM interaction tests cover
typed-first queueing, permission denial, interrupt acknowledgement/failure,
keyboard/IME behavior, privacy gates, transcript text safety and reduced motion.
Hook tests cover ownership loss, cleanup failures, late connection cancellation
and unload release. Native LiveKit hook tests cover exact-identity discovery,
initial attributes on participant arrival, attribute transitions and slow
generation without a reply timeout. Stylesheet-contract tests guard responsive declarations;
they do not claim to measure layout. Type checking verifies installed SDK
public APIs. Production build validates the bundle.

Parent integration owns browser checks at desktop/390px, the live local API,
microphone/autoplay permissions and a real room session. Do not substitute a
mocked transcript or animation as evidence of a working live pipeline.

## Local and cloud clarity

The conversation surface shows a four-part route summary before starting, with
explicit unknown states when the server is unavailable. "Local setup" describes
configuration; idle UI says services are checked at start, rather than claiming
they are already running. The privacy acknowledgement retains a disclosure of
where audio and text go without repeating all four locations by default.
Connection & AI settings keep Ollama's endpoint under Advanced connection;
custom endpoints expose it immediately. Cloud AI choices explain text routing.
Startup uses the server's reported stage and keeps existing transcript content.

StatusBadge and ConversationPanelState were adapted from StatusBadge and
ExtractionPanelState in mongo-ai/Intelligent-Document-Processor with the owner's
explicit permission in this task. Adaptations use existing Studio tokens and
React, without importing that project's motion, icon, or routing dependencies.
No document-processing code, credentials, or private data was imported.

## Document Processor component review and adaptation

Reviewed the owner's private Intelligent-Document-Processor source via authenticated
GitHub reads rather than cloning or installing the full application. The scoped
review covered Sidebar, CommandPalette, ChatPanel, ReviewHeader, PremiumLoader,
StatusBadge and ExtractionPanelState, plus the stylesheet and package manifest.

- CommandPalette: adapted its searchable action model and arrow/Enter navigation
  into StudioCommands. Native dialog adds modal focus handling and Escape; locked
  actions cannot bypass Studio session ownership. No routing dependency added.
- Sidebar: adapted the composition pattern into a restrained desktop control
  column alongside a larger sticky conversation workspace. Mobile stacks normally.
- ChatPanel: adapted role-separated message surfaces and alignment. Existing
  LiveKit audio, IME handling, draft preservation and scroll-follow rules remain
  authoritative. Its browser SpeechRecognition code was not imported.
- ReviewHeader: its document validation/progress controls do not map to Studio.
- PremiumLoader: rotating fictional loading stages were not reused. Studio reports
  actual worker stages and does not invent percentages or work being performed.

Owner permission covers reuse; private product data and application configuration
were not imported. No new dependencies. This is a scoped UI review, not a security
or correctness audit of the source application.

Validation: 206 frontend tests passed, including keyboard selection, locked
commands, focus restoration and modal exclusion. TypeScript/Vite build passed
with the existing bundle-size warning. Browser checks exercised search-to-settings,
no-results, Escape, desktop composition and 390 px mobile width without horizontal
overflow; no page errors were observed. Conversation inference was not rerun for
these presentation-only changes.


## Cohesive navigation and appearance

The global navigation contains Conversation, Voice library and Settings. Opening
Voices or Settings preserves the conversation in a native modal; provider and
voice mutations remain locked during active sessions. The settings tablist now
contains Voices, Connection & AI, Audio and Appearance, with wrapping arrow-key
navigation, Home/End and a single active tab stop. Audio links to existing speech
configuration, voice selection and the explicit microphone test; it never begins
capture automatically.

Appearance adapts the owner's UserPreferences theme choices to real global CSS
tokens: Light, Dark (navy/cyan) and System. System is the new-browser default and
tracks OS changes. Only theme and compact-density preferences use localStorage;
transcripts and credentials are not included. Invalid stored values fall back to
System. Blocked storage still allows an immediate change and reports that it was
not saved. Themes are initialized before React renders; storage events synchronize
other loaded tabs. Compact mode reduces spacing without shrinking touch targets.
Reduced motion continues to follow the OS setting.

Verification: 210 frontend tests passed; production typecheck/build passed with
the existing bundle-size warning. Browser checks verified Light/Dark appearance,
persistence after reload, compact density, mobile tabs at 390 px, the Audio-to-mic
check flow (microphone remained off), and navigation. System-change handling and
storage failures were checked with focused tests. Voice inference was unchanged.

## Guided troubleshooting and first-run recovery

Setup validates the complete bounded report shape before rendering. Missing items
appear first, unverified checks stay explicit, and found items are collapsed.
The optional pipeline inspector includes three real-control exercises with expected
observations, restoration steps and interview prompts. Self-reported progress is
in-memory and explicitly distinguished from test evidence. No fault is injected,
provider switched or service stopped by opening or checking the guide.
