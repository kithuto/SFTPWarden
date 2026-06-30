# PostgreSQL Example

This example shows the PostgreSQL provider. SFTPWarden reads and mutates users
through the configured `sftp_users` table. The included `schema.sql` mirrors the
default table expected by SFTPWarden.

Create a new PostgreSQL-backed project:

```bash
python -m pip install "sftpwarden[postgres]"
export SFTPWARDEN_POSTGRES_DSN='postgresql://sftpwarden:change-me@db.example.com:5432/sftpwarden'

sftpwarden init postgres-example \
  --provider postgresql \
  --dsn '${SFTPWARDEN_POSTGRES_DSN}' \
  --create-table \
  --yes
sftpwarden user add alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden deploy
```

Use this checked-out example after creating the table manually or with
`sftpwarden init --create-table`:

```bash
cd examples/postgres
export SFTPWARDEN_POSTGRES_DSN='postgresql://sftpwarden:change-me@db.example.com:5432/sftpwarden'
sftpwarden validate --config sftpwarden.yaml
sftpwarden context add postgres-example --root . --yes
sftpwarden deploy --context postgres-example --dry-run
```

Use an environment variable for the DSN so database credentials are not committed
in `sftpwarden.yaml`. Run `sftpwarden refresh --context postgres-example` after
changing database-backed users outside SFTPWarden.
