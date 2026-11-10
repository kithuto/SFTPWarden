# Upgrade

`sftpwarden upgrade` updates the CLI and its installed watcher. Normally, servers
keep running their existing version until you explicitly deploy each context.
A verified backup is recommended before updating or deploying.

```bash
sftpwarden upgrade --check
sftpwarden upgrade --dry-run
sftpwarden upgrade
sftpwarden deploy --context prod --dry-run
sftpwarden deploy --context prod
```

The command first appeared in 1.4.0. To reach 1.4.0 from an older CLI, update it
with its original installer first; see [the upgrade runbook](../recovery.md#upgrade-from-130).

## Options

| Option | Effect |
| --- | --- |
| `--check` | Check completed stable releases without inspecting or changing deployments. |
| `--dry-run` | Inspect the installation, watcher and contexts and show the proposed changes. |
| `--to VERSION` | Select an available stable release explicitly. Downgrades are refused. |
| `--yes`, `-y` | Accept the update confirmation. Compatibility checks still apply. |
| `--deploy-all` | With `--yes`, explicitly authorize all-context deployment when compatibility requires it. |
| `--status` | Read the last update receipt without a network request. |
| `--json` | Return structured output without interactive prompts or update notices. |

## Compatibility and confirmation

Before changing anything, the command inspects registered contexts and checks
whether the replacement watcher can work with their running versions and provider
layouts. An inaccessible context is an error, not evidence that it is compatible.

If a context is incompatible, the confirmation lists the problem and explains
that continuing will deploy **all registered contexts**. This may interrupt active
transfers. Declining leaves the installation unchanged. For automation, accepting
this expanded scope requires both flags:

```bash
sftpwarden upgrade --yes --deploy-all --json
```

`--yes` alone refuses this operation. The watcher starts only after the coordinated
deployments restore compatibility. A failed step leaves an incomplete result with
recovery information; it is not reported as a completed update.

In particular, the 1.4.0 file-delivery watcher requires the 1.4.0 runtime protocol
and dedicated provider-directory mounts. A watched 1.3.0 deployment requires the
coordinated deployment path. It cannot safely use the new watcher unchanged.

## Installation and component results

Self-update supports writable pip virtual environments, pipx and uv tool
installations. It uses the installation's owning manager and interpreter and
preserves provider dependencies. Editable/source checkouts, system Python,
container-installed CLIs and unsupported manager configurations receive manual
update guidance before changes. Update a container through its image workflow.

Official runtime images advance during deploy; custom images remain unchanged.
Custom watcher images are also preserved, with an action message for their
maintainer. An incompatible custom runtime must be resolved before an automatic
coordinated update can proceed.

An absent watcher is not installed automatically. An inactive watcher remains
inactive. Replacement preserves delivery checkpoints and verifies a new process
before marking an active watcher complete. An already-current watcher is not
restarted unnecessarily.

Native service-account changes are refused before replacement. Run the update as
the original owner, or explicitly reinstall the watcher with the intended account.

Results distinguish CLI, watcher and deployment outcomes. A normal successful
management update explicitly reports that servers still need deploy. JSON without
`--yes` returns a confirmation-required result before changing anything.

## Interrupted updates

On Windows, an independent worker waits for the invoking CLI to exit before
replacing its installation. The initial result means the update was started;
check completion in a new terminal:

```bash
sftpwarden upgrade --status
sftpwarden upgrade --status --json
```

The private receipt under `SFTPWARDEN_HOME/updates/` records completed steps and
recovery actions. Preserve it on failure. A failed installer can leave the CLI or
watcher partially updated; automatic rollback of arbitrary package installations
is not promised. Repair the CLI using its original manager if it cannot start,
then inspect status and retry the requested update. Do not delete checkpoints or
migration recovery material to bypass an incomplete operation.

See [Operations](../operations.md#updating-sftpwarden) for staged updates and
release-notification settings.
