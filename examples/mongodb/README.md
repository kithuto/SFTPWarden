# MongoDB Example

Use this provider when your environment already has accounts stored as MongoDB documents.
SFTPWarden manages accounts in that database; uploaded files still live in the
runtime's data storage.

## Prerequisites

- Python 3.11 or later and Docker with Compose v2.
- A database reachable from both your CLI host and the runtime container.
- Credentials with the permissions needed to read and manage the selected storage.
- `SFTPWARDEN_MONGODB_DSN` available in your shell through your normal secret-management workflow.

The DSN includes the database address and any credentials. Do not commit it or
paste real passwords into shell history. See
[credentials and transport](../../docs/providers.md#credentials-and-environment-variables)
for environment resolution, TLS and certificate-file requirements.
The host CLI does not automatically read a project's `.env` file.

## Create and deploy

Run this in a new directory:

```bash
python -m pip install "sftpwarden[mongodb]"
mkdir -p ~/sftpwarden-mongodb
cd ~/sftpwarden-mongodb
sftpwarden init mongodb-example \
  --provider mongodb \
  --dsn '${SFTPWARDEN_MONGODB_DSN}' \
  --collection sftp_users \
  --create-table \
  --yes
sftpwarden config server.host 127.0.0.1
sftpwarden user create alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden deploy
sftpwarden health
```

Enter Alice's password privately when prompted. `--create-table` authorizes
creation of the collection and username index.
It does not install a database server.
Use `--no-create-table` if storage must be provisioned separately.

## Verify and update

Health should succeed. Connect with `sftp -P 2222 alice@127.0.0.1`, verify the
server fingerprint and complete the
[file-transfer tutorial](../../docs/getting-started.md#connect-and-transfer-a-file).
Then add Alice's recommended SSH key using the same tutorial.

CLI user edits normally refresh access automatically. After an external database
edit, use `sftpwarden refresh --context mongodb-example` or wait for the enabled
sync loop. Test a new client connection to verify an access change.
If health fails, check runtime network access and credentials as well as the
database contents.

## Storage details

Schema v2 stores one document per user with `_id = username` and a named `keys`
array. Credentials also need collection metadata access. A missing or recreated
collection is an error rather than an empty account list.

Individual user edits work on standalone MongoDB. Import, copy, schema migration
and provider restore require a replica set or sharded cluster because they use
transactions.

See [Providers](../../docs/providers.md) for validation, concurrency and transfer
rules. The checked-out example configuration uses the same environment reference;
it is reference material, not a provisioned database.
