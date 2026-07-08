# SQLite Example

This example shows the SQLite provider. SQLite is built in and works well for a
small single-host deployment where SFTPWarden is the only writer.

Create a new SQLite-backed project:

```bash
mkdir -p ~/sftpwarden-sqlite
cd ~/sftpwarden-sqlite
sftpwarden init sqlite-example --provider sqlite --yes
sftpwarden user create alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden deploy
```

Use the checked-out config for validation or as a reference:

```bash
cd examples/sqlite
sftpwarden validate --config sftpwarden.yaml
```

Do not copy a generated SQLite database between unrelated projects unless you
intend to copy its users too. For a deployable SQLite project, use
`sftpwarden init --provider sqlite` so SFTPWarden creates a schema v2
`users.sqlite` with the named-key table.

Avoid SQLite for NFS, high concurrency, or multi-writer deployments.
