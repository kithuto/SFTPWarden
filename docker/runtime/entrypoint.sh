#!/bin/sh
set -eu

NOFILE_LIMIT="${SFTPWARDEN_NOFILE_LIMIT:-65536}"

case "$NOFILE_LIMIT" in
  ""|*[!0-9]*)
    echo "SFTPWARDEN_NOFILE_LIMIT must be a positive integer" >&2
    exit 1
    ;;
esac

if [ "$NOFILE_LIMIT" -lt 1 ]; then
  echo "SFTPWARDEN_NOFILE_LIMIT must be a positive integer" >&2
  exit 1
fi

CURRENT_NOFILE="$(ulimit -n)"
if [ "$CURRENT_NOFILE" = "unlimited" ] || [ "$CURRENT_NOFILE" -gt "$NOFILE_LIMIT" ]; then
  ulimit -n "$NOFILE_LIMIT"
fi

mkdir -p /etc/sftpwarden/authorized_keys /var/lib/sftpwarden /data /run/sshd
python -m sftpwarden.runtime.recovery
python -m sftpwarden.runtime.host_keys

exec python -m sftpwarden.runtime.supervisor "$@"
