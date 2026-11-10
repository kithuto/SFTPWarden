# Named Keys

## Public-key validation

SFTPWarden checks the SSH wire structure and type of RSA, Ed25519, NIST P-256,
P-384 and P-521 ECDSA keys, including the supported OpenSSH security-key formats.
It rejects truncated or extra fields, type/blob mismatches, malformed lengths and
multiple key lines. Optional comments and a trailing file newline are accepted.
ECDSA points must use the uncompressed encoding accepted by OpenSSH.

These checks validate the encoding; they do not establish possession of the
private key or guarantee acceptance by a server's algorithm policy. Verify access
with a real connection before removing an existing working credential. The
`AAAA...` values below are placeholders; replace them with actual public keys.
The YAML/CSV examples in the repository contain public-only sample keys whose
private keys were discarded. Replace those sample keys before using the examples.

## User schemas

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

In schema v2, an explicit `keys` list takes precedence over legacy `public_keys`.
An empty list means no key authorization. Omitting `keys` still converts legacy
`public_keys` for compatibility; `keys: null` and other non-list values are invalid.

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

## Revocation and expiry

Removing or disabling the last key is valid. A user with no usable credentials
cannot establish a new session; its declared `disabled` flag is not changed
automatically. Creating a new user through the CLI still requires a credential.
Disabling a user removes its key authorization as well as password access.

Expiry is evaluated in UTC, including when provider content has not changed.
Installed expiring keys also carry an OpenSSH `expiry-time` restriction, so the
deadline does not depend on a later refresh. OpenSSH's option identifies the last
valid second; SFTPWarden renders the preceding second so access is denied at the
declared deadline. Subsecond deadlines are rounded down. See the
[OpenSSH authorized-key options](https://man.openbsd.org/sshd.8#expiry-time).

These changes govern **new authentications**. Existing sessions and transfers
are not terminated by replacing `authorized_keys`. For immediate termination,
first apply the revocation successfully, then have the runtime administrator
terminate that user's processes inside the selected Linux container (for
example, `pkill -TERM -u alice` after checking the username/UID). This interrupts
that user's transfers. Do not execute runtime account/process commands on the
operator's host.

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
