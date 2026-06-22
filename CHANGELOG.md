# Changelog

All notable changes to SFTPWarden will be documented in this file.

The format follows Keep a Changelog, and this project uses Semantic Versioning.

## [Unreleased]

### Planned

This roadmap is directional and may change as SFTPWarden receives operational
feedback. It summarizes the main features planned for future releases.

#### v1.1 - Providers, Backup, Import/Export, and Health

- Add SQLite, MariaDB, and MongoDB providers.
- Add provider import, export, and copy commands.
- Add backup and restore commands.
- Add project and runtime health checks.

#### v1.2 - Kubernetes

- Add an official Helm chart.
- Add Kubernetes manifests and examples.
- Add Kubernetes probes and service configuration.
- Document provider usage for Kubernetes deployments.

#### v1.3 - Audit and Observability

- Add audit logging.
- Add audit commands for listing, tailing, and exporting events.
- Add richer runtime status output.
- Add runtime metrics.

#### v1.4 - Advanced Security and Supply Chain

- Add SSH host key pinning.
- Add assisted key rotation workflows.
- Add support for secret files.
- Add production-oriented security checks.
- Add Docker image signing and release provenance.

#### v1.5 - Integrations and Policies

- Add a read-only HTTP JSON provider.
- Add user templates.
- Add user groups or tags.
- Add provider schema migrations.
- Add provider diagnostics.

#### v2.0 - Enterprise Web Console

- Add an optional web console.
- Add a management API with OpenAPI documentation.
- Add dashboard, user, provider, context, deploy, runtime, backup, audit, health,
  security, and diagnostics views.
- Add basic RBAC.
- Add local login and OIDC support.

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
