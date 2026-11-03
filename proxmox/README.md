# Proxmox LXC deployment

Run this on the Proxmox host to create an unprivileged Debian 13 LXC and install Sakamoto:

```bash
bash -c "$(wget -qO- https://raw.githubusercontent.com/taichikuji/Sakamoto/main/proxmox/sakamoto.sh)"
```

The installer uses [community-scripts](https://github.com/community-scripts/ProxmoxVE), defaults to 2 CPU cores, 1 GB RAM, and 4 GB disk, installs Python 3.15.2 and Sakamoto at `/opt/Sakamoto`, and enables (without starting) `Sakamoto.service`. uv provisions Python; Pipenv installs the locked project packages.

## Finish setup

```bash
pct enter <CTID>
nano /opt/Sakamoto/.env
systemctl start Sakamoto
systemctl status Sakamoto
```

Replace the placeholder `TOKEN`; `STEAM_TOKEN` is optional.

## Update

Re-run the installer for an existing container. Its updater upgrades Debian, installs Python 3.15.2, pulls the repository, recreates the Pipenv environment if its Python version differs, runs `pipenv install --deploy`, and restarts `Sakamoto.service`. Existing `.env` settings and SQLite data remain in place. Standard [community-scripts LXC updates](https://github.com/community-scripts/ProxmoxVE) remain compatible for system updates.
