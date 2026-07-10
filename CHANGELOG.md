# Changelog

All notable changes to SFTPWarden will be documented in this file.

The format follows Keep a Changelog, and this project uses Semantic Versioning.

## [Unreleased]

### Planned

This roadmap is directional and may change as SFTPWarden receives operational
feedback. It summarizes the main public roadmap through v2.0.

#### v1.4 - Audit and Transfer Visibility

- Add local JSONL audit logging for user, key, provider, deploy, refresh,
  backup, restore, watcher, and runtime sync operations.
- Add audit commands for listing, tailing, filtering, and exporting events.
- Add transfer visibility commands for recent SFTP file activity.
- Add richer runtime status with provider, user, key, sync, backup, and error
  context.

#### v1.5 - Security Hardening

- Add production-oriented `sftpwarden security check` profiles and strict mode.
- Add secret-file support for DSNs and sensitive provider settings.
- Add host key fingerprint and assisted host key rotation workflows.
- Strengthen release security with signing, provenance, SBOM, audit, and scan
  gates where practical.

#### v1.6 - Access Policies

- Add first-class user access modes such as read/write, upload-only,
  download-only, and disabled.
- Document and test runtime enforcement boundaries for OpenSSH/internal-sftp.
- Prepare the model for per-path policies, file pattern filters, and atomic
  upload workflows.

#### v1.7 - User Lifecycle and Quotas

- Add user expiry, review dates, tags, owners, disabled reasons, and lifecycle
  metadata.
- Add inactive-user review and bulk disable dry-run workflows.
- Add quota status, quota recalculation, and quota configuration groundwork.

#### v1.8 - Retention and Cleanup

- Add retention policies for deleting, archiving, or moving old SFTP data.
- Require safe dry-run output before destructive cleanup.
- Audit retention actions and include readable archive manifests.

#### v1.9 - Diagnostics and Supportability

- Add redacted diagnostics bundles for support and incident response.
- Expand `doctor` with deeper local, remote, Compose, Kubernetes, and Helm checks.
- Centralize secret redaction for audit, diagnostics, and security output.

#### v2.0 - API and OpenAPI

- Add an optional management API under `/api/v1`.
- Publish OpenAPI documentation and stable API schemas.
- Reuse existing CLI service-layer behavior for API operations.
- Add token-based authentication and audit API mutations.

## [1.3.0] - 2026-07-08

### Added

- Added schema v2 named SSH keys with name, fingerprint, comment, disabled
  state, timestamps, expiry, source, metadata, rotation, rename, import, and
  per-key enable/disable workflows.
- Added `sftpwarden init --user-schema 1|2`; new projects default to schema v2,
  while quick-start/simple deployments can choose schema v1 explicitly.
- Added command-first user/key commands under `sftpwarden user ...` and
  `sftpwarden user key ...`.
- Added provider schema inspection and explicit migration commands:
  `provider schema show`, `provider schema migrate`, and `provider keys migrate`.

### Changed

- Schema v1 `public_keys` remains a supported simple user format, not a
  deprecated legacy mode.
- Runtime refresh writes only active keys to `authorized_keys`; disabled and
  expired named keys are excluded.
- YAML, CSV, SQLite, MySQL, MariaDB, PostgreSQL, and MongoDB providers can read
  and write schema v1 or schema v2 data.

## [1.2.1] - 2026-06-29

### Added

- Configurable Kubernetes user data PVC sizing for manifests and generated Helm
  values.
- Configurable Docker Compose healthcheck timings and Kubernetes/Helm runtime
  probe timings.
- Pluggable watcher backends with auto-detection for Windows Task Scheduler,
  macOS launchd, Linux systemd/OpenRC/runit/supervisord, and Docker fallback.
- Backend-specific watcher uninstall plans for explicit uninstall, backend
  replacement, and removal of the last remote local-sync context.
- Kubernetes and Helm init now create the default namespace automatically with
  `--yes`, while still supporting explicit namespace selection and strict
  no-create behavior.

### Changed

- Watcher installs now default to `auto` instead of assuming systemd.
- Windows native watcher sync uses OpenSSH `scp` for provider file uploads.
- Kubernetes YAML/CSV providers are treated as declarative deploy inputs:
  deploy, `kube apply`, and Helm upgrades copy the rendered local provider file
  into the provider PVC during rollout; database-backed Kubernetes providers
  remain refreshable through `kubectl exec`.

### Fixed

- Improved Windows and cross-platform CLI/test compatibility outside the Linux
  runtime container.
- Runtime containers now clamp inherited `nofile` limits before starting OpenSSH
  so Kubernetes chrooted `internal-sftp` sessions do not stall on platforms that
  expose extremely high open-file limits.

## [1.2.0] - 2026-06-27

### Added

- Official Kubernetes manifests and Helm chart for single-runtime deployments.
- `sftpwarden kube` and `sftpwarden helm` command groups.
- Kubernetes and Helm deployment targets for `sftpwarden init` and `sftpwarden deploy`.
- Kubernetes ConfigMap, Secret, PVC, Service, StatefulSet, and runtime health probe rendering.
- Documentation and examples for Kubernetes deployment and provider recommendations.

## [1.1.0] - 2026-06-23

### Added

- SQLite, MariaDB, and MongoDB providers.
- Provider import, export, and copy commands for moving users between providers.
- Project backup and restore commands for config, provider snapshots, host keys,
  and runtime state.
- Project and runtime health checks, plus Docker Compose healthcheck generation.
- Examples and documentation for SQLite, MariaDB, MongoDB, transfer, backup,
  restore, and health workflows.

### Changed

- Runtime Docker image installs official database provider extras for MySQL,
  MariaDB, PostgreSQL, and MongoDB.
- Watcher and deploy logic treat SQLite as a local provider file that can be
  synchronized for remote local-sync contexts.

## [1.0.0] - 2026-06-20

### Added

- Production-ready Typer/Rich CLI for init, validate, plan, deploy, compose, refresh,
  sync, watch, contexts, providers, users, watcher management, and runtime commands.
- Local, remote local-sync, and remote-only context workflows with Docker Compose,
  SSH, rsync, critical-context confirmation, and active context handling.
- YAML, CSV, MySQL, and PostgreSQL providers with user listing, add, update,
  remove, SQL table creation support, and provider registry.
- Lightweight OpenSSH runtime image, watcher image, chroot-oriented user isolation,
  UID/GID allocation, persistent runtime state, host key persistence, and refresh
  planning.
- Public documentation, examples, Sphinx site, CI, Docker, release, and security
  workflows for the first stable release.

### Security

- Conservative defaults for secrets, plaintext password rejection in provider data,
  explicit destructive user-data deletion, restricted OpenSSH SFTP runtime, Docker
  image scans, dependency audit, SBOM generation, and OpenSSF Scorecard workflow.
