# Providers

Providers store SFTP users outside the runtime image. The runtime reads the
configured provider during refresh/sync and writes only active users and active
keys into OpenSSH state.

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
