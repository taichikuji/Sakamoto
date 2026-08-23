# Sakamoto

Sakamoto is a modular Python Discord bot inspired by the *Nichijou* character. It focuses on voice features for small-to-medium communities.

It is a Python rewrite intended to apply stronger modular design than earlier projects.

<p align="center">
  <img src="media/icon.webp" width="150" alt="Sakamoto icon"><br>
  <img src="https://img.shields.io/github/license/taichikuji/Sakamoto?color=FF3351&logo=github" alt="License">
  <img src="https://img.shields.io/github/commit-activity/w/taichikuji/Sakamoto?label=commits&logo=github" alt="Commit activity">
  <img src="https://img.shields.io/librariesio/github/taichikuji/Miia-Py?logo=github" alt="Dependencies">
</p>

## Run

See the [setup guide](https://github.com/taichikuji/Sakamoto/wiki/How-to-get-the-bot-working/) and [configuration reference](https://github.com/taichikuji/Sakamoto/wiki/Configuration-Guide#setting-environment-variables) for integration-specific tokens.

### Docker Compose

```bash
export TOKEN='YOUR_DISCORD_BOT_TOKEN'
./init-docker.sh
```

### Docker image

```bash
docker build -t sakamoto:latest .
docker run -e TOKEN='YOUR_DISCORD_BOT_TOKEN' sakamoto:latest
```

Prebuilt images are available as `ghcr.io/taichikuji/sakamoto:latest`; they can be used with Kubernetes, though this repository does not provide a Kubernetes manifest.

To enable the built-in updater, uncomment its service and the `discord` service labels in `docker-compose.yml`. `CRON_SCHEDULE` defaults to `0 0 * * *` (daily).

## Develop

```bash
pipenv install --dev
export TOKEN='YOUR_DISCORD_BOT_TOKEN'
pipenv run python main.py
```

Extensions live in `extensions/`, grouped by responsibility:

- `core/` contains always-on administration such as loading, syncing, and shutdown.
- `audio/`, `moderation/`, `community/`, `integrations/`, and `general/` contain user-facing domains.

See the [contribution guide](.github/CONTRIBUTING.md), [domain context](CONTEXT.md), and [wiki](https://github.com/taichikuji/Sakamoto/wiki/).

## Dependencies

Can be seen @ [Pipfile](https://github.com/taichikuji/Sakamoto/blob/main/Pipfile)
