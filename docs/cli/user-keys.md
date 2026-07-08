# User Key Commands

`sftpwarden user key` manages SSH keys for one provider user.

Schema v1 providers store anonymous `public_keys`. In v1, `list`, `show`, `add`,
and `remove` work with deterministic key names and fingerprints. Operations that
need persisted key metadata - `disable`, `enable`, `rename`, `rotate`, `expire`,
and `import` - migrate the provider forward to schema v2 after confirmation.
Use `--yes` for non-interactive migration approval, or `--dry-run` to preview the
migration and key operation without writing.

Schema v2 stores named keys with metadata, lifecycle fields, disabled state, and
expiry. Key names must match `^[a-z][a-z0-9._-]{0,63}$`.

## `sftpwarden user key`

Command group for managing a user's SSH keys.

```bash
sftpwarden user key --help
```

Subcommands:

- `list`
- `show`
- `add`
- `remove`
- `disable`
- `enable`
- `rename`
- `rotate`
- `expire`
- `import`

## `sftpwarden user key list`

Lists SSH keys for one user.

```bash
sftpwarden user key list alice
sftpwarden user key list alice --context prod
sftpwarden user key list alice --config ./sftpwarden.yaml
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | User whose keys should be listed. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Reads through a project config directly. |

## `sftpwarden user key show`

Shows one user key as JSON.

```bash
sftpwarden user key show alice prod-ci
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | User that owns the key. |
| `key_name` | Yes | key name or deterministic v1 name | Key to show. In schema v1, use the name displayed by `key list`. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Reads through a project config directly. |

## `sftpwarden user key add`

Adds one SSH public key to a user.

```bash
sftpwarden user key add alice prod-ci --public-key ./prod-ci.pub
sftpwarden user key add alice laptop --public-key "ssh-ed25519 AAAA..."
sftpwarden user key add alice prod-ci --public-key ./prod-ci.pub --comment "CI deployment"
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | User that will receive the key. |
| `key_name` | Yes | key name | Operator-facing key name. Must match `^[a-z][a-z0-9._-]{0,63}$`. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--public-key` | `TEXT` | required | Public key text or path to a `.pub` file. |
| `--comment` | `TEXT` | none | Key-level operator note. Persisted in schema v2. |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Uses a project config directly. |
| `--dry-run` | flag | false | Shows the change without writing provider data. |
| `--no-refresh` | flag | false | Saves provider data but skips automatic runtime refresh. |

### Duplicate Rules

Schema v2 rejects duplicate key names and duplicate key fingerprints. Fingerprints
are OpenSSH-compatible SHA256 fingerprints derived from the key blob. Key
algorithm support follows valid OpenSSH public key input, including modern keys
such as Ed25519.

## `sftpwarden user key remove`

Removes one key from a user.

```bash
sftpwarden user key remove alice prod-ci
sftpwarden user key remove alice prod-ci --yes
sftpwarden user key remove alice prod-ci --dry-run
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | User that owns the key. |
| `key_name` | Yes | key name or deterministic v1 name | Key to remove. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Uses a project config directly. |
| `--yes`, `-y` | flag | false | Accepts the removal confirmation prompt. |
| `--dry-run` | flag | false | Shows the removal without writing provider data. |
| `--no-refresh` | flag | false | Saves provider data but skips automatic runtime refresh. |

## `sftpwarden user key disable`

Disables one named key without removing it. Disabled keys are not written to
`authorized_keys`.

```bash
sftpwarden user key disable alice prod-ci
sftpwarden user key disable alice prod-ci --yes
sftpwarden user key disable alice prod-ci --dry-run
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | User that owns the key. |
| `key_name` | Yes | key name | Key to disable. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Uses a project config directly. |
| `--yes`, `-y` | flag | false | Accepts schema migration confirmation when the provider is v1. |
| `--dry-run` | flag | false | Shows migration and disable plan without writing. |
| `--no-refresh` | flag | false | Saves provider data but skips automatic runtime refresh. |

## `sftpwarden user key enable`

Enables one disabled named key.

```bash
sftpwarden user key enable alice prod-ci
sftpwarden user key enable alice prod-ci --yes
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | User that owns the key. |
| `key_name` | Yes | key name | Key to enable. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Uses a project config directly. |
| `--yes`, `-y` | flag | false | Accepts schema migration confirmation when the provider is v1. |
| `--dry-run` | flag | false | Shows migration and enable plan without writing. |
| `--no-refresh` | flag | false | Saves provider data but skips automatic runtime refresh. |

## `sftpwarden user key rename`

Renames one key while keeping its public key and metadata.

```bash
sftpwarden user key rename alice old-name new-name
sftpwarden user key rename alice old-name new-name --yes
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | User that owns the key. |
| `old_name` | Yes | key name | Current key name. |
| `new_name` | Yes | key name | Replacement key name. Must match `^[a-z][a-z0-9._-]{0,63}$`. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Uses a project config directly. |
| `--yes`, `-y` | flag | false | Accepts schema migration confirmation when the provider is v1. |
| `--dry-run` | flag | false | Shows migration and rename plan without writing. |
| `--no-refresh` | flag | false | Saves provider data but skips automatic runtime refresh. |

## `sftpwarden user key rotate`

Replaces one key's public key while preserving its name and lifecycle metadata.

```bash
sftpwarden user key rotate alice prod-ci --public-key ./prod-ci-new.pub
sftpwarden user key rotate alice prod-ci --public-key "ssh-ed25519 AAAA..." --yes
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | User that owns the key. |
| `key_name` | Yes | key name | Key to rotate. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--public-key` | `TEXT` | required | Replacement public key text or path to a `.pub` file. |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Uses a project config directly. |
| `--yes`, `-y` | flag | false | Accepts schema migration confirmation when the provider is v1. |
| `--dry-run` | flag | false | Shows migration and rotation plan without writing. |
| `--no-refresh` | flag | false | Saves provider data but skips automatic runtime refresh. |

## `sftpwarden user key expire`

Sets an expiry timestamp for one key. Expired keys are treated as inactive and
are not written to `authorized_keys`.

```bash
sftpwarden user key expire alice prod-ci --at 2027-01-01
sftpwarden user key expire alice prod-ci --at 2027-01-01T12:00:00Z
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | User that owns the key. |
| `key_name` | Yes | key name | Key to expire. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--at` | ISO date or datetime | required | Expiry date/time. Accepts values such as `2027-01-01`, `2027-01-01T12:00:00+00:00`, or `2027-01-01T12:00:00Z`. Date-only values mean midnight UTC. |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Uses a project config directly. |
| `--yes`, `-y` | flag | false | Accepts schema migration confirmation when the provider is v1. |
| `--dry-run` | flag | false | Shows migration and expiry plan without writing. |
| `--no-refresh` | flag | false | Saves provider data but skips automatic runtime refresh. |

## `sftpwarden user key import`

Imports all `.pub` files from a directory as user keys.

```bash
sftpwarden user key import alice --from-dir ./keys
sftpwarden user key import alice --from-dir ./single-key --name prod-ci
sftpwarden user key import alice --from-dir ./keys --yes --dry-run
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | User that will receive the imported keys. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--from-dir` | directory path | required | Directory containing `.pub` files. Only regular files ending in `.pub` are imported. |
| `--name` | key name | file stem | Overrides the generated key name, but only when the directory contains exactly one `.pub` file. Without `--name`, each key name is the public key file name without `.pub`. |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Uses a project config directly. |
| `--yes`, `-y` | flag | false | Accepts schema migration confirmation when the provider is v1. |
| `--dry-run` | flag | false | Shows migration and import plan without writing. |
| `--no-refresh` | flag | false | Saves provider data but skips automatic runtime refresh. |
