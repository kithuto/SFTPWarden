"""Fresh files plus an explicitly selected, empty and protected external provider."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import replace
from pathlib import Path

from sftpwarden.config import FILE_PROVIDER_TYPES, ProviderConfig, load_config, write_config
from sftpwarden.contexts import ContextEntry, ContextType, resolve_context
from sftpwarden.providers import provider_from_config
from sftpwarden.providers.destination import destination_fingerprint
from sftpwarden.providers.schema import require_configured_schema
from sftpwarden.render.compose import write_compose
from sftpwarden.services.backup_inventory import member_metadata
from sftpwarden.services.backup_manifest import ValidatedBackup
from sftpwarden.services.restore_existing import replace_external_provider, verify_safety_archive
from sftpwarden.services.restore_target import inspect_empty_target, publish_fresh_files
from sftpwarden.users import users_fingerprint
from sftpwarden.utils.errors import RuntimeError
from sftpwarden.utils.persistence import atomic_bytes
from sftpwarden.utils.state_transactions import state_transaction


def restore_external_fresh(
    root: str,
    provider_context: str,
    source: Path,
    manifest: ValidatedBackup,
    entries: list[str],
    *,
    dry_run: bool,
) -> tuple[Path, Path]:
    """Never infer database isolation from an empty filesystem directory."""
    from sftpwarden.services.backup import default_backup_path, write_backup_archive

    if manifest.config.provider.type in FILE_PROVIDER_TYPES:
        raise RuntimeError("--provider-context is only valid for external database archives.")
    if manifest.config_path != "sftpwarden.yaml":
        raise RuntimeError(
            "Fresh external runtime recovery requires the standard captured configuration path."
        )
    selected = resolve_context(context_name=provider_context, read_only=True)
    if selected.type != ContextType.LOCAL or not selected.config or not selected.root:
        raise RuntimeError(
            "Provider recovery requires an explicit local context for the new database."
        )
    target = load_config(selected.config)
    if (
        target.provider.type != manifest.config.provider.type
        or target.provider.user_schema != manifest.users.schema_version
    ):
        raise RuntimeError("Recovery provider type and schema must match the selected database.")
    source_identity = manifest.provider_destination
    target_identity = destination_fingerprint(target.provider)
    if source_identity is None or target_identity is None or source_identity == target_identity:
        raise RuntimeError(
            "Fresh provider recovery requires a separately identified database namespace.",
            suggestion=(
                "Use an archive with an explicit source destination and select a distinct "
                "initialized empty database/collection; aliases alone do not prove "
                "isolation."
            ),
        )
    config = manifest.config.model_copy(deep=True)
    config.provider = target.provider.model_copy(deep=True)
    rebound = replace(manifest, config=config)
    destination = inspect_empty_target(
        root, rebound, source=source, entries=entries, protected_external=True
    )
    provider = provider_from_config(selected.root, target)
    expected = require_configured_schema(provider)
    if expected.users:
        raise RuntimeError(
            "Fresh external recovery requires an initialized empty provider destination."
        )
    safety = default_backup_path("empty-provider-safety").absolute()
    if dry_run:
        return destination, safety
    with state_transaction(Path(selected.config)):
        if (
            load_config(selected.config) != target
            or require_configured_schema(provider) != expected
        ):
            raise RuntimeError(
                "Provider destination changed before recovery; no replacement was started."
            )
        write_backup_archive(
            output_path=safety,
            root=Path(selected.root),
            entries=[Path(selected.config).relative_to(Path(selected.root)).as_posix()],
            context_name=selected.name,
            provider_type=target.provider.type.value,
            include_data=False,
            config_path=Path(selected.config),
            topology="external-provider-destination",
        )
        protected = verify_safety_archive(safety)
        if protected.users != expected or protected.provider_destination != target_identity:
            raise RuntimeError("The empty destination safety archive did not verify.")
        # Only private staging changes until all configuration/material has been checked.
        write_config(source / manifest.config_path, config)
        write_compose(config, source, allow_local_build=False)
        selected_entries = list(entries)
        if config.docker.compose_file not in selected_entries:
            selected_entries.append(config.docker.compose_file)
        receipt = destination.with_name("." + destination.name + ".provider-recovery.json")
        if receipt.exists() or receipt.is_symlink():
            raise RuntimeError(
                "An earlier provider recovery receipt requires inspection before retrying."
            )
        record = {
            "format": 1,
            "phase": "provider_pending",
            "safety_backup": str(safety),
            "target_root": str(destination),
            "provider_destination": target_identity,
            "source_provider_destination": source_identity,
            "users_fingerprint": users_fingerprint(manifest.users),
        }
        atomic_bytes(receipt, json.dumps(record).encode())
        try:
            replace_external_provider(provider, expected, manifest.users)
            record["phase"] = "provider_restored_files_pending"
            atomic_bytes(receipt, json.dumps(record).encode())
            publish_fresh_files(destination, source, selected_entries, rebound)
            record["phase"] = "files_restored_activation_required"
            atomic_bytes(receipt, json.dumps(record).encode())
        except Exception:
            raise RuntimeError(
                (
                    "Fresh external recovery is incomplete; inspect the private provider "
                    "recovery receipt before retrying."
                ),
                suggestion=(
                    f"Retain {receipt}, {safety}, and the source archive. "
                    "Reconcile the selected provider using its checked safety snapshot "
                    "before activation."
                ),
            ) from None
    return destination, safety


def protected_provider(entry: ContextEntry, manifest: ValidatedBackup) -> ProviderConfig:
    """Verify the separately completed database recovery before copying runtime files."""
    if entry.type != ContextType.LOCAL or not entry.root or not entry.config:
        raise RuntimeError(
            "Select the local context created by the separate --root --provider-context recovery."
        )
    root = Path(entry.root).resolve()
    receipt = root.with_name("." + root.name + ".provider-recovery.json")
    try:
        if receipt.is_symlink() or receipt.stat().st_size > 65536:
            raise ValueError
        record = json.loads(receipt.read_text(encoding="utf-8"))
        config = load_config(entry.config)
        identity = destination_fingerprint(config.provider)
        if (
            record.get("format") != 1
            or record.get("phase") != "files_restored_activation_required"
            or record.get("target_root") != str(root)
            or identity is None
            or record.get("provider_destination") != identity
            or record.get("source_provider_destination") != manifest.provider_destination
            or identity == manifest.provider_destination
            or record.get("users_fingerprint") != users_fingerprint(manifest.users)
            or config.provider.type != manifest.config.provider.type
            or config.provider.user_schema != manifest.users.schema_version
        ):
            raise ValueError
        safety = verify_safety_archive(Path(record["safety_backup"]))
        if safety.users.users or safety.provider_destination != identity:
            raise ValueError
        if require_configured_schema(provider_from_config(root, config)) != manifest.users:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError(
            "Separate provider recovery is missing, changed or incomplete; runtime "
            "recovery was not started."
        ) from None
    return config.provider


def rebind_archive(
    source: Path, manifest: ValidatedBackup, provider: ProviderConfig, output: Path
) -> None:
    """Retarget a private recovery stream while preserving its validated Linux metadata."""
    config = manifest.config.model_copy(deep=True)
    config.provider = provider.model_copy(deep=True)
    write_config(source / manifest.config_path, config)
    write_compose(config, source, allow_local_build=False)
    document = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    members = dict(manifest.members)
    if config.docker.compose_file not in members:
        members[config.docker.compose_file] = {"type": "file", "mode": 0o600, "uid": 0, "gid": 0}
        document["entries"].append(config.docker.compose_file)
    inventory = {}
    with output.open("xb") as stream:
        output.chmod(0o600)
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, metadata in sorted(members.items()):
                member = tarfile.TarInfo(name)
                member.mode = int(metadata["mode"])
                member.uid, member.gid = int(metadata["uid"]), int(metadata["gid"])
                if metadata["type"] == "directory":
                    member.type = tarfile.DIRTYPE
                    archive.addfile(member)
                    inventory[name] = member_metadata(member)
                else:
                    path = source / name
                    member.size = path.stat().st_size
                    with path.open("rb") as content:
                        digest = hashlib.file_digest(content, "sha256").hexdigest()
                        content.seek(0)
                        archive.addfile(member, content)
                    inventory[name] = member_metadata(member, digest)
            document["members"] = inventory
            document["provider_destination"] = destination_fingerprint(provider)
            payload = json.dumps(document, sort_keys=True).encode()
            header = tarfile.TarInfo("manifest.json")
            header.mode, header.size = 0o600, len(payload)
            archive.addfile(header, io.BytesIO(payload))
    verify_safety_archive(output)
