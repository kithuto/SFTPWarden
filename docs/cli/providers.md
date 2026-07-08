# Provider Commands

Provider commands move users between providers, inspect provider schema state,
and run explicit schema migrations. They work with local or remote local-sync
contexts that have a local project config. Remote-only contexts do not expose
local provider transfer operations.

## `sftpwarden provider`

Command group for provider transfer and schema operations.

```bash
sftpwarden provider --help
```

Subcommands:

- `export`
- `import`
- `copy`
- `schema show`
- `schema migrate`
- `keys migrate`

## `sftpwarden provider export`

Exports users from a provider to YAML, JSON, or CSV.

```bash
sftpwarden provider export --format json
sftpwarden provider export --output users.yaml
sftpwarden provider export --context prod --format csv --output users.csv
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Source context. |
| `--config` | `PATH` | none | Source project config. |
| `--output`, `-o` | `PATH` | stdout | File to write. If omitted, raw transfer data is written to stdout without Rich decoration. |
| `--format` | `yaml`, `csv`, `json` | inferred from output suffix, otherwise `yaml` | Output transfer format. |

### Effects

Read-only unless `--output` is supplied. With `--output`, SFTPWarden writes the
export file with private file permissions where supported.

## `sftpwarden provider import`

Imports users from a YAML, JSON, or CSV transfer file into the selected provider.
You must choose exactly one write mode: `--merge` or `--replace`.

```bash
sftpwarden provider import --input users.yaml --merge
sftpwarden provider import --input users.json --replace --dry-run
sftpwarden provider import --input users.csv --merge --json
sftpwarden provider import --input users.json --merge --no-refresh
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--input`, `-i` | `PATH` | required | Transfer file to read. |
| `--context`, `-c` | `TEXT` | active context | Destination context. |
| `--format` | `yaml`, `csv`, `json` | inferred from input suffix | Input transfer format. |
| `--merge` | flag | false | Upserts imported users and keeps destination-only users. Mutually exclusive with `--replace`. |
| `--replace` | flag | false | Makes destination users exactly match the input file. Mutually exclusive with `--merge`. |
| `--dry-run` | flag | false | Parses the input and reports changes without writing provider data. |
| `--json` | flag | false | Prints mutation result as JSON. |
| `--no-refresh` | flag | false | Skips automatic runtime refresh after a runtime-affecting provider write. |

### Effects

May write provider data. For remote local-sync contexts, file providers are
synced to the remote host after the write. For Kubernetes YAML/CSV providers,
the local provider file is updated and deploy/apply/upgrade is required to copy
it into the provider PVC.

## `sftpwarden provider copy`

Copies users from one registered context to another. You must choose exactly one
write mode: `--merge` or `--replace`.

```bash
sftpwarden provider copy --from-context dev --to-context prod --merge --dry-run
sftpwarden provider copy --from-context dev --to-context prod --replace --no-refresh
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--from-context` | `TEXT` | required | Source registered context. |
| `--to-context` | `TEXT` | required | Destination registered context. |
| `--merge` | flag | false | Upserts source users and keeps destination-only users. Mutually exclusive with `--replace`. |
| `--replace` | flag | false | Makes destination users exactly match source users. Mutually exclusive with `--merge`. |
| `--dry-run` | flag | false | Reports changes without writing destination provider data. |
| `--json` | flag | false | Prints mutation result as JSON. |
| `--no-refresh` | flag | false | Skips automatic runtime refresh after a runtime-affecting provider write. |

### Effects

May write the destination provider. Comment-only changes do not refresh the
runtime because comments are metadata.

## `sftpwarden provider schema`

Command group for provider schema inspection and migration.

```bash
sftpwarden provider schema --help
```

Subcommands:

- `show`
- `migrate`

## `sftpwarden provider schema show`

Shows the configured provider schema and the schema currently stored in provider
data.

```bash
sftpwarden provider schema show
sftpwarden provider schema show --context prod
sftpwarden provider schema show --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Context to inspect. |
| `--config` | `PATH` | none | Project config to inspect directly. |
| `--json` | flag | false | Prints configured schema, provider schema, user count, provider type, and migration status as JSON. |

### Effects

Read-only. If `provider.user_schema` in `sftpwarden.yaml` is newer than the
provider data, this command reports that a migration is required and that the
next deploy/apply/upgrade will run it.

## `sftpwarden provider schema migrate`

Explicitly migrates provider users forward to a target user schema.

```bash
sftpwarden provider schema migrate --to 2 --dry-run
sftpwarden provider schema migrate --to 2 --backup --yes
sftpwarden provider schema migrate --to 2 --no-backup --yes
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--to` | supported schema version | `2` | Target provider user schema. Migrations are forward-only. |
| `--context`, `-c` | `TEXT` | active context | Context whose provider should be migrated. |
| `--config` | `PATH` | none | Project config to migrate directly. |
| `--backup` / `--no-backup` | flag pair | `--backup` | Controls whether a logical provider backup is written before a real migration. |
| `--yes`, `-y` | flag | false | Accepts the migration confirmation prompt. |
| `--dry-run` | flag | false | Shows planned migration without writing provider data or config. |
| `--json` | flag | false | Prints migration result as JSON. |

### Effects

A real migration may write provider data and, when needed, update
`provider.user_schema` in `sftpwarden.yaml`. Downgrades are rejected. Unknown
future schemas are rejected with an upgrade-oriented error.

## `sftpwarden provider keys`

Command group for key-storage migrations.

```bash
sftpwarden provider keys --help
```

Subcommands:

- `migrate`

## `sftpwarden provider keys migrate`

Shortcut for migrating schema v1 anonymous `public_keys` to schema v2 named
keys.

```bash
sftpwarden provider keys migrate --dry-run
sftpwarden provider keys migrate --backup --yes
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Context whose provider should be migrated. |
| `--config` | `PATH` | none | Project config to migrate directly. |
| `--backup` / `--no-backup` | flag pair | `--backup` | Controls whether a logical provider backup is written before a real migration. |
| `--yes`, `-y` | flag | false | Accepts the migration confirmation prompt. |
| `--dry-run` | flag | false | Shows planned migration without writing provider data or config. |
| `--json` | flag | false | Prints migration result as JSON. |

### Effects

Equivalent to `sftpwarden provider schema migrate --to 2`. Migrated keys receive
deterministic names from the schema migration.
