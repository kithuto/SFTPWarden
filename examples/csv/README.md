# CSV Example

This example shows the CSV file provider. It is useful when user lists are
reviewed or handed off through spreadsheet-based workflows.

Create a new CSV-backed project:

```bash
mkdir -p ~/sftpwarden-csv
cd ~/sftpwarden-csv
sftpwarden init csv-example --provider csv --yes
sftpwarden user add alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden deploy
```

Use this checked-out example as a reference or local smoke test:

```bash
cd examples/csv
sftpwarden validate --config sftpwarden.yaml
sftpwarden context add csv-example --root . --yes
sftpwarden deploy --context csv-example --dry-run
```

The provider file is `users.csv`. Keep the header row intact and replace the
example password hash before using it outside local testing.

Use `sftpwarden refresh --context csv-example` after changing users and
`sftpwarden deploy --context csv-example` after changing `sftpwarden.yaml`.
