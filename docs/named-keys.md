# Named Keys

A named key lets you manage one device or integration without replacing all of
a user's SSH access. Alice might have a `laptop` key and an `automation` key.
You can revoke one while leaving the other available.

New projects use schema v2, which supports named keys. If your provider uses
schema v1, read [Migration](#migration) before changing it.

## Commands

### Add a key to Alice

This continues [Getting Started](getting-started.md#use-an-ssh-key-recommended).
Use the existing Alice account and select its context first:

```bash
sftpwarden context use demo
sftpwarden user key list alice
```

If you have not created her key yet, create it on the client computer. Choose a
passphrase and keep the private `alice-key` file there:

```bash
ssh-keygen -t ed25519 -f ./alice-key -C sftpwarden-demo-alice
sftpwarden user key add alice laptop --public-key ./alice-key.pub
```

Do not overwrite an existing key. Only the `.pub` file goes into SFTPWarden.
The add command refreshes the runtime by default. For Kubernetes YAML/CSV
providers, deploy to deliver the updated provider file to the Pod.

Verify that the key works without falling back to Alice's password:

```bash
sftp -o PreferredAuthentications=publickey -o IdentitiesOnly=yes \
  -i ./alice-key -P 2222 alice@127.0.0.1
```

The prompt for the key's passphrase unlocks the local private key. It is separate
from Alice's account password. Adding a key leaves password access available.

### Replace a device without losing access

For an overlapping change, add the replacement under a new name, test it from
the new client and then remove the old key:

```bash
sftpwarden user key add alice new-laptop --public-key ./new-laptop.pub
```

After verifying a real connection with the new private key:

```bash
sftpwarden user key remove alice laptop
sftpwarden user key rename alice new-laptop laptop
```

If you need to replace a key in place, `user key rotate` preserves its name and
metadata. Keep another tested credential available until you verify the rotation.

### Other key tasks

These are independent examples. Run only the command for your task:

| Task | Command |
| --- | --- |
| Inspect a key | `sftpwarden user key show alice laptop` |
| Replace its public key | `sftpwarden user key rotate alice laptop --public-key ./replacement.pub` |
| Temporarily revoke it | `sftpwarden user key disable alice laptop` |
| Re-enable it | `sftpwarden user key enable alice laptop` |
| Set a deadline | `sftpwarden user key expire alice laptop --at 2027-01-01` |
| Import public key files | `sftpwarden user key import alice --from-dir ./keys` |

Choose a future deadline appropriate to your use case. Import uses each `.pub`
filename as its key name. With one file, `--name` can override that name.
See [User Key Commands](cli/user-keys.md) for options and refresh controls.

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

## User schemas

These examples illustrate the two storage formats. The shortened keys and
fingerprints are placeholders, not usable credentials. Use the CLI to write real
key entries.

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
need per-key control, rotation, expiry, disable/enable, import or metadata.
Key names must match `^[a-z][a-z0-9._-]{0,63}$`. Fingerprints are derived from
the public key when omitted and are validated when present.

In schema v2, an explicit `keys` list takes precedence over legacy `public_keys`.
An empty list means no key authorization. Omitting `keys` still converts legacy
`public_keys` for compatibility; `keys: null` and other non-list values are invalid.


## Public-key validation

SFTPWarden checks the SSH wire structure and type of RSA, Ed25519, NIST P-256,
P-384 and P-521 ECDSA keys, including the supported OpenSSH security-key formats.
It rejects truncated or extra fields, type/blob mismatches, malformed lengths and
multiple key lines. Optional comments and a trailing file newline are accepted.
ECDSA points must use the uncompressed encoding accepted by OpenSSH.

These checks validate the encoding; they do not establish possession of the
private key or guarantee acceptance by a server's algorithm policy. Verify access
with a real connection before removing an existing working credential. The
`AAAA...` values above are placeholders; replace them with actual public keys.
The YAML/CSV examples in the repository contain public-only sample keys whose
private keys were discarded. Replace those sample keys before using the examples.
