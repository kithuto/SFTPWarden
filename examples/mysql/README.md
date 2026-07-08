# MySQL Example

This example shows the MySQL provider. SFTPWarden reads and mutates users
through the configured `sftp_users` table. The included `schema.sql` mirrors the
schema v2 tables expected by SFTPWarden, including `sftp_user_keys`.

Create a new MySQL-backed project:

```bash
python -m pip install "sftpwarden[mysql]"
export SFTPWARDEN_MYSQL_DSN='mysql://sftpwarden:change-me@db.example.com:3306/sftpwarden'

sftpwarden init mysql-example \
  --provider mysql \
  --dsn '${SFTPWARDEN_MYSQL_DSN}' \
  --create-table \
  --yes
sftpwarden user create alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden deploy
```

Use this checked-out example after creating the table manually or with
`sftpwarden init --create-table`:

```bash
cd examples/mysql
export SFTPWARDEN_MYSQL_DSN='mysql://sftpwarden:change-me@db.example.com:3306/sftpwarden'
sftpwarden validate --config sftpwarden.yaml
sftpwarden context add mysql-example --root . --yes
sftpwarden deploy --context mysql-example --dry-run
```

Use an environment variable for the DSN so database credentials are not committed
in `sftpwarden.yaml`. Run `sftpwarden refresh --context mysql-example` after
changing database-backed users outside SFTPWarden.
