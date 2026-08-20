# Agent Guide

Before changing code, read `README.md`, `CONTEXT.md`, `.github/CONTRIBUTING.md`, and the relevant implementation and tests.

## Rules

- Make the smallest complete change; avoid unrelated refactors, dependencies, and behavior changes.
- Never expose or commit secrets.
- Keep Discord code asynchronous; do not introduce blocking work.
- Reuse shared resources, including the bot's `aiohttp.ClientSession`.
- Optional integrations must fail gracefully: preserve core slash-command and voice workflows.
- If behavior is unclear, inspect code, tests, and docs rather than guessing.

## Structure

- Extensions: `functions/system/` (system/admin) and `functions/tool/` (user-facing).
- Files beginning with `_` are internal and are not extensions.
- Music changes require reviewing `functions/tool/music.py` and `functions/tool/_audio_engine.py`.
  - Keep provider/source resolution in `music.py`.
  - Keep generic voice, queue, and playback state in `_audio_engine.py`.

## Verify

Run the tests for the changed subsystem, then the full suite when practical. Run audio tests for music or audio changes.
