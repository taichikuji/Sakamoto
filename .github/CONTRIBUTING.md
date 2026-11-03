# Contributing to Sakamoto

Be respectful and constructive. Report bugs and propose features through the provided GitHub templates; search existing issues first.

## Setup

Install Python 3.15.2 and Pipenv, then install the locked dependencies:

```bash
pipenv sync --dev
export TOKEN='YOUR_DISCORD_BOT_TOKEN'
pipenv run python main.py
```

Create the token in the [Discord Developer Portal](https://discord.com/developers/applications). Use `pipenv run ...` for project commands.

## Changes and tests

- Put always-on administration in `extensions/core/`. Put user-facing cogs in the domain extension that owns them, such as `extensions/audio/` or `extensions/moderation/`; follow the existing cog and `setup` pattern.
- Follow existing style, use clear names and appropriate type hints, and document non-obvious logic.
- Update relevant documentation and tests. Before opening a PR, run the relevant tests and verify no new warnings or errors.

Run the code-quality checks with:

```bash
pipenv run ruff check .
pipenv run ruff format --check .
pipenv run pytest -q
```

## Reloading audio extensions

`/reload audio.music` and `/reload audio.radio` replace their cogs while preserving the shared playback engine, active audio, and queue. Changes to `extensions/audio/_audio_engine.py` require a bot restart.

Existing Queue Entries keep the resolver from the music cog that created them; new commands use the reloaded cog. Use `/stop` before reloading when existing queued tracks should be discarded. Restart the bot when replacing the shared engine itself.

Unloading music clears its cache and cancels its expiry timer. Running extraction work keeps its capacity slot until it finishes, including across reloads; cancelling a command or reaching its waiting deadline does not terminate an executor thread.

## Pull requests

1. Fork the repository and branch from `main`.
2. Make and test a focused change.
3. Open a PR using the supplied template and link related issues.

## Commits

Use [semantic commit messages](https://gist.github.com/joshbuchea/6f47e86d2510bce28f8e7f42ae84c716):

```text
<type>(<scope>): <subject>
```

`scope` is optional. Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, and `chore`.
