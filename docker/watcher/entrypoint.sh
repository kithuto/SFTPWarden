#!/bin/sh
set -eu

SSH_SOURCE_DIR=/run/sftpwarden-watcher/ssh
SSH_WORK_DIR=/tmp/sftpwarden-watcher/ssh
KNOWN_HOSTS_SOURCE=/run/sftpwarden-watcher/known_hosts

if [ -d "$SSH_SOURCE_DIR" ]; then
    rm -rf "$SSH_WORK_DIR"
    mkdir -p "$SSH_WORK_DIR"
    cp -R "$SSH_SOURCE_DIR"/. "$SSH_WORK_DIR"/
    find "$SSH_WORK_DIR" -type d -exec chmod 0700 '{}' +
    find "$SSH_WORK_DIR" -type f -exec chmod 0600 '{}' +
fi

if [ -f "$KNOWN_HOSTS_SOURCE" ]; then
    mkdir -p /root/.ssh
    cp "$KNOWN_HOSTS_SOURCE" /root/.ssh/known_hosts
    chmod 0644 /root/.ssh/known_hosts
fi

exec sftpwarden watch "$@"
