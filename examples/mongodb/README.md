# MongoDB Example

This example shows the MongoDB provider. MongoDB stores one document per user in
the configured collection. The document identity is `_id = username`.

Create a new MongoDB-backed project:

```bash
python -m pip install "sftpwarden[mongodb]"
export SFTPWARDEN_MONGODB_DSN='mongodb://mongo.example.com:27017/sftpwarden'

sftpwarden init mongodb-example \
  --provider mongodb \
  --dsn '${SFTPWARDEN_MONGODB_DSN}' \
  --collection sftp_users \
  --create-table \
  --yes
sftpwarden user add alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden deploy
```

Use this checked-out example after creating the collection manually or with
`sftpwarden init --create-table`:

```bash
cd examples/mongodb
export SFTPWARDEN_MONGODB_DSN='mongodb://mongo.example.com:27017/sftpwarden'
sftpwarden validate --config sftpwarden.yaml
sftpwarden context add mongodb-example --root . --yes
sftpwarden deploy --context mongodb-example --dry-run
```

During init, SFTPWarden can create the collection and the unique username index
for you.

Use an environment variable for the DSN so database credentials are not committed
in `sftpwarden.yaml`. Run `sftpwarden refresh --context mongodb-example` after
changing database-backed users outside SFTPWarden.
