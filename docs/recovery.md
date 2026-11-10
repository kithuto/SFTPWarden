# Recovery and upgrades

Use this guide to preserve an existing installation, restore it to a verified
destination or upgrade from 1.3.0. A recovery includes account access, server
identity and file ownership as well as file contents.

| Your task | Start here |
| --- | --- |
| Create a recovery archive | [Capture the running installation](#capture-the-running-installation) |
| Restore to another local directory | [Local recovery](#local-recovery) |
| Recover database-backed accounts | [External database recovery](#external-database-recovery) |
| Restore into a cluster | [Kubernetes and Helm recovery](#kubernetes-and-helm-recovery) |
| Upgrade an existing 1.3.0 deployment | [Upgrade from 1.3.0](#upgrade-from-130) |

Commands run from the administrator's CLI. Replace `prod` with the source
context and use a separate destination name. Confirm both before maintenance.

Runtime capture uses a helper image matching the installed CLI release. The
Docker host or cluster must be able to obtain that image before maintenance.

## Before maintenance

Pause external provider editors, database writers, the watcher and uploads before
maintenance. SFTPWarden coordinates its own operations; its locks cannot stop an
unrelated database client or filesystem editor. Maintenance capture terminates
remaining transfers. Keep the original installation and recovery archive until
the replacement passes health and authenticated SFTP checks with the original
trusted host keys.

## Capture the running installation

Check `sftpwarden context show prod` and arrange downtime. This example includes
uploaded files; without `--include-data`, they are excluded:

```bash
sftpwarden backup --context prod --maintenance --include-data --output recovery.tar.gz --yes
```

Keep the reported archive in restricted storage. A command failure means the
capture or resumption needs attention; do not treat a partially produced file as
a successful backup.

### What capture does

For Compose, capture reads the selected container's applied configuration and
actual mounts. Remote contexts capture the remote runtime, including local-sync
contexts with a different local desired configuration. Kubernetes capture reads
the selected workload, provider, state/data claims and actual SSH identity. It
temporarily scales the workload to zero and uses a disposable helper Pod. The
helper and runtime must be able to reach an external provider when one is used.

An undeployed project can be backed up without maintenance only while it has no
runtime state or host keys. Including nonempty `data/` also requires maintenance:
files without retained runtime identity cannot form a usable recovery archive.

The command resumes only a previously running installation. It verifies resumed
health and cluster identity, before publishing the archive. Inspect reported
cleanup or resumption failures before retrying. Do not remove an operation lock
until its owning process has stopped and the target has been inspected.

### Archive contents and limits

Archives contain a logical provider snapshot, configuration, host keys, runtime
state and optional user data, with file checksums and Linux ownership/modes.
Only regular files and directories are supported. Links, devices, sockets and
FIFOs cause refusal; move unsupported entries out of the selected backup scope
or preserve them separately. An archive is limited to 100,000 members, 100 GiB
expanded content and 64 MiB per recovery metadata document. Checksums detect
corruption, not authenticity. Accept archives only from a trusted source.

Environment references remain references. Preserve the referenced credentials,
database access, CA/client certificate files and custom infrastructure separately;
the archive is not a backup of your database server, secret manager or cluster.
Custom Compose additions are not reproduced in generated recovery Compose files.
Recreate required external mounts and network access before activation.

## Choose the destination

| Destination | Behavior |
| --- | --- |
| Readable local installation | Verify a complete safety backup before replacement. A deployed runtime requires `--maintenance` and matching layout/config/provider destinations. |
| Empty directory | Use `--root`; its parent must exist. Unrelated content, links and ambiguous ownership are refused. |
| Damaged local installation | Preserve readable originals privately with an omission inventory. This artifact is incomplete and does not authorize overwrite. Recover separately. |
| Remote installation | Select a context pointing to a separate empty remote root. Existing remote contents are refused. |
| Kubernetes/Helm | Select a separate namespace/release with no existing workload or selected PVCs. Existing claims are never treated as empty. |
| Inaccessible or conflicting target | Stop before replacement; a failed inspection is not proof of absence. |

### Local recovery

```bash
sftpwarden restore recovery.tar.gz --root ./recovered --include-data --yes
```

The result reports `files_restored` and `activation_required: true`. Register
the recovered directory and inspect the selected destination:

```bash
sftpwarden context add recovered --root ./recovered
sftpwarden context use recovered
sftpwarden context show
cat ./recovered/sftpwarden.yaml
```

Provide external secrets and resolve any port or container-name conflict with
the source before activation. Then preview and deploy:

```bash
sftpwarden deploy --dry-run
sftpwarden deploy
sftpwarden health
```

Check a real SFTP upload and download against the recovered server. Linux metadata is applied and verified
inside the runtime, including when the CLI runs on macOS or Windows. Do not
claim recovery from a successful host-side copy alone.

### Replacing an existing local runtime

For an existing local runtime, use `restore --context NAME --maintenance` only
with matching provider settings, generated Compose and expected bind mounts.
Pause all writers and close external SQLite connections. Without `--include-data`,
retained user IDs must be compatible with the recovered state. The verified safety
archive and private `.sftpwarden/restore-transaction.json` record remain available.
In-place replacement needs host read/write access to protected runtime files.
On native Linux this normally requires an administrator; membership in the Docker
group alone does not grant filesystem access. Without those permissions, restore
stops during inspection before replacing anything. Use a separate empty `--root`
for recovery without elevated host filesystem access. When an administrator restores
an operator-owned project, its configuration, provider and journal retain the
project owner's access.

### Interrupted local recovery

Before the first replacement effect, failure resumes a previously running
runtime. After replacement starts, failure keeps it stopped and blocks startup
with mixed contents. Retain the journal, source and safety archive. Recover the
safety archive into a separate empty destination or reconcile every recorded
step before explicit activation. Never delete a blocker merely to start SSH.

### External database recovery

An empty directory does not isolate a database. Initialize a **separate empty
database/table or collection**, with the same provider type and schema and
register a local context whose configuration selects it. Keep writers paused.

```bash
sftpwarden restore recovery.tar.gz --root ./recovered \
  --provider-context empty-database --include-data --yes
```

The destination's logical safety snapshot is verified before checked replacement.
The configured namespace must differ from the source; a DNS alias is not evidence
of isolation. MongoDB bulk replacement requires a replica set. The private sibling
`.recovered.provider-recovery.json` records provider and file publication steps.
Retain it and the safety archive. If interrupted, inspect that receipt and protect
the database before retrying; filesystem rollback alone does not undo a committed
database change.

For remote recovery, first complete this local recovery and register the recovered
project. Then use `sftpwarden restore recovery.tar.gz --context EMPTY_REMOTE
--provider-context RECOVERED_LOCAL --include-data` (on one shell line). Replace
both uppercase names with the registered destination contexts.
The second step verifies the completed receipt and unchanged database contents,
then restores runtime files; it does not replace the database again. Supply the
new destination's credentials on the remote host before deploy.

### Kubernetes and Helm recovery

First restore into a separate local `--root` (including `--provider-context` for
external databases). Register that recovered project, select Kubernetes mode and
a separate namespace/release and keep the archived runtime policy unchanged.
Then restore the same archive with `--context RECOVERED_CLUSTER --include-data`.
Only after file recovery succeeds, run `deploy` for that context.

The helper restores into new data/state/provider/identity claims and verifies
Linux metadata. Failed claims remain for inspection with an incomplete marker;
they are not automatically deleted. If using `kubernetes.host_keys_secret`, create
the matching Secret privately in the new namespace before recovery. A missing or
mismatched identity is refused. External providers use the separately recovered
database and do not allocate a provider PVC.

### Verify before retiring the source

Complete these checks against the restored runtime:

1. Health succeeds and the expected configuration is applied.
2. Every retained host-key fingerprint matches the trusted source.
3. Account UID/GID values and file ownership match the archive.
4. File hashes match and an authenticated SFTP upload/download succeeds.

Keep the source and archives until all checks pass. A health check alone does
not verify restored file contents or client access.

(upgrade-from-130)=
## Upgrade from 1.3.0

The `upgrade` command is new in 1.3.1. First update the CLI with the manager that
installed it, preserving the same environment and provider extras. For example,
use `python -m pip install --upgrade 'sftpwarden[mysql]==1.3.1'` in the owning
virtual environment, `pipx upgrade sftpwarden` for pipx, or
`uv tool upgrade sftpwarden` for an unpinned uv tool. A pinned uv installation
requires updating its explicit requirement with `uv tool install --force` and
retaining its original extras/options. Source checkouts and container CLIs use
their source/image update procedure instead.

Stop an old active watcher before replacing its native CLI installation. After
installing 1.3.1, preview `sftpwarden upgrade --dry-run`. A file-delivery watcher
cannot remain on the legacy runtime protocol: confirm the proposed all-context
deployment during the maintenance window, or deploy the contexts explicitly
before replacing the watcher. Read [Upgrade](cli/upgrade.md) for the complete
consent and recovery contract. Subsequent supported CLI updates can use `upgrade`.

Take a verified recovery point before changing an existing deployment. Version
1.3.0 does not record the applied configuration needed by the new maintenance
capture. Preserve its actual mounted configuration, provider, state, host keys
and data during a stopped maintenance window and verify that recovery material
independently. A project-only archive is insufficient evidence of runtime recovery.

### Compose provider layout

Deploy migrates YAML/CSV/SQLite files from a legacy single-file mount into the
dedicated `provider/` directory and updates configuration and generated Compose.
It preserves recovery material and a migration receipt. This directory mount
makes atomic file replacement visible inside the runtime. Update external editors
and scripts to the new path after deploy. Refresh and the watcher cannot perform
this deployment migration. If interrupted, inspect the receipt before retrying;
do not remove the preserved source until the deployed mount is verified.

### Kubernetes identity and storage

Set the explicit cluster context, namespace and release before preparation:

CLI deploy, `kube apply` and `helm upgrade` perform preparation automatically.
The separate preparation commands below are useful for inspection and for direct
external Helm/kubectl workflows; they are not an extra mandatory migration step
before every CLI deploy.

```bash
sftpwarden kube prepare --context prod --dry-run
sftpwarden kube prepare --context prod
sftpwarden deploy --context prod --dry-run
sftpwarden deploy --context prod --yes
```

Preparation captures all active host-key algorithms from the existing Pod, checks
the selected durable source and records public fingerprints. It also switches a
legacy rolling-update strategy to explicit activation without replacing the Pod;
concurrent workload changes are rejected before recording successful preparation.
Generated identity
uses a retained PVC. Operator-provided identity uses the selected Secret and must
already contain valid matching keys; it is never overwritten with an empty Secret.
If the old ephemeral keys are already lost, preservation cannot be claimed. Recover
them from trusted material or perform an explicit client trust change.

For direct Helm use, run `kube prepare` first, write the generated values and then
upgrade the chart. An unprepared legacy upgrade is refused. The StatefulSet uses
`OnDelete`: updating Helm resources does not itself replace the running Pod.
Review the result and explicitly delete the selected `<release>-0` Pod to activate
it, then verify readiness, fingerprints and SFTP. Direct manifest application
requires the same ordered preparation; offline rendering cannot enforce this
operator procedure. CLI deploy performs preparation and explicit activation.

Data/state/provider/host-key PVCs have retention annotations. A populated legacy
chart-owned Secret is retained in both live resources and the Helm release record
before its template can disappear. `helm uninstall` through SFTPWarden refuses an
unprepared retention record. Reinstall under the same name/namespace with retained
claims and matching identity; external Secrets keep their original ownership.
PV reclaim policy still matters if an operator explicitly deletes a claim.

If candidate activation fails, retain the prepared identity and claims. Restore
the last verified candidate configuration/image and explicitly replace the failed
Pod. Do not blindly `helm rollback` to an old chart revision that used ephemeral
identity or lacked recorded retention. A rollback to 1.3.0 requires the pre-upgrade
recovery material and a separately verified recovery destination. Never rotate
keys or delete retained storage as a shortcut around failed activation.
