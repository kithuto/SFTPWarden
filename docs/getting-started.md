# Getting Started

By the end of this tutorial, you will have a local SFTP server, an account named
`alice` and a file you have uploaded and downloaded through that account.

SFTP transfers files over an encrypted SSH connection. SFTPWarden sets up the
OpenSSH server in Docker and manages its users. You will use Docker Compose,
which runs the container and connects it to persistent folders on your computer.
User accounts will be stored in a YAML text file.

This tutorial is for **SFTPWarden 1.3.1**. Everything runs on your own computer.
The server will listen only on `127.0.0.1`, so this is a local learning environment.

## Before You Start

Use a macOS or Linux terminal with:

- Python **3.11 or newer** with `pip`.
- A running Docker engine and **Docker Compose v2**.
- OpenSSH client tools: `ssh-keygen` and `sftp`.
- Ports **2222** and, for the later configuration exercise, **2200** available.

The commands below use a POSIX shell such as Bash or Zsh. They are not PowerShell
commands. On Windows, use an appropriate Linux environment with its own access to
Docker to follow this shell-based tutorial.

Check the main prerequisites:

```bash
python --version
docker info
docker compose version
command -v ssh-keygen sftp
```

Python must report at least 3.11, Docker must report a reachable server, Compose
must report v2 and the final command must locate both client tools. If your
Python executable is named `python3` or has a version suffix, use that instead of
`python` below.

## Install

Install SFTPWarden:

```bash
python -m pip install sftpwarden
```

Deployment uses a runtime image matching the installed CLI version. The first
deployment may take longer while Docker downloads it.

If you are contributing from a source checkout, follow
[Install from Source](contributing.md#install-from-source) instead of the package
installation above. Source deployments build the runtime from that checkout.

## Create a First Project

Use a new directory and an unused context name. If you already have a
`sftpwarden-demo` directory or a `demo` context, choose different names and use
them consistently throughout the tutorial.

```bash
mkdir ~/sftpwarden-demo
cd ~/sftpwarden-demo
sftpwarden init demo --yes
sftpwarden config server.host 127.0.0.1
sftpwarden context current
```

`init` creates the project and registers a **context** named `demo`. A context
remembers which project you are administering. The final command should identify
`demo` as the active context.

`--yes` accepts initialization defaults. YAML is the built-in user provider,
so a fresh installation needs no provider option. This tutorial assumes that
default has not been changed.

To choose a different provider for one project, use `--provider`, for example
`sftpwarden init another-demo --provider sqlite --yes`. To change the default
for future projects, use `sftpwarden config default-provider sqlite`. Use
`sftpwarden config default-provider yaml` to return to YAML. These are
alternatives for later use, not additional steps in this tutorial.

The host setting restricts the eventual Docker port binding to your machine.
Saving a setting does not start the server.

These are the important project files and folders:

| Item | Purpose |
| --- | --- |
| `sftpwarden.yaml` | Settings for this installation |
| `users.yaml`, then `provider/users.yaml` after the first deploy | Accounts and their credentials |
| `docker-compose.yml` | Generated instructions for running the container |
| `data/` | Uploaded user files |
| `host_keys/` | Keys identifying the server to SFTP clients |
| `state/` | Runtime information, including managed user identifiers |

The first deployment prepares the dedicated `provider/` directory and updates
the configured provider path. Use the CLI for account changes in this tutorial.
Do not continue editing the original `users.yaml` after deployment.

**Context selection matters:** changing directories does not necessarily select
another context. When returning to this project, run `sftpwarden context use demo`.
The Docker Compose commands below must also run from this project's directory.

## Add a User

Start with a username and password. Create Alice's account:

```bash
sftpwarden user create alice --no-refresh
sftpwarden users
```

The CLI asks for a password and then asks you to repeat it. Choose a strong
password for this account. Your input is hidden and SFTPWarden stores a password
hash rather than the password itself.

The user list should include `alice`. `--no-refresh` saves the account without
trying to contact a server that has not been deployed yet. The first deployment
will apply it.

You will use this password for the first transfer. Afterward,
[add an SSH key to Alice's account](#use-an-ssh-key-recommended) for the recommended
authentication method.

## Deploy and Refresh

Validate the project, then start the server:

```bash
sftpwarden validate
sftpwarden deploy
sftpwarden health
```

Validation checks configuration and the provider location. Deployment prepares the
container and applies the account. A successful health check confirms that the
runtime is ready; a real transfer in the next step checks that Alice can use it.

For later administration, remember:

| What changed? | What applies it? |
| --- | --- |
| A project setting, such as the SFTP port | `sftpwarden deploy` |
| User data already visible to this local runtime | Automatic synchronization or `sftpwarden refresh` to apply it now |

You can add users whenever you need them, either with a command such as
`sftpwarden user create bob` or by editing the active `provider/users.yaml` file
in its supported format. User commands normally apply access changes immediately.
Direct edits are picked up by automatic synchronization, which is enabled by
default with a periodic interval of 60 seconds. No server restart is needed.
Use the CLI to enter passwords privately; provider files accept password hashes.

`--no-refresh` deliberately skipped the immediate refresh during initial setup.
See [Configuration Effects](configuration-effects.md) for other deployment
models and the full activation rules.

## Connect and Transfer a File

First create a small file in your project directory:

```bash
printf 'Hello from SFTPWarden!\n' > hello.txt
```

Before connecting, read the server's host-key fingerprints through Docker:

```bash
docker compose exec sftpwarden sh -c 'for key in /etc/sftpwarden/host_keys/*.pub; do ssh-keygen -lf "$key"; done'
```

Keep the `SHA256:...` fingerprints in view. Now start an SFTP connection from the
same terminal and directory:

```bash
sftp -P 2222 alice@127.0.0.1
```

`-P` selects the published port. SSH chooses the server key type automatically.
The server key identifies the server; Alice's password identifies Alice.

On the first connection, SSH asks whether to trust the server. Compare its
fingerprint with the Docker output for the same key type. Type `yes` only if
they match. Enter Alice's
password when asked. You should then see an `sftp>` prompt.

At that prompt, run these commands without adding the prompt itself:

```text
pwd
ls
put hello.txt upload/hello.txt
get upload/hello.txt received.txt
bye
```

Alice sees a restricted root directory, normally shown as `/`, with an
`upload` directory inside it. The root is intentionally not writable. Put files
inside `upload/`; an attempt to upload directly into `/` is expected to fail.

`put` sends the local file to the server. `get` retrieves it under a different
local name. `bye` returns you to your normal terminal.

Verify the round trip:

```bash
cmp hello.txt received.txt
```

No output and a successful exit mean the files match. You have now verified an
authenticated SFTP connection and a complete upload/download.

## Use an SSH Key (Recommended)

For regular use, we recommend a passphrase-protected SSH key. Keys resist
password guessing and authenticate without sending the private key to the
server. Keep the private key protected: anyone who can use it can access the
account.

Continue with the same Alice account and the running demo. In your normal
terminal, create a dedicated key pair:

```bash
ssh-keygen -t ed25519 -f ./alice-key -C sftpwarden-demo-alice
```

Choose and confirm a passphrase when prompted. If the filename already exists,
stop and choose another instead of overwriting a key.

The command creates `alice-key`, the **private key** used by your client and
`alice-key.pub`, the **public key** you can give to the server. Keep the private
file out of source control.

Add the public key to Alice's existing account under the name `laptop`:

```bash
sftpwarden user key add alice laptop --public-key ./alice-key.pub
```

This command saves the key and refreshes access in the running local server.
You do not need to recreate Alice or deploy the server again.

Test key authentication:

```bash
sftp -o PreferredAuthentications=publickey -o IdentitiesOnly=yes \
  -i ./alice-key -P 2222 alice@127.0.0.1
```

`-i` selects your private key. The two options restrict this test to public-key
authentication using that identity, so a working password cannot hide a key
setup problem. If asked, enter the **key passphrase**, which unlocks the local
key, rather than Alice's account password.

At the `sftp>` prompt, confirm you can still retrieve the file:

```text
get upload/hello.txt received-with-key.txt
bye
```

Then run `cmp hello.txt received-with-key.txt` in your terminal. No output and a
successful exit confirm that the file matches.

Adding a key leaves password access enabled. Once key access works for every
account that needs it, follow [Key-Only Deployments](security.md#key-only-deployments)
to disable password authentication for the server.

## Check the Project

Use these commands when returning to the demo:

```bash
sftpwarden context use demo
sftpwarden users
sftpwarden health
```

To inspect proposed user changes before applying them, use `sftpwarden plan`.
For configuration changes, start with the
[port-change walkthrough](configuration.md#change-the-local-sftp-port).

## Stop and Resume the Demo

Exit the SFTP session first. From `~/sftpwarden-demo`, stop and remove the
container and its Compose network:

```bash
docker compose down
```

The project folders remain, including Alice's uploaded file, server host keys
and runtime state. To start the same environment again:

```bash
sftpwarden context use demo
sftpwarden deploy
sftpwarden health
```

The server should keep its identity because `host_keys/` remains in place.
You can reconnect with the earlier SFTP command and retrieve the uploaded file.

To permanently remove an environment later, first preserve any files you need
and read [context cleanup](operations.md#context-cleanup). Removing a registered
context can also delete its project directory; it is different from stopping
the container.

## If Something Does Not Work

| Symptom | Check |
| --- | --- |
| `sftpwarden` is not found | Check that installation succeeded and that your Python installation's scripts directory is on `PATH`. |
| Docker cannot be reached | Start the Docker engine and rerun `docker info`. |
| Deployment reports that port 2222 is occupied | Choose an unused port with `sftpwarden config server.port 2200`, deploy again and use that port in the SFTP command. |
| The connection is refused | Run `sftpwarden health` and `docker compose ps`; check that the container is running and that the client port matches the deployed port. |
| Password authentication is denied | Confirm `sftpwarden users` lists Alice, enter the password chosen during creation and check that deployment succeeded. |
| Key authentication is denied | Confirm you added the matching `.pub` file to Alice and the automatic refresh succeeded. Use the private key with `-i`. |
| SSH reports too many authentication failures | Add `-o IdentitiesOnly=yes` to the SFTP command to use the explicitly selected key instead of extra agent identities. |
| Upload reports permission denied | Use `upload/hello.txt` as the destination, not a file directly under `/`. |
| SSH reports a changed host key | Stop and compare fingerprints. Another server or a recreated identity may be using that port; do not bypass the check. |

For additional diagnostics, see [Operations](operations.md#troubleshooting).

## Choose the Next Guide

| Next goal | Read |
| --- | --- |
| Change the local port and understand configuration | [Configuration](configuration.md#change-the-local-sftp-port) |
| Deploy on another machine or in a cluster | [Operations](operations.md) |
| Choose another source of user accounts | [Providers](providers.md) |
| Add, rotate or expire named SSH keys | [Named Keys](named-keys.md) |
| Look up an exact command or option | [CLI Reference](cli-reference.md) |
| Prepare for access from other machines | [Security](security.md) |
| Back up, restore or upgrade an installation | [Recovery](recovery.md) |
