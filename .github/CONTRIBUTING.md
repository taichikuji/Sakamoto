# Contributing to Sakamoto

Be respectful and constructive. Report bugs and propose features through the provided GitHub templates; search existing issues first.

## Setup

```bash
pipenv install --dev
export TOKEN='YOUR_DISCORD_BOT_TOKEN'
pipenv run python main.py
```

Create the token in the [Discord Developer Portal](https://discord.com/developers/applications). Use `pipenv run ...` for project commands.

## Changes and tests

- Put extensions in `functions/system/` (administration) or `functions/tool/` (user features); follow the existing cog and `setup` pattern.
- Follow existing style, use clear names and appropriate type hints, and document non-obvious logic.
- Update relevant documentation and tests. Before opening a PR, run the relevant tests and verify no new warnings or errors.

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
