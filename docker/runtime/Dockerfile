FROM python:3.14-alpine AS runtime

LABEL org.opencontainers.image.title="SFTPWarden" \
      org.opencontainers.image.description="Lightweight OpenSSH SFTP runtime with declarative user provisioning" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SFTPWARDEN_CONFIG=/etc/sftpwarden/sftpwarden.yaml

RUN apk add --no-cache openssh-server shadow tini \
    && mkdir -p /etc/sftpwarden/host_keys /etc/sftpwarden/authorized_keys /var/lib/sftpwarden /data /run/sshd

WORKDIR /opt/sftpwarden
COPY pyproject.toml README.md LICENSE ./
COPY sftpwarden ./sftpwarden
RUN PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore \
    pip install --no-cache-dir --no-compile ".[mysql,postgres,mongodb]" \
    && rm -rf /root/.cache /opt/sftpwarden \
        /usr/local/bin/pip* \
        /usr/local/lib/python3.14/ensurepip \
        /usr/local/lib/python3.14/site-packages/pip* \
    && find /usr/local -depth -type d -name __pycache__ -exec rm -rf '{}' + \
    && find /usr/local -type f -name '*.pyc' -delete

COPY docker/runtime/sshd_config.template /etc/ssh/sshd_config
COPY docker/runtime/entrypoint.sh /usr/local/bin/sftpwarden-entrypoint
RUN chmod 0755 /usr/local/bin/sftpwarden-entrypoint

EXPOSE 22
ENTRYPOINT ["tini", "--", "sftpwarden-entrypoint"]
CMD ["/usr/sbin/sshd", "-D", "-e"]
