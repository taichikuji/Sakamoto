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

By default, Docker Compose builds Sakamoto locally. On an existing installation
with root-owned data from an older release, migrate the volume ownership once
before running `./init-docker.sh`:

```bash
docker compose run --rm --user root --entrypoint chown discord -R sakamoto:sakamoto /usr/src/app/data
```

To enable automatic updates, uncomment the published `ghcr.io/taichikuji/sakamoto:latest` image, the Watchtower label, and the `updater` service in `docker-compose.yml`. The updater uses the maintained `nickfedor/watchtower` fork, only watches labelled containers, and removes old images after successful updates.

Watchtower checks daily at midnight UTC by default. Override its six-field cron expression with `WATCHTOWER_SCHEDULE`.

### Docker image

```bash
docker build -t sakamoto:latest .
docker run -e TOKEN='YOUR_DISCORD_BOT_TOKEN' sakamoto:latest
```

Prebuilt images are available as `ghcr.io/taichikuji/sakamoto:latest`; they can be used with Kubernetes, though this repository does not provide a Kubernetes manifest.

## Develop

```bash
pipenv install --dev
export TOKEN='YOUR_DISCORD_BOT_TOKEN'
pipenv run python main.py
```

See the [contribution guide](.github/CONTRIBUTING.md), [domain context](CONTEXT.md), and [wiki](https://github.com/taichikuji/Sakamoto/wiki/).

## Features

Extensions live in `extensions/`, grouped by responsibility:

**Audio**

- `audio.music` — Search and play songs or audio URLs; manage the queue, skip tracks, or stop playback.
- `audio.radio` — Search Radio Garden stations or play a random station.

**Community**

- `community.lobby` — Create and clean up temporary voice lobbies from a configured generator channel.
- `community.redirect` — Rewrite supported X, Bluesky, TikTok, and Instagram links to alternative frontends.

**Core**

- `core.analytics` — Give the application owner daily aggregate slash-command success and failure counts through `/analytics`.
- `core.loader` — Let the application owner load, unload, or reload extensions at runtime.
- `core.shutdown` — Let the application owner shut down the bot gracefully.
- `core.sync` — Sync global and server application commands as the bot owner.

**General**

- `general.help` — Browse available commands or get details about one.
- `general.info` — View bot information and uptime.
- `general.ping` — Check bot latency.

**Integrations**

- `integrations.anilist` — Search anime, manga, characters, and users; browse rankings and weekly airing schedules.
- `integrations.steam` — Link a Steam account and get join links for joinable Steam game lobbies.

**Moderation**

- `moderation.clear` — Bulk-delete up to 100 messages, optionally filtering by member.
- `moderation.honeypot` — Softban non-admins who post in a configured bait channel, request deletion of their last hour of messages, remove the bait post, and optionally log the event.
- `moderation.votekick` — Let members vote to remove someone from their current voice channel.

## Dependencies

Can be seen @ [Pipfile](https://github.com/taichikuji/Sakamoto/blob/main/Pipfile)
