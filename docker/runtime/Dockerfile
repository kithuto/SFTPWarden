FROM python:3.14-alpine AS runtime

LABEL org.opencontainers.image.title="SFTPWarden" \
      org.opencontainers.image.description="Lightweight OpenSSH SFTP runtime with declarative user provisioning" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SFTPWARDEN_CONFIG=/etc/sftpwarden/sftpwarden.yaml

RUN apk add --no-cache openssh-server shadow tini \
    && mkdir -p /etc/sftpwarden/host_keys /etc/sftpwarden/authorized_keys /var/lib/sftpwarden /data /run/sshd

WORKDIR /opt/sftpwarden
COPY pyproject.toml README.md LICENSE ./
COPY sftpwarden ./sftpwarden
RUN pip install --no-cache-dir . \
    && rm -rf /root/.cache /opt/sftpwarden

COPY docker/runtime/sshd_config.template /etc/ssh/sshd_config
COPY docker/runtime/entrypoint.sh /usr/local/bin/sftpwarden-entrypoint
RUN chmod 0755 /usr/local/bin/sftpwarden-entrypoint

EXPOSE 22
ENTRYPOINT ["tini", "--", "sftpwarden-entrypoint"]
CMD ["/usr/sbin/sshd", "-D", "-e"]
