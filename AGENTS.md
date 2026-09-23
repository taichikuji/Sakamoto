# Agent Guide

Inspect the code and tests you will change. Read `CONTEXT.md` for behavior and maintainer decisions, `README.md` for setup or deployment, and `.github/CONTRIBUTING.md` for contribution workflow when relevant.

## Work

- Make the smallest complete change. Avoid unrelated refactors and behavior changes. Add dependencies only when they materially simplify the solution.
- Never expose or commit secrets. Commit or push only when asked.
- Use Pipenv for dependencies and commands (`pipenv sync`, `pipenv run ...`).
- Keep Discord code asynchronous and nonblocking. Reuse the bot's `aiohttp.ClientSession`.
- Keep optional integrations isolated so their failure does not break core slash commands or voice.
- Avoid unnecessary CPU, memory, network, and background work. Stop temporary workers, including Buildx builders, after use; keep caches that reduce expensive requests.

## Code map

- `extensions/core/` holds always-on administration; other extensions live in domain folders. Files beginning with `_` are internal, not extensions.
- For music changes, inspect both `extensions/audio/music.py` and `extensions/audio/_audio_engine.py`. Keep provider and source resolution in `music.py`; keep shared voice, queue, and playback state in `_audio_engine.py`.

## Verify

Run focused tests for changed behavior, including audio tests for music or audio changes. Run the full suite for changes that affect shared behavior.
