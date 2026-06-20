# Remote Local-Sync Example

```bash
mkdir -p ~/sftpwarden-prod
cd ~/sftpwarden-prod
sftpwarden init prod --remote deploy@example.com:/opt/sftpwarden \
  --critical
```

Local config/provider files are the source of truth. Use `sftpwarden watch` to
sync changes and `sftpwarden refresh` to apply them.

Use `sftpwarden context add prod deploy@example.com:/opt/sftpwarden --critical`
only when that remote project already exists and you want to register it locally.
