# PaperMuse macOS Release Notes

## Supported Platform

- macOS 14 or newer.
- Apple Silicon Mac.
- The release app is expected to run from `PaperMuse.app` with bundled server assets and a bootstrapped local Python runtime.
- The main runtime archive may be embedded in the app bundle at release time; it must not point at a developer machine path.

## Required Provider Keys

PaperMuse can launch without these keys, but the main roundtable and scan flows stay in first-run setup until required keys are configured:

- `DEEPSEEK_API_KEY`
- `TAVILY_API_KEY`
- `ENCODER_API_TYPE`

At least one LLM provider key is required for scan/adversary flows:

- `DEEPSEEK_API_KEY`
- `OPENAI_API_KEY`
- `GOOGLE_API_KEY`

The first-run config template is copied to:

`~/Library/Application Support/PaperMuse/config/secrets.toml.example`

User-provided keys should be placed in:

`~/Library/Application Support/PaperMuse/config/secrets.toml`

## Optional Capabilities

These integrations are optional. Missing tools degrade the related evidence surface but must not block app startup:

- CNKI evidence: requires `opencli` with a usable CNKI/Chrome session.
- Zotero/local corpus evidence: requires `zsearch`.
- Self-library evidence QA: optional PaperQA2 runtime from `requirements-paperqa.txt`, plus `PAPER_MUSE_PDF_DIR` pointing at a Zotero/PDF export directory. `PQA_HOME` can move PaperQA indexes outside the app bundle.
- Adversary deep evidence sidecar: optional `gpt-researcher` sidecar runtime; the app reports `missing`, `installing`, `failed`, `installed`, or `ready`.

Paid/API smoke is not run automatically. Any paid smoke must be started explicitly by a human.

## User Data Locations

Release mode stores user data outside the app bundle:

- Data and generated results: `~/Library/Application Support/PaperMuse/data`
- Config: `~/Library/Application Support/PaperMuse/config`
- Cache: `~/Library/Application Support/PaperMuse/cache`
- Python runtimes: `~/Library/Application Support/PaperMuse/runtime`
- Logs: `~/Library/Application Support/PaperMuse/logs`

Release builds must not read or write the developer checkout at `~/Projects/paper-muse`.

## Release Health

No-cost release smoke can read:

- `GET /health`
- `GET /release/health`
- `GET /evidence/status`
- `GET /perf/status`

`/release/health` distinguishes runtime bootstrap state, server import readiness, missing required keys, optional capability degradation, sidecar status, and accidental developer-path usage.

## Release Build Commands

Build an embedded main runtime archive:

`./tools/build_main_runtime.py`

Build, sign, notarize, staple, assess, and zip the app:

`PAPER_MUSE_MAIN_RUNTIME_FILE=dist/runtime/papermuse-main-runtime-macos-arm64.tar.gz PAPER_MUSE_NOTARY_PROFILE=<keychain-profile> ./tools/release_package.py package`
