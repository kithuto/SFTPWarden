FROM python:3.14-alpine AS watcher

LABEL org.opencontainers.image.title="SFTPWarden watcher" \
      org.opencontainers.image.description="Local-to-remote SFTPWarden config watcher" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk add --no-cache openssh-client rsync tini

WORKDIR /opt/sftpwarden
COPY pyproject.toml README.md LICENSE ./
COPY sftpwarden ./sftpwarden
RUN pip install --no-cache-dir ".[watch]" \
    && rm -rf /root/.cache /opt/sftpwarden

COPY docker/watcher/entrypoint.sh /usr/local/bin/sftpwarden-watcher-entrypoint
RUN chmod 0755 /usr/local/bin/sftpwarden-watcher-entrypoint

ENTRYPOINT ["tini", "--", "sftpwarden-watcher-entrypoint"]
