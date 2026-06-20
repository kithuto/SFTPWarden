# Security Policy

SFTPWarden is infrastructure software. Please treat vulnerabilities, accidental
secret exposure, and unsafe deployment patterns seriously.

## Supported Versions

Before the first stable release, only the current `main` development line is
supported.

After v1.0, supported versions will be documented here.

## Reporting a Vulnerability

Do not open a public issue for vulnerabilities.

When the repository is public, use the GitHub private vulnerability reporting or
security advisory flow. Until then, contact the project owner privately.

Please include:

- affected version or commit;
- affected component: CLI, runtime, watcher, provider, Docker image, docs, or CI;
- steps to reproduce;
- expected impact;
- logs or screenshots with secrets redacted;
- whether the issue is already public or exploited.

## What Counts as Security-Sensitive?

Examples:

- plaintext secrets written to disk or logs;
- private keys or DSNs included in examples, tests, images, or docs;
- remote command injection;
- unsafe SSH or rsync command construction;
- Docker watcher mounting broad host credentials;
- broken chroot permissions;
- provider mutations that bypass validation;
- authentication or disabled-user behavior that grants unintended access.

## Deployment Responsibility

SFTPWarden provides OpenSSH chroot isolation inside a container. It does not replace:

- host hardening;
- network firewalling;
- patch management;
- backups;
- log monitoring;
- secret management;
- an independent review before internet-facing production use.

Do not expose experimental deployments to the public internet without a review of
the host, firewall, Docker configuration, SSH settings, and provider data.
