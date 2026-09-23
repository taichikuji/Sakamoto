# Agent Guide

Before changing code, read `README.md`, `CONTEXT.md`, `.github/CONTRIBUTING.md`, and the relevant implementation and tests.

## Rules

- Make the smallest complete change; avoid unrelated refactors, dependencies, and behavior changes.
- Never expose or commit secrets.
- If behavior is unclear, inspect the code, tests, and documentation instead of guessing.
- Use Pipenv for dependency management and commands (`pipenv sync`, `pipenv run ...`).
- Keep Discord code asynchronous; do not introduce blocking work.
- Reuse shared resources, including the bot's `aiohttp.ClientSession`.
- Treat CPU, memory, network traffic, and background processes as costs. Stop temporary workers such as Buildx builders after use, avoid recurring work without a clear benefit, and preserve caches that prevent more expensive requests.
- Optional integrations must fail gracefully: preserve core slash-command and voice workflows.

## Structure

- Extensions: `extensions/core/` (always-on administration) and domain folders such as `extensions/audio/` and `extensions/moderation/`.
- Files beginning with `_` are internal and are not extensions.
- Music changes require reviewing `extensions/audio/music.py` and `extensions/audio/_audio_engine.py`.
  - Keep provider/source resolution in `music.py`.
  - Keep generic voice, queue, and playback state in `_audio_engine.py`.

## Verify

Run tests for the changed subsystem, including audio tests for music or audio changes, then the full suite when practical.
