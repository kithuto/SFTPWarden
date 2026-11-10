# Changelog

All notable changes to SFTPWarden will be documented in this file.

The format follows Keep a Changelog. SFTPWarden uses three-part release numbers:
major versions identify substantial new product capabilities, minor versions add
capabilities within that direction, and patches correct defects. The planned
major milestones are the management API (2.0) and operations console (3.0).
This capability-based policy is not strict Semantic Versioning. Compatibility,
migrations and required operator actions are documented for every release,
independently of its number; a major release need not break existing deployments.

## [Unreleased]

### Planned

This future feature roadmap prioritizes reliable operation, then storage,
integration and administration. It is directional, has no delivery dates and
does not describe implemented functionality. Stabilization patches are managed
separately. Production support requires evidence from the affected real services
and deployment environments, not only mocked tests or rendered configuration.

| Version | Delivery | Planned outcome |
| --- | --- | --- |
| v1.5 | Audit and Transfer Visibility | Private segmented audit journal, observed SFTP activity, queries/export and operational status. |
| v1.6 | Security and Client Trust | Security profiles/checks, secret files, fingerprints, assisted rotation and safe client onboarding. |
| v1.7 | Diagnostics and Adoption | Local/remote doctor, provider diagnostics, redacted support bundles and adoption runbooks. |
| v1.8 | Backup and Recovery Operations | Encryption, verified backup destinations, retention, external scheduling and recovery drills. |
| v1.9 | Access Policies | Explicit, tested allowed/denied SFTP operations for supported access modes. |
| v1.10 | User Lifecycle and Governance | Account expiry, ownership, tags, reviews and evidence-based inactivity handling. |
| v1.11 | Metrics and Observability | Default internal metrics, official Grafana dashboard and optional external monitoring integrations. |
| v1.12 | Storage Usage and Capacity Alerts | Space/file measurement, freshness, thresholds and capacity alerts. |
| v1.13 | Reliable Notifications | Persistent webhook delivery, bounded retries, inspection and recovery. |
| v1.14 | Data Retention and Archiving | Reviewed document retention, execution-time checks and restorable archives. |
| v1.15 | Modular Storage, File Metadata and S3 | Primary document storage with per-user assignments, enforced ownership/permissions and confirmed S3 persistence. |
| v1.16 | Enforced Storage Quotas | Mandatory byte/file limits with concurrent reservations and recovery on supported managed write paths. |
| v1.17 | Google Cloud Storage | GCS document backend using the common metadata, permission, quota and recovery contracts. |
| v1.18 | Azure Blob Storage | Azure Blob document backend through the same contracts. |
| v1.19 | Enterprise Filesystem Integrations | Qualified shared filesystem and enterprise file-service combinations with explicit limitations. |
| v2.0 | Management API and OpenAPI | Optional API with authentication, operation/context authorization, stable contracts and durable jobs. |
| v2.1 | Identity and Delegated Administration | Administrative identities, roles, sessions, scoped delegation and optional OIDC. |
| v2.2 | Infrastructure as Code | Terraform, Ansible and CI with import, idempotency and explicit resource authority. |
| v2.3 | HTTP User Provider | Read-only authenticated HTTP JSON users, validated snapshots and last-applied preservation on errors. |
| v2.4 | Controlled Workflows | Authorized automation with input/resource limits, persistent results and recovery. |
| v2.5 | Quarantine and File Validation | Revision-safe validation/publication, with optional antivirus and PGP integrations. |
| v2.6 | SSH Certificates and CA Integration | Existing-CA interoperability, issuance, principals, revocation, rotation and protected key handling. |
| v3.0 | Operations Console | Optional API-only web administration after API, identity and operational contracts mature. |
| v3.1 | Kubernetes Operator | CRD reconciliation, useful status, explicit ownership and single-runtime support. |
| v3.2 | Kubernetes High Availability | Coordinated replicas, authorization, identities, storage metadata and quotas; operator optional. |
| v3.3 | Multi-client Administration | Common management with separate runtimes, storage, credentials and recovery resources per client. |

CLI-first operation and OpenSSH remain core. API, console, cloud dependencies
and Kubernetes automation stay optional. The console does not wait for HA or
the operator. Document storage starts with private user namespaces; external
cloud changes require explicit reconciliation. Contexts or namespaces alone do
not establish complete client isolation. An end-user file portal, LDAP/AD,
complex per-path policies and auditing changes outside the runtime are outside
these scheduled deliveries.

## [1.4.0] - 2026-11-11

### Added

- Add `upgrade` with stable-release discovery, previews, confirmation and separate
  CLI, watcher and deployment results. Self-update supports owning pip virtual
  environments, pipx and uv tool installations, preserving provider dependencies.
- Check watcher/runtime compatibility before updating. Incompatible installations
  require explicit consent to deploy all contexts; normal management updates leave
  servers running until explicit deploy. Custom images remain unchanged.
- Detect actual deployed versions, recommend a backup and orchestrate supported
  migrations during deploy, Kubernetes apply and Helm upgrade. Private receipts
  retain incomplete-operation evidence; completion requires runtime verification.
- Replace installed watchers with process verification and preserved checkpoints,
  retain inactive installations and avoid restarting an unchanged current watcher.
- Add bounded, cached release notices and `upgrade --status` for update recovery.

### Fixed

- Preserve all container security findings in JSON and SARIF reports while
  enforcing the existing HIGH/CRITICAL threshold separately. Report conversion
  errors and missing scan results fail the security job.
- Require Alpine's corrected `zlib` package in runtime and watcher images,
  including both source and release-wheel builds, addressing CVE-2026-4738.
- Make `kube doctor` check the configured host-key Secret or the default retained
  PVC, avoiding false warnings for valid identity configurations.
- Flush SQLite snapshots and restored files through writable descriptors on Windows,
  and protect temporary host-key validation files with owner-only Windows permissions.
- Apply deny-only authentication policies, enforce key expiry, and retain the last
  applied policy when provider input is missing or invalid.
- Supervise required runtime processes and report synchronization failures through
  readiness checks.
- Provision new accounts in a locked batch, verify their locked state and batch
  shared group membership before enabling access. Wake reconciliation on user-file changes while
  retaining periodic expiry checks and serial, bounded application.
- Protect provider and project configuration changes against concurrent writers.
- Allow Docker watchers to start with a read-only context registry. Native remote
  deployment uses OpenSSH file transfer on macOS and Windows, including paths with
  spaces and quotes.
- Report malformed YAML configurations through the private error boundary so
  recovery can classify damaged installations without exposing their contents.
- Coordinate provider schema upgrades with configuration updates and retain private
  recovery records when publication is interrupted. Schema changes require deploy.
- Resolve explicit provider DSN environment references, enforce configured transport
  protections, and keep driver credentials out of CLI error output.
- Prepare remote-only Compose deployments in containers matching the CLI release,
  including schema and provider-mount migration before recreation and readiness.
  Subsequent file-provider edits use the dedicated `provider/` directory.
- Capture applied runtime state from local/remote Compose and Kubernetes/Helm;
  verify complete archives, preserve damaged originals and separate file recovery
  from explicit service activation. External database recovery requires a protected
  destination and checked provider replacement.
- Refuse a data backup without runtime maintenance and retained identity, before
  publishing an archive that cannot restore service.
- Report insufficient host permissions before in-place recovery and retain the
  project owner's access when an administrator restores protected runtime files.
- Reject unsupported non-root chroot ownership and document reserved configuration
  fields without implying runtime effects.
- Retain Kubernetes host identity on persistent storage or an explicit Secret,
  prepare legacy identity before Pod replacement and retain managed claims through
  supported upgrade and uninstall/reinstall operations.
- Persist watcher delivery/application checkpoints and retry failed targets without
  another edit, including after watcher restart. Metadata-only edits do not refresh.
- Keep Docker watcher checkpoints and locks owned by the host operator so native
  CLI inspection and watcher takeover work with private permissions on Linux.
- Bound provider transport waits, reject missing MongoDB collections as invalid
  input and privately deliver explicitly selected remote environment files.
- Stop Kubernetes identity, backup and restore helper Pods gracefully instead of
  waiting for forced termination after their work finishes.
- Clear the legacy rolling-update settings when upgrading StatefulSets to explicit
  activation, for both generated manifests and direct Helm upgrades.

### Changed

- Reduce reconciliation overhead for large YAML inventories while retaining strict
  input validation and the existing provider fingerprint format.
- Release packages, chart and native images pass one artifact-based promotion gate.
  Existing conflicting versioned artifacts cannot be silently overwritten.
  Rehearse image, multi-architecture index and chart publication in a disposable
  registry; recognize missing GHCR versions and verify image identity with both
  classic and containerd Docker stores.
- Check bounded load before expensive release checks, reuse candidate builds and
  run independent external groups concurrently without duplicating CLI contracts.
  Disposable container cleanup removes anonymous test volumes and preserves
  borrowed candidate images.
- Raise the required line coverage to 95%, with a project target above 97% and additional
  tests for interrupted recovery, process cleanup, identity and platform boundaries.
- SQLite cluster bootstrap now requires an initialized provider PVC. Missing input
  does not become an empty policy. See the recovery runbook for migration actions.

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
