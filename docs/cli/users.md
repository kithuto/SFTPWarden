# User Commands

Use these commands to list accounts, create access or revoke it. Read commands
show provider data; they do not prove that a client can authenticate. For a full
walkthrough, see [Operations](../operations.md#local-runtime).

Commands below show alternative invocations. Replace `prod` with your context
where needed. User JSON can contain password hashes and other credential
material; keep it out of public logs and issues.

User changes mutate provider data. After a runtime-affecting user change,
SFTPWarden refreshes the runtime automatically unless `--no-refresh` is passed.
For Kubernetes YAML/CSV providers, SFTPWarden saves the local provider file and
prints the deploy/apply/upgrade command to run because the file is copied into
the provider PVC during rollout.

## `sftpwarden users`

Lists users from the selected provider.

```bash
sftpwarden users
sftpwarden users --context prod
sftpwarden users --config ./sftpwarden.yaml
sftpwarden users --json
```

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Reads users through a project config directly. |
| `--json` | flag | false | Prints the complete provider user model as JSON. |

## `sftpwarden user`

Command group for managing users in mutable providers.

```bash
sftpwarden user --help
```

Subcommands:

- `show`
- `create`
- `update`
- `disable`
- `enable`
- `remove`
- `key`

## `sftpwarden user show`

Shows one user as JSON.

```bash
sftpwarden user show alice
sftpwarden user show alice --context prod
sftpwarden user show alice --config ./sftpwarden.yaml
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | Provider username to read. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--config` | `PATH` | none | Reads through a project config directly. |

## `sftpwarden user create`

Create Alice with an interactively prompted password:

```bash
sftpwarden user create alice
```

Before the first deployment, add `--no-refresh` because no runtime exists yet.
As an alternative for a new account, use its existing public key:

```bash
sftpwarden user create bob --public-key ./bob-key.pub
```

To add a key to Alice after creating her account, use
`sftpwarden user key add alice laptop --public-key ./alice-key.pub`.
See [Named Keys](../named-keys.md) for key generation and verification.

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | New provider username. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--public-key` | `TEXT` | none | Adds an SSH public key. The value can be literal OpenSSH public key text or a path to a public key file. May be passed multiple times. |
| `--password` | `TEXT` | none | Plaintext password to hash before saving. Avoid shell history exposure in production. |
| `--password-hash` | `TEXT` | none | Precomputed `sha512crypt` hash to store. Treat it as sensitive; command arguments can be visible to other processes. |
| `--upload-dir` | relative path | `upload` | Upload directory inside the user's chroot. Must be a safe relative path. |
| `--comment` | `TEXT` | none | Operator note stored as metadata. It does not affect runtime access by itself. |
| `--uid` | `INTEGER` | auto-assigned | Explicit Linux UID for the runtime user. |
| `--gid` | `INTEGER` | auto-assigned | Explicit Linux GID for the runtime user. |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--no-refresh` | flag | false | Saves provider data but does not refresh the runtime. |

### Notes

If password authentication is allowed and no key is provided, interactive mode
can prompt for a password. Public key values are normalized and validated before
storage.

## `sftpwarden user update`

Updates an existing provider user. The examples below are independent changes.
`--public-key` replaces the user's key list; use the named-key commands to change
one key without replacing the others. UID/GID changes to retained identities
require a maintenance and ownership procedure; refresh refuses silent renumbering.

```bash
sftpwarden user update alice --comment "Finance inbox"
sftpwarden user update alice --upload-dir inbound
sftpwarden user update alice --public-key ./alice-new.pub
sftpwarden user update alice --uid 12001 --gid 12001
sftpwarden user update alice --disabled
sftpwarden user update alice --enabled
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | Existing provider username. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--public-key` | `TEXT` | unchanged | Replaces the user's public keys. May be passed multiple times. |
| `--password` | `TEXT` | unchanged | Plaintext password to hash before saving. |
| `--password-hash` | `TEXT` | unchanged | Replacement precomputed password hash. |
| `--upload-dir` | relative path | unchanged | Replacement upload directory. |
| `--comment` | `TEXT` | unchanged | Replacement operator note. Comment-only changes do not refresh runtime. |
| `--uid` | `INTEGER` | unchanged | Replacement explicit UID. |
| `--gid` | `INTEGER` | unchanged | Replacement explicit GID. |
| `--disabled` / `--enabled` | flag pair | unchanged | Disables or enables the whole user. |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--no-refresh` | flag | false | Saves provider data but skips automatic runtime refresh. |

## `sftpwarden user disable`

Disables an entire user. After successful refresh, new password and key logins
are denied. Existing sessions continue; see [revocation](../named-keys.md#revocation-and-expiry).

```bash
sftpwarden user disable alice
sftpwarden user disable alice --context prod
sftpwarden user disable alice --no-refresh
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | Existing provider username. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--no-refresh` | flag | false | Saves provider data but skips automatic runtime refresh. |

## `sftpwarden user enable`

Enables a disabled user.

```bash
sftpwarden user enable alice
sftpwarden user enable alice --context prod
sftpwarden user enable alice --no-refresh
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | Existing provider username. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--no-refresh` | flag | false | Saves provider data but skips automatic runtime refresh. |

## `sftpwarden user remove`

Removes a user from the provider. User files are kept unless `--delete-files` is
explicitly passed.

```bash
sftpwarden user remove alice
sftpwarden user remove alice --yes
sftpwarden user remove alice --delete-files --yes
```

### Arguments

| Argument | Required | Value | What it means |
| --- | --- | --- | --- |
| `username` | Yes | `TEXT` | Existing provider username to remove. |

### Options

| Flag | Value | Default | What it does |
| --- | --- | --- | --- |
| `--context`, `-c` | `TEXT` | active context | Selects a registered context. |
| `--yes`, `-y` | flag | false | Accepts the removal confirmation prompt. |
| `--no-refresh` | flag | false | Removes the provider user but skips automatic runtime refresh. |
| `--delete-files`, `--force-delete-files` | flag | false | Permanently deletes the user's runtime data directory after removing the provider user. |

### Safety

`--delete-files` is irreversible. For remote contexts, it deletes the matching
remote data directory through SSH. Use `--force-delete-files` only when the
destructive intent should be obvious in scripts.
