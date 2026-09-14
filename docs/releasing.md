# Preparing a source-only Studio release

Studio is currently a development prototype. Its supported setup is an Apple
Silicon Mac with Python 3.12, Node.js 22.12 or newer, and separately configured
speech models, LiveKit, and reasoning accounts. Use docs/oss-readiness.md for
observed results and unresolved limitations; do not label these as a stable release.

## Choose the artifact

- The repository source contains Studio, its frontend source, setup tools,
  lockfiles, example configuration, tests, and notices. Users follow the quickstart
  and build the frontend locally.
- The `livekit-plugins-voicebox` wheel and Python source distribution contain only
  the provider library. Installing either does not install the Studio UI, server,
  launch scripts, or local Nemotron setup.
- Do not ship a working-directory archive. Include only reviewed source paths.
  Exclude `.git`, `.env`, caches, libraries, recordings, model weights, native
  installations, node_modules, and built frontend dependencies.

## Licenses and separately installed components

| Component | Evidence | Source-only release treatment |
| --- | --- | --- |
| Project source and Python provider | Root and provider MIT LICENSE files | Include both license files |
| Adapted LiveKit React starter code | web/THIRD_PARTY_LICENSES, pinned in docs/provenance.md | Include the upstream notice unchanged |
| Qwen MLX checkpoint | [Pinned model card](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16/blob/1eccf1cb2519b5a4e8a95b5f0544f3303568164f/README.md) declares Apache-2.0 | Download separately; no weights in source release |
| NeMo-Speech.cpp runtime | [Pinned license](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/4f9676226f667d14608487df744f375db87127f8/LICENSE), Apache-2.0 | Build separately; setup preserves native notices |
| Nemotron weights | [NVIDIA Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/) | Download separately; this is a distinct model agreement, not the project MIT license |
| Python/npm dependencies | Locked package distributions and their own notices | Install separately; bundled binary/app releases require an additional dependency-notice review |
| Copilot, Codex and other hosted reasoning | Provider account terms and actual entitlements | No account or subscription access is granted by the project license |

The pinned Qwen card, pinned native runtime license, and NVIDIA model agreement
were checked on 14 September 2026. NVIDIA model redistribution requires its
agreement and attribution notice; see docs/local-stt.md. No model redistribution
is part of this source candidate. This inventory is not an exhaustive legal
opinion or audit of every transitive dependency.

## Candidate verification

1. Review the exact source diff and list every intended archive path. Include the
   launcher executable permission, .env.example, both lockfiles used by the app,
   the native setup lockfile, licenses, contributor guide and security policy.
2. Build the provider wheel and source distribution. Inspect their members and
   confirm the MIT license is present. Do not describe these as a Studio installer.
3. Scan the final source snapshot and extracted package contents for secrets.
   Check for voice data and model assets separately; secret scanning alone cannot
   identify every private file. Record archive SHA-256 checksums and file inventory.
4. Run the checks in CONTRIBUTING.md for the reviewed source and bind hosted CI
   to the commit that would ship. Local passing tests do not replace hosted CI.
5. Retain known failures in the release notes. A development preview still needs
   clear setup expectations, a listening check, and an independent first-user trial.

Preparing files does not publish them. Review the exact commit/archive before
creating a tag, uploading assets, publishing a package, or changing repository
visibility. The current package version remains 0.1.0.dev0; no stable version or
public release is implied by building an artifact.
