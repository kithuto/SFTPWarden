# MariaDB Example

MariaDB uses the same PyMySQL dependency as the MySQL provider.

```bash
python -m pip install "sftpwarden[mariadb]"
export SFTPWARDEN_MARIADB_DSN='mariadb://sftpwarden:change-me@db.example.com:3306/sftpwarden'
sftpwarden init prod --provider mariadb --dsn '${SFTPWARDEN_MARIADB_DSN}' --create-table
```

Installing `sftpwarden[mysql]` also enables the MariaDB provider, and installing
`sftpwarden[mariadb]` also enables the MySQL provider.
