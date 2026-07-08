# Named Keys

SFTPWarden v1.3 supports two user schemas.

Schema v1 is the simple format:

```yaml
users:
  - username: alice
    public_keys:
      - ssh-ed25519 AAAA...
```

Schema v2 is the named-key format:

```yaml
schema_version: 2
users:
  - username: alice
    keys:
      - name: prod-ci
        public_key: ssh-ed25519 AAAA...
        fingerprint: SHA256:...
        comment: CI deploy key
        disabled: false
        created_at: 2026-07-07T10:00:00Z
        updated_at: 2026-07-07T10:00:00Z
        expires_at: 2027-01-01
        source: user.key.add
        metadata:
          owner: platform
```

Schema v1 is supported for simplicity. Schema v2 is recommended when operators
need per-key control, rotation, expiry, disable/enable, import, or metadata.
Key names must match `^[a-z][a-z0-9._-]{0,63}$`. Fingerprints are derived from
the public key when omitted and are validated when present.

## Commands

```bash
sftpwarden user key list alice
sftpwarden user key show alice prod-ci
sftpwarden user key add alice prod-ci --public-key ./prod-ci.pub
sftpwarden user key rotate alice prod-ci --public-key ./prod-ci-new.pub
sftpwarden user key expire alice prod-ci --at 2027-01-01
sftpwarden user key disable alice prod-ci
sftpwarden user key enable alice prod-ci
sftpwarden user key rename alice prod-ci ci-prod
sftpwarden user key remove alice ci-prod --yes
sftpwarden user key import alice --from-dir ./keys
```

`key import --from-dir` uses each `.pub` file name as the key name. When the
directory contains one key, `--name <key_name>` can override that file name.

## Migration

Inspect the provider schema:

```bash
sftpwarden provider schema show
```

Preview and apply migration:

```bash
sftpwarden provider keys migrate --dry-run
sftpwarden provider schema migrate --to 2 --dry-run
sftpwarden provider schema migrate --to 2 --backup --yes
```

Advanced key operations on a schema v1 provider ask before migrating to schema
v2. In non-interactive automation, pass `--yes`. In `--dry-run`, SFTPWarden
prints the planned migration and key change without writing data.
