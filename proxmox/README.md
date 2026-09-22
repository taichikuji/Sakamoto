# Proxmox LXC deployment

Run this on the Proxmox host to create an unprivileged Debian 13 LXC and install Sakamoto:

```bash
bash -c "$(wget -qO- https://raw.githubusercontent.com/taichikuji/Sakamoto/main/proxmox/sakamoto.sh)"
```

The installer uses [community-scripts](https://github.com/community-scripts/ProxmoxVE), defaults to 2 CPU cores, 1 GB RAM, and 4 GB disk, installs Sakamoto at `/opt/Sakamoto`, and enables (without starting) `Sakamoto.service`.

## Finish setup

```bash
pct enter <CTID>
nano /opt/Sakamoto/.env
systemctl start Sakamoto
systemctl status Sakamoto
```

Replace the placeholder `TOKEN`; `STEAM_TOKEN` is optional.

## Update

Re-run the installer for an existing container. Its updater upgrades Debian, pulls the repository, runs `pipenv install --deploy`, and restarts `Sakamoto.service`. Standard [community-scripts LXC updates](https://github.com/community-scripts/ProxmoxVE) remain compatible for system updates.
