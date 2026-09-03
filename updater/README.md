# Sakamoto updater

The optional Docker Compose updater replaces the archived Watchtower workflow. It requires Docker and the legacy `docker-compose` command.

Enable the `updater` service and the `com.taichikuji.sakamoto.enable=true` label in the root `docker-compose.yml`, then deploy with `./init-docker.sh`.

`CRON_SCHEDULE` uses standard cron syntax and defaults to daily at midnight:

```yaml
- CRON_SCHEDULE=${CRON_SCHEDULE:-0 0 * * *}
```

For every run, the updater finds labelled services in its Compose project, pulls their images, recreates only changed services, and prunes old images after an update. Kubernetes deployments need a separate update strategy.
