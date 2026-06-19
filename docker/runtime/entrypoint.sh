#!/bin/sh
set -eu

HOST_KEYS_DIR="${SFTPWARDEN_HOST_KEYS_DIR:-/etc/sftpwarden/host_keys}"
mkdir -p "$HOST_KEYS_DIR" /etc/sftpwarden/authorized_keys /var/lib/sftpwarden /data /run/sshd
chmod 700 "$HOST_KEYS_DIR"

if [ ! -f "$HOST_KEYS_DIR/ssh_host_ed25519_key" ]; then
  ssh-keygen -q -t ed25519 -N "" -f "$HOST_KEYS_DIR/ssh_host_ed25519_key"
fi

if [ ! -f "$HOST_KEYS_DIR/ssh_host_rsa_key" ]; then
  ssh-keygen -q -t rsa -b 4096 -N "" -f "$HOST_KEYS_DIR/ssh_host_rsa_key"
fi

sftpwarden runtime refresh --config "$SFTPWARDEN_CONFIG"
sftpwarden runtime sync --config "$SFTPWARDEN_CONFIG" &

exec "$@"

