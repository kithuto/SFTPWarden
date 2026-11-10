# Providers

SQLite runtime reads use a read-only connection and a consistent transaction for
users and keys. Reading, health checks and runtime planning do not initialize or
migrate tables. Configure `provider.user_schema` to match existing storage and
perform initialization or migration explicitly before deployment. A single-user
update replaces only that user's key rows in the same transaction; full provider
replacement still removes users and keys absent from the supplied snapshot.

Providers store SFTP users outside the runtime image. The runtime reads the
configured provider during refresh/sync and writes only active users and active
keys into OpenSSH state.

Missing, unreadable, malformed or truncated provider files fail refresh and leave
the last applied authorization in place. Empty YAML files, `users: null` and CSV
files without a valid header are errors. To intentionally remove all users, use
`users: []` in YAML or a CSV containing only its valid header. CSV rows must have
the same number of cells as the header; incomplete rows are rejected.
Import uses the same validation for YAML, CSV and JSON. JSON requires an explicit
`users` list, and duplicate explicit keys in YAML/JSON are rejected. YAML anchors
and merge defaults remain supported. A truncated import fails before changing
the destination provider.

## Choosing a Provider

| Provider | Runtime reads | CLI mutations | Good fit |
| --- | ---: | ---: | --- |
| YAML | Yes | Yes | Quick starts, small GitOps-style deployments |
| CSV | Yes | Yes | Spreadsheet-friendly user handoff |
| SQLite | Yes | Yes | Single-host deployments without an external database |
| MySQL | Yes | Yes | Shared production database |
| MariaDB | Yes | Yes | MySQL-compatible production database |
| PostgreSQL | Yes | Yes | Production database and Kubernetes deployments |
| MongoDB | Yes | Yes | Document-oriented infrastructure |

For production Kubernetes, prefer PostgreSQL, MariaDB/MySQL, or MongoDB when user
state must change outside deployment cycles. YAML and CSV remain useful for
reviewed declarative workflows, but their rendered contents are copied into the
provider PVC during `deploy`, `kube apply`, or `helm upgrade --install`.

## Provider Configuration

### Concurrent edits

User and named-key commands compare the selected user with a fresh copy inside
the provider's lock or transaction. Edits to different users preserve both
changes; a stale edit to the same user returns a conflict, including when only
its comment changed. Reload the provider before retrying a conflicting edit.
Import, copy and schema migration compare the complete source snapshot before
replacement, so a concurrent edit prevents an outdated bulk write.

YAML and CSV use the stable file lock described below. SQLite holds a write
transaction across comparison and publication. SQL databases use transactional
row locks and serializable mutations; users and key rows are read together in a
read-only, repeatable-read snapshot. Updating one SQL user never replaces another
user's keys. Transaction conflicts and lock timeouts are errors, not successful
partial edits.

These guarantees cover participating application writers. Arbitrary editors and
direct database writes do not acquire SFTPWarden's application locks. Keep an
exclusive maintenance window for external bulk rewrites and schema changes.

### Compose file-provider migration

For local Compose and remote `local-sync` deployments, `sftpwarden deploy` moves
the active YAML, CSV or SQLite provider to `provider/<filename>` and updates
`provider.path` to `/etc/sftpwarden/provider-data/<filename>`. The runtime mounts
only that dedicated directory read-only. A later atomic save replaces a file
inside the mounted directory and becomes visible to normal refresh/sync without
another deploy. `compose --write` alone does not perform this migration.

Run `deploy --dry-run` to review the transition, then perform the deploy before
using the upgraded client to edit a running legacy installation. Stop external
editors and database writers during this one-time transition. The migration
retains the original provider and private recovery material in
`.sftpwarden/provider-migration/` (`config.yaml`, `provider`, `journal.json`).
SQLite recovery snapshots include committed WAL transactions. An occupied target,
unrelated files in the dedicated directory or conflicting recovery material stops
the migration without overwriting that material.

After migration, the path in the updated config is authoritative. Update external
editors and scripts to use `provider/<filename>`; the original file is a recovery
copy and is no longer synchronized. Application writers using the old local path
receive an error. If deployment fails after the copy, user edits to the migrated
file remain blocked until a retry successfully recreates the runtime and waits
for readiness. Do not delete the pending-deploy marker to bypass that check.

For an interrupted config update, keep the source, target and recovery directory
unchanged and retry `deploy`. If a conflict is reported, preserve both copies and
reconcile them before retrying. To roll back a completed transition, stop writers
and the runtime, preserve any newer provider edits, restore the recovery config
and the matching provider, remove the old-path migration notice only after that
reconciliation, and recreate the legacy mount using the matching CLI version.
Never assume the original provider includes edits made after migration.

The client uses POSIX advisory locks or Windows byte-range locks in a stable
separate file, plus unique temporary files and atomic replacement. These locks
coordinate SFTPWarden writers; external editors and direct database clients do not
participate. Use a local filesystem with reliable locking and rename semantics;
network mounts and cloud synchronization are not distributed writer coordination.
Windows file privacy also depends on the containing directory's ACL. Directory
fsync is used where supported; Windows does not offer that operation through this
implementation. Remote-only migration remains an open stabilization requirement.

YAML:

```yaml
provider:
  type: yaml
  path: /etc/sftpwarden/users.yaml
  user_schema: 2
```

CSV:

```yaml
provider:
  type: csv
  path: /etc/sftpwarden/users.csv
  user_schema: 2
```

SQLite:

```yaml
provider:
  type: sqlite
  path: /etc/sftpwarden/users.sqlite
  user_schema: 2
```

MySQL, MariaDB, and PostgreSQL:

```yaml
provider:
  type: postgresql
  dsn: "${SFTPWARDEN_POSTGRES_DSN}"
  table: sftp_users
  user_schema: 2
```

MongoDB:

```yaml
provider:
  type: mongodb
  dsn: "${SFTPWARDEN_MONGODB_DSN}"
  collection: sftp_users
  user_schema: 2
```

Use environment variables for real DSNs so secrets are not committed in
`sftpwarden.yaml`.

Provider commands resolve `${NAME}` from their process environment. A whole DSN
written as `$NAME` or `%NAME%` is also supported on Linux, macOS, and Windows.
Expansion happens once: dollar signs inside the resulting credential are not
expanded again. Bare dollar signs within a literal URL are preserved. Missing
or empty referenced variables fail before a connection is opened.

Generated Compose files pass only the variables referenced by `provider.dsn`.
They contain required-variable expressions, not resolved credentials. Docker
Compose resolves these on the machine where it runs, using its environment and
project `.env` rules. Host-side provider commands use the process environment;
they do not load a project's `.env` file. Local shell variables are not implicitly
forwarded to a remote Docker Compose process.

### Database transport options

MySQL and MariaDB accept these DSN query parameters:

| Option | Behavior |
| --- | --- |
| `charset` | Driver connection character set. |
| `connect_timeout` | Connection timeout, 1–30 whole seconds; default 5. |
| `read_timeout`, `write_timeout` | Socket operation timeouts, 1–30 whole seconds; default 20. |
| `ssl_ca` | CA certificate file available on the machine executing the provider operation. |
| `ssl_cert`, `ssl_key` | Optional client certificate and private key files; a key requires a certificate. |
| `ssl_verify_cert`, `ssl_verify_identity` | Request verified TLS; disabling either is rejected. |
| `ssl_disabled` | `false` requires verified TLS; `true` explicitly disables TLS and cannot be combined with TLS options. |

TLS options always enable certificate-chain and hostname verification. An
untrusted certificate, a hostname mismatch, or a server without requested TLS
must fail the connection. Unknown, duplicate, empty, and malformed options are
errors. File paths refer to the executing host or container; custom CA/client
files must be provisioned there explicitly.

MongoDB connections default to `connectTimeoutMS=5000`,
`serverSelectionTimeoutMS=5000`, `socketTimeoutMS=20000`, and `timeoutMS=25000`.
The URI can specify a smaller positive whole-millisecond value for each option;
zero, larger values, and duplicate timeout options are rejected. Every provider
operation closes its client, including when reading or validation fails.

PostgreSQL keeps psycopg/libpq DSN and TLS semantics, including `sslmode=verify-full`
and `sslrootcert`. `connect_timeout` defaults to five seconds and accepts whole
seconds from 1 to 30 (including `PGCONNECT_TIMEOUT`). Libpq applies connection
timeouts per host in a multi-host DSN. Subsequent socket waits have a 20-second
deadline; timed-out sessions are closed. Provision CA/client files on every host
or container that executes a provider operation. The runtime's attempt deadline
also bounds reconciliation as a whole.

Socket timeouts bound individual driver operations. They do not establish a
single deadline for an entire multi-step CLI workflow. The supervised runtime
also applies its separate refresh-attempt deadline.

## User Schemas

Schema v1 and schema v2 are both supported formats:

- `user_schema: 1` stores simple anonymous public keys in `public_keys`.
- `user_schema: 2` stores named keys with fingerprints, comments, disabled
  state, timestamps, expiry, source, and metadata.

New `sftpwarden init` projects default to schema v2. Use `--user-schema 1` when
you intentionally want the simpler v1 format:

```bash
sftpwarden init dev --user-schema 1 --yes
sftpwarden init prod --user-schema 2 --yes
```

Existing configs that omit `provider.user_schema` continue to behave as schema
v1 until explicitly migrated. Changing `provider.user_schema` in
`sftpwarden.yaml` records the desired schema; it does not rewrite provider data
at config-edit time. Forward migrations run during `sftpwarden deploy`,
`sftpwarden kube apply`, or `sftpwarden helm upgrade`, with confirmation unless
`--yes` is used. Manual YAML edits are handled the same way as changes made with
`sftpwarden config`.

## File Providers

CSV `disabled` values are checked explicitly, ignoring surrounding whitespace
and case. The boolean text forms listed for SQL below are accepted; an empty or
omitted CSV value retains the default `false`. Other values fail validation.
Schema-v2 CSV key JSON rejects duplicate fields and non-finite numbers, including
inside metadata. Failed serialization leaves an existing provider file intact.

In schema v2, an explicit `keys` list is authoritative. `keys: []` revokes key
access even if a legacy `public_keys` copy remains in the document. If `keys` is
omitted, legacy `public_keys` are still converted to named keys. A null, boolean,
number, string or mapping in place of a key list is invalid; it is not interpreted
as an empty policy. Schema v1 continues to use `public_keys`.

YAML schema v1:

```yaml
users:
  - username: alice
    public_keys:
      - ssh-ed25519 AAAA...
```

YAML schema v2:

```yaml
schema_version: 2
users:
  - username: alice
    keys:
      - name: prod-ci
        public_key: ssh-ed25519 AAAA...
        comment: CI deploy key
        disabled: false
        expires_at: 2027-01-01
```

CSV schema v1 uses a `public_keys` column. CSV schema v2 uses a `keys` JSON
column, which is supported but less comfortable for nested key metadata.

## SQL Providers

SQL input is validated as a complete user/key snapshot. Invalid boolean values,
incomplete key rows, fractional UID/GID values and malformed metadata fail the
read instead of becoming enabled credentials, missing keys or empty metadata.
Metadata must be a JSON object with unique field names; SQL `NULL` means no
metadata. Correct the source data and retry refresh. Failed validation preserves
the runtime's last applied authorization and state.

Boolean columns accept driver booleans, integer `0`/`1`, single-bit byte values,
and case-insensitive `true`/`false`, `t`/`f`, `yes`/`no`, `y`/`n`, `on`/`off` or
`1`/`0` text. An explicitly null or unrecognized boolean is invalid. Custom user
queries may select a subset of users; all fetched key rows must still be valid.

Schema v1 uses the configured users table, usually `sftp_users`:

```text
username, public_keys, password_hash, uid, gid, upload_dir, comment, disabled
```

Schema v2 keeps the users table and adds `sftp_user_keys`:

```text
username, name, public_key, fingerprint, comment, disabled, created_at,
updated_at, expires_at, source, metadata
```

During `init`, SFTPWarden checks whether required SQL storage exists. If it is
missing, interactive init asks whether to create it. Non-interactive automation
can use `--create-table` or `--no-create-table`.

```bash
sftpwarden init prod \
  --provider postgresql \
  --dsn 'postgresql://sftpwarden:change-me@db.example.com:5432/sftpwarden' \
  --create-table
```

With `user_schema: 2`, table creation includes the key table as well as the users
table.

## MongoDB

Schema v1 stores users with `public_keys`. Schema v2 embeds `keys` in each user
document and writes schema metadata on documents created by SFTPWarden. During
`init`, SFTPWarden checks the configured collection and username index, and can
create them when requested.

Reads require a stable collection UUID before and after the snapshot. Missing,
recreated or inaccessible collections report failure and preserve applied access;
they are never interpreted as an empty user set. Provider credentials therefore
need collection metadata access as well as document read access.

Ordinary user and key edits use atomic document comparison on standalone servers
and replica sets. Bulk replacement requires a replica set or sharded cluster and
runs in one transaction. On standalone MongoDB, import/copy (including merge),
full schema migration and provider restore are rejected before the first write.
Configure a replica set to use those operations; individual user CRUD remains
available. A rejected bulk operation leaves the stored users unchanged.

## Transfer and Migration

Move users between providers:

```bash
sftpwarden provider export --format json > users.json
sftpwarden provider import --input users.json --merge
sftpwarden provider copy --from-context dev --to-context prod --merge
```

Inspect or migrate schema:

```bash
sftpwarden provider schema show
sftpwarden provider keys migrate --dry-run
sftpwarden provider schema migrate --to 2 --backup --yes
```

`provider schema show` compares the configured schema with the provider data
that is actually stored and reports whether migration is pending. Explicit
migration commands are useful when you want to migrate immediately. Deploy
commands perform the same forward reconciliation before applying runtime or
cluster changes when `sftpwarden.yaml` requests a newer schema.

Migrations are forward-only and never run during ordinary reads. Backward schema
changes are rejected with a clear error instead of rewriting provider data.
Advanced named-key operations on schema v1 data can ask to migrate to schema v2;
in non-interactive workflows, pass `--yes` when that migration is intended.
