FROM python:3.14-alpine AS watcher

LABEL org.opencontainers.image.title="SFTPWarden watcher" \
      org.opencontainers.image.description="Local-to-remote SFTPWarden config watcher" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apk add --no-cache openssh-client rsync tini

WORKDIR /opt/sftpwarden
COPY pyproject.toml README.md LICENSE ./
COPY sftpwarden ./sftpwarden
RUN PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore \
    pip install --no-cache-dir --no-compile "." \
    && rm -rf /root/.cache /opt/sftpwarden \
        /usr/local/bin/pip* \
        /usr/local/lib/python3.14/ensurepip \
        /usr/local/lib/python3.14/site-packages/pip* \
    && find /usr/local -depth -type d -name __pycache__ -exec rm -rf '{}' + \
    && find /usr/local -type f -name '*.pyc' -delete

COPY docker/watcher/entrypoint.sh /usr/local/bin/sftpwarden-watcher-entrypoint
RUN chmod 0755 /usr/local/bin/sftpwarden-watcher-entrypoint

ENTRYPOINT ["tini", "--", "sftpwarden-watcher-entrypoint"]
