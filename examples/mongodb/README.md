# MongoDB Example

MongoDB stores one document per user in the configured collection. The document
identity is `_id = username`.

```bash
python -m pip install "sftpwarden[mongodb]"
export SFTPWARDEN_MONGODB_DSN='mongodb://mongo.example.com:27017/sftpwarden'
sftpwarden init prod --provider mongodb --dsn '${SFTPWARDEN_MONGODB_DSN}' --collection sftp_users
```

During init, SFTPWarden can create the collection and the unique username index
for you.
