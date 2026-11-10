# YAML Example

Use YAML when you want a readable account file and the simplest setup. These steps create a new
local Compose project. You need Python 3.11 or later, the installed SFTPWarden
CLI and Docker with Compose v2. Run commands from your administrator computer.

## Create and deploy

```bash
mkdir -p ~/sftpwarden-yaml
cd ~/sftpwarden-yaml
sftpwarden init yaml-example --yes
sftpwarden config server.host 127.0.0.1
sftpwarden user create alice --no-refresh
sftpwarden deploy --dry-run
sftpwarden deploy
sftpwarden health
```

Enter and confirm Alice's password when prompted. Health should succeed.
If another local server already uses port 2222, choose an available
`server.port` before deployment.

## Connect and manage access

```bash
sftp -P 2222 alice@127.0.0.1
```

Verify the server fingerprint before accepting it. Follow
[Getting Started](../../docs/getting-started.md#connect-and-transfer-a-file)
to upload and download a file, then add the recommended SSH key to Alice.

User commands refresh by default. If you edit the provider outside the CLI,
run `sftpwarden refresh --context yaml-example`. Configuration changes
require `sftpwarden deploy`.

## Storage and reference files

YAML is the built-in default. If you changed the global default, select YAML
with `--provider` during init or restore it with
`sftpwarden config default-provider yaml`.

Init creates `users.yaml`. Compose deploy moves the active file into
`provider/` and updates its configured path. Use that new path for external
edits; the original is a recovery copy.

The checked-out configuration is reference material. Its sample credentials are not yours; replace them before deployment.
See [Providers](../../docs/providers.md) for formats and migration behavior.
