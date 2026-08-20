#!/bin/bash

# This script automates the process of cleaning up Docker containers, pulling the latest code from the default branch of a Git repository, and rebuilding and restarting the Docker environment.
# It includes the following features:
# - Performs cleanup of Docker resources, gc stuff.
# - You can run "--reset" in case something is acting up.
# - Upon execution without flags it will deploy according to the docker compose YAML.
# - Every step is logged for easy troubleshooting and feedback.

# Usage:
# - Run the script without arguments to update and restart the Docker environment.
# - Use the "--prune" flag to perform a full cleanup before updating and restarting.
# - Use the "--reset" flag to reset the repository without affecting the Docker environment.
# - Use the "--help" flag to display this help message.

SUCCESS='\e[39m\e[42m[SUCCESS]\e[49m \e[32m'
ERROR='\e[39m\e[41m[ERROR]\e[49m \e[31m'
export COMPOSE_BAKE=true

log() {
    echo -e "$1 $2"
}

show_help() {
    sed -n '10,14s/^# //p' "$0"
}

prune() {
    if docker compose down --rmi local --volumes --remove-orphans; then
        log "$SUCCESS" "Docker cleanup successful"
    else
        log "$ERROR" "Docker cleanup failed"; exit 1
    fi
}

reset_git() {
    default_branch=$(git remote show origin | grep 'HEAD branch' | awk '{print $NF}')
    if git fetch --all && git reset --hard "origin/$default_branch"; then
        log "$SUCCESS" "Git reset to latest commit successful"
    else
        log "$ERROR" "Git reset failed"; exit 1
    fi
}

cleanup_buildkit() {
    if docker ps -a --filter "name=buildx_buildkit" --format "table {{.Names}}" | grep buildx_buildkit | xargs -r docker rm -f 2>/dev/null; then
        log "$SUCCESS" "BuildKit containers removed"
    else
        log "$ERROR" "No BuildKit containers to remove"
    fi
}

# Check for --help flag
if [[ "$1" == "--help" ]]; then
    show_help
    exit 0
fi

# Check for --prune flag
if [[ "$1" == "--prune" ]]; then
    prune
    exit 0
fi

# Check for --reset flag
if [[ "$1" == "--reset" ]]; then
    reset_git
    exit 0
fi

# Clean old version
if docker compose down; then
    log "$SUCCESS" "Container removed"
else
    log "$ERROR" "Failed to remove containers"; exit 1
fi

# Pull latest version
if git fetch --all; then
    log "$SUCCESS" "Updated to latest commit"
else
    log "$ERROR" "Git update failed"; exit 1
fi

# Build, migrate persistent data ownership, and start new version
if docker compose build --force-rm && \
    docker compose run --rm --user root --entrypoint chown discord -R sakamoto:sakamoto /usr/src/app/data && \
    docker compose up -d; then
    cleanup_buildkit
    log "$SUCCESS" "Build and start successful"
else
    log "$ERROR" "Build/start failed"; exit 1
fi
