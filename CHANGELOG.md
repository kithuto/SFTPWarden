# Changelog

All notable changes to SFTPWarden will be documented in this file.

The format follows Keep a Changelog, and this project uses Semantic Versioning.

## [Unreleased]

### Planned

This roadmap is directional and may change as SFTPWarden receives operational
feedback. The goal is to keep future releases compatible with the current CLI-first
workflow while making the project easier to operate, recover, observe, and adopt
in production environments.

#### v1.1 - Providers, Backup, Import/Export, and Health

- Add dedicated MariaDB and MongoDB provider support.
- Keep MariaDB familiar for MySQL-style deployments while exposing it as its own
  provider for a clearer user experience.
- Add MongoDB support using a `sftp_users` collection, a unique `username` index,
  and configurable collection names.
- Add optional installation extras for MariaDB and MongoDB users.
- Add provider portability commands so users can export, import, and copy users
  between supported providers.
- Require explicit merge or replace behavior for imports and provider copies so
  user data changes are intentional.
- Add backup and restore commands for project configuration, exported users,
  generated Compose files, host keys, and runtime state.
- Exclude user file data from backups by default, with an explicit opt-in path for
  teams that need to include it.
- Create a safety backup before restore operations overwrite project files.
- Add project and runtime health checks, including a Compose healthcheck powered by
  the runtime health command.

#### v1.2 - Kubernetes

- Add an official Helm chart under `charts/sftpwarden`.
- Add Kubernetes manifests and examples for teams that do not use Helm.
- Run SFTPWarden as a single-replica `StatefulSet` with persistent volumes for
  data, runtime state, and host keys.
- Use ConfigMaps for non-sensitive configuration and Secrets for credentials,
  DSNs, and other sensitive values.
- Add liveness, readiness, and startup probes based on `sftpwarden runtime health`.
- Support configurable Kubernetes Services such as `ClusterIP`, `NodePort`, and
  `LoadBalancer`.
- Document YAML and CSV providers as GitOps/deploy-time flows in Kubernetes.
- Recommend SQL, MariaDB, PostgreSQL, or MongoDB providers for dynamic user
  mutation in Kubernetes environments.
- Keep the Kubernetes scope intentionally small: no Operator and no multi-node
  active-active mode.

#### v1.3 - Audit and Observability

- Add JSONL audit logging for operational and user-management changes.
- Record important events such as user add/update/remove, deploy, refresh, backup,
  restore, import/export, provider changes, and security checks.
- Add audit commands to list, tail, and export audit events.
- Add richer runtime status output, including last refresh details, runtime
  fingerprint, active and disabled user counts, and recent errors.
- Add Prometheus text-format runtime metrics.
- Keep observability usable without requiring a separate server or control plane.
- Prepare audit, status, and metrics data so the future web console can reuse the
  same source of truth.

#### v1.4 - Advanced Security and Supply Chain

- Add optional SSH host key pinning for remote workflows.
- Show host key fingerprints during remote init, deploy, and deep doctor checks.
- Add assisted rotation workflows for user keys and host keys.
- Add support for secret files for DSNs and credentials.
- Add `sftpwarden security check` for production-oriented validation.
- Check permissions, secrets, chroot settings, host keys, provider configuration,
  Compose output, critical contexts, and sensitive paths.
- Sign Docker images with Sigstore/cosign.
- Add release provenance and SLSA-oriented supply chain metadata while keeping
  existing audits, scans, SBOM generation, and Scorecard checks.

#### v1.5 - Integrations and Policies

- Add a read-only HTTP JSON provider for teams that already expose user data
  through an internal service.
- Add user templates for upload directories, disabled state, comments, auth
  defaults, and optional UID/GID settings.
- Add logical groups or tags to help organize users without turning SFTPWarden
  into a full identity platform.
- Add provider schema/version migration support for SQL, MariaDB, PostgreSQL, and
  MongoDB providers.
- Add `sftpwarden provider doctor` for provider-specific diagnostics.
- Keep LDAP, SCIM, and SAML outside the roadmap unless real user demand makes
  them worth adding.
- Prepare the data and permission model for the optional v2.0 web console.

#### v2.0 - Enterprise Web Console

- Add an official, optional web console for teams that want browser-based daily
  operations.
- Keep SFTPWarden fully usable from the CLI; the web console will not replace the
  current command-line workflows.
- Keep clear boundaries between the OpenSSH runtime, the CLI, the management API,
  and the web console.
- Share core management logic between the CLI and the web console so behavior
  stays consistent across interfaces.
- Add an optional web extra, a `sftpwarden web` command, and a separate
  `sftpwarden-console` container.
- Add Docker Compose and Helm examples for enabling the console.
- Add an HTTP management API under `/api/v1` with OpenAPI documentation.
- Provide a dashboard for contexts, runtime health, providers, users, recent
  deploy/refresh/backup/restore activity, and alerts.
- Add browser-based user management for creating, editing, disabling, removing,
  validating, planning, and refreshing user changes.
- Add provider tools for connection validation, schema/table/collection inspection,
  import/export/copy workflows, and mutability visibility.
- Add context, deploy, runtime, backup/restore, audit, health, security, and doctor
  views that map back to existing CLI concepts.
- Add basic RBAC roles such as admin, operator, and auditor, with per-context
  permissions and mandatory audit trails.
- Add local initial login and prioritize OIDC for enterprise authentication.

#### Not Planned

- Multi-node active-active SFTP runtime.
- Kubernetes Operator.
- Dropbox-style file sharing dashboard.
- Full custom IAM system.
- Mandatory control plane.
- A web console dependency for existing CLI users.
- Breaking current workflows such as `sftpwarden init dev`, `context add`,
  `watch`, `refresh`, or existing local and remote context usage.

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
