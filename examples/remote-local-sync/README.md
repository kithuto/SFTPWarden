# Remote Local-Sync Example

```bash
sftpwarden context add prod deploy@example.com:/opt/sftpwarden \
  --root ~/sftpwarden-prod \
  --critical
```

Local config/provider files are the source of truth. Use `sftpwarden watch` to sync changes and `sftpwarden refresh -c prod` to apply them.

