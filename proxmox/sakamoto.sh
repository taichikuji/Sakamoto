#!/usr/bin/env bash
source <(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/misc/build.func)
# Copyright (c) 2024-2026 taichikuji
# Author: taichikuji
# License: MIT | https://github.com/taichikuji/Sakamoto/raw/main/LICENSE
# Source: https://github.com/taichikuji/Sakamoto

APP="Sakamoto"
var_tags="${var_tags:-discord;bot;python}"
var_cpu="${var_cpu:-2}"
var_ram="${var_ram:-1024}"
var_disk="${var_disk:-4}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
var_unprivileged="${var_unprivileged:-1}"

header_info "$APP"
variables
color
catch_errors

# Use the base Debian installer for container provisioning,
# then install the application via lxc-attach after build.
var_install="debian-install"

function update_script() {
  header_info
  check_container_storage
  check_container_resources

  if [[ ! -d /opt/Sakamoto ]]; then
    msg_error "No ${APP} Installation Found!"
    exit
  fi

  msg_info "Updating base system"
  $STD apt-get update
  $STD apt-get upgrade -y
  msg_ok "Base system updated"

  msg_info "Updating Sakamoto"
  cd /opt/Sakamoto
  $STD git pull
  $STD pipenv install --deploy
  msg_ok "Sakamoto updated"

  msg_info "Restarting Sakamoto service"
  systemctl restart Sakamoto
  msg_ok "Sakamoto service restarted"

  msg_ok "Updated successfully!"
  exit
}

start
build_container

msg_info "Installing Sakamoto dependencies"
$STD lxc-attach -n "$CTID" -- bash -c "\
  apt-get update && \
  apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    pipenv \
    git \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    libopus-dev \
    build-essential"
msg_ok "Sakamoto dependencies installed"

msg_info "Cloning Sakamoto repository"
$STD lxc-attach -n "$CTID" -- bash -c "\
  git clone https://github.com/taichikuji/Sakamoto.git /opt/Sakamoto"
msg_ok "Sakamoto repository cloned"

msg_info "Installing Python packages"
$STD lxc-attach -n "$CTID" -- bash -c "\
  cd /opt/Sakamoto && pipenv install --deploy"
msg_ok "Python packages installed"

msg_info "Setting up Sakamoto service"
lxc-attach -n "$CTID" -- bash -c 'cat > /etc/systemd/system/Sakamoto.service << EOF
[Unit]
Description=Sakamoto Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/Sakamoto
EnvironmentFile=/opt/Sakamoto/.env
ExecStart=/usr/bin/pipenv run python main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF'

lxc-attach -n "$CTID" -- bash -c "\
  cat > /opt/Sakamoto/.env << 'ENVEOF'
# Required: Your Discord bot token
TOKEN=your_discord_bot_token_here

# Optional: Steam API token (for Steam-related commands)
# STEAM_TOKEN=your_steam_api_token_here
ENVEOF"

$STD lxc-attach -n "$CTID" -- bash -c "\
  systemctl daemon-reload && \
  systemctl enable Sakamoto"
msg_ok "Sakamoto service configured"

description

msg_ok "Completed successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e ""
echo -e "${INFO}${RD} IMPORTANT: The bot will NOT start until you configure your Discord token!${CL}"
echo -e ""
echo -e "${INFO}${YW} Follow these steps to configure and start the bot:${CL}"
echo -e "${TAB}${YW} 1. Enter the LXC container:${CL}"
echo -e "${TAB}${TAB}${BGN}pct enter ${CTID}${CL}"
echo -e "${TAB}${YW} 2. Edit the environment file:${CL}"
echo -e "${TAB}${TAB}${BGN}nano /opt/Sakamoto/.env${CL}"
echo -e "${TAB}${YW} 3. Replace ${BGN}your_discord_bot_token_here${CL}${YW} with your actual Discord bot token${CL}"
echo -e "${TAB}${YW} 4. (Optional) Add your Steam API token if you want Steam commands${CL}"
echo -e "${TAB}${YW} 5. Start the bot:${CL}"
echo -e "${TAB}${TAB}${BGN}systemctl start Sakamoto${CL}"
echo -e "${TAB}${YW} 6. Check the bot status:${CL}"
echo -e "${TAB}${TAB}${BGN}systemctl status Sakamoto${CL}"
