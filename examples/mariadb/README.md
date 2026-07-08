# MariaDB Example

This example shows the MariaDB provider. MariaDB uses the same PyMySQL
dependency as the MySQL provider. The included `schema.sql` mirrors the schema
v2 tables expected by SFTPWarden, including `sftp_user_keys`.

Create a new MariaDB-backed project:

```bash
python -m pip install "sftpwarden[mariadb]"
export SFTPWARDEN_MARIADB_DSN='mariadb://sftpwarden:change-me@db.example.com:3306/sftpwarden'

sftpwarden init mariadb-example \
  --provider mariadb \
  --dsn '${SFTPWARDEN_MARIADB_DSN}' \
  --create-table \
  --yes
sftpwarden user create alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden deploy
```

Use this checked-out example after creating the table manually or with
`sftpwarden init --create-table`:

```bash
cd examples/mariadb
export SFTPWARDEN_MARIADB_DSN='mariadb://sftpwarden:change-me@db.example.com:3306/sftpwarden'
sftpwarden validate --config sftpwarden.yaml
sftpwarden context add mariadb-example --root . --yes
sftpwarden deploy --context mariadb-example --dry-run
```

Installing `sftpwarden[mysql]` also enables the MariaDB provider, and installing
`sftpwarden[mariadb]` also enables the MySQL provider.

Use an environment variable for the DSN so database credentials are not committed
in `sftpwarden.yaml`. Run `sftpwarden refresh --context mariadb-example` after
changing database-backed users outside SFTPWarden.
