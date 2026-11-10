ARG PYTHON_BASE=python:3.14.10-alpine3.24@sha256:a18b20ad69e74df6cf6d2e1b1a260d7a1ecf5380fd83b310f83ba88c461cf894
FROM ${PYTHON_BASE} AS base

LABEL org.opencontainers.image.title="SFTPWarden" \
      org.opencontainers.image.description="Lightweight OpenSSH SFTP runtime with declarative user provisioning" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SFTPWARDEN_CONFIG=/etc/sftpwarden/sftpwarden.yaml

RUN apk add --no-cache openssh-server shadow tini 'zlib>=1.4-r0' \
    && mkdir -p /etc/sftpwarden/host_keys /etc/sftpwarden/authorized_keys /var/lib/sftpwarden /data /run/sshd
COPY docker/release-constraints.txt /release-constraints.txt

COPY docker/runtime/sshd_config.template /etc/ssh/sshd_config
COPY docker/runtime/newusers.pam /etc/pam.d/newusers
COPY docker/runtime/entrypoint.sh /usr/local/bin/sftpwarden-entrypoint
RUN chmod 0755 /usr/local/bin/sftpwarden-entrypoint

EXPOSE 22
ENTRYPOINT ["tini", "--", "sftpwarden-entrypoint"]
CMD ["/usr/sbin/sshd", "-D", "-e"]

FROM base AS runtime-source
WORKDIR /opt/sftpwarden
COPY pyproject.toml README.md LICENSE ./
COPY sftpwarden ./sftpwarden
RUN PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore \
    pip install --no-cache-dir --no-compile -c /release-constraints.txt ".[mysql,postgres,mongodb]" \
    && rm -rf /root/.cache /opt/sftpwarden /release-constraints.txt \
        /usr/local/bin/pip* \
        /usr/local/lib/python3.14/ensurepip \
        /usr/local/lib/python3.14/site-packages/pip* \
    && find /usr/local -depth -type d -name __pycache__ -exec rm -rf '{}' + \
    && find /usr/local -type f -name '*.pyc' -delete

FROM base AS runtime-wheel
COPY dist/*.whl /candidate/
RUN set -eu; set -- /candidate/*.whl; test "$#" -eq 1; \
    PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore \
    pip install --no-cache-dir --no-compile -c /release-constraints.txt "$1[mysql,postgres,mongodb]" \
    && rm -rf /candidate /release-constraints.txt /root/.cache /usr/local/bin/pip* \
        /usr/local/lib/python3.14/ensurepip \
        /usr/local/lib/python3.14/site-packages/pip* \
    && find /usr/local -depth -type d -name __pycache__ -exec rm -rf '{}' + \
    && find /usr/local -type f -name '*.pyc' -delete

FROM runtime-source AS runtime
