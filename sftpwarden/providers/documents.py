"""Strict document parsing shared by file providers, import and recovery."""

from __future__ import annotations

import csv
import io
from typing import Any, Literal

import yaml

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    # Both loaders implement SafeConstructor, including the mapping hook below.
    from yaml import SafeLoader  # type: ignore[assignment]

from sftpwarden.users import ProviderUsers
from sftpwarden.users.schemas import detect_csv_schema, users_from_mapping
from sftpwarden.utils.errors import ProviderError
from sftpwarden.utils.json import strict_json_value

DocumentFormat = Literal["yaml", "csv", "json"]


class _UniqueKeyLoader(SafeLoader):
    """Reject duplicate explicit keys while preserving normal YAML merge semantics."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        explicit: set[Any] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            if key in explicit:
                raise ValueError("Duplicate mapping key")
            explicit.add(key)
        return super().construct_mapping(node, deep=deep)


def parse_provider_document(text: str, fmt: DocumentFormat) -> ProviderUsers:
    """Require explicit complete input and keep sensitive parser values out of errors."""
    try:
        if fmt == "csv":
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            fields = list(reader.fieldnames or [])
            if "username" not in fields or len(fields) != len(set(fields)):
                raise ValueError("Invalid CSV header")
            schema = detect_csv_schema(fields, fallback_schema=1)
            users = []
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("Invalid CSV row width")
                users.append(schema.user_from_csv_row(row))
            return ProviderUsers(schema_version=schema.version, users=users)
        data = (
            strict_json_value(text) if fmt == "json" else yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506
        )
        if not isinstance(data, dict) or not isinstance(data.get("users"), list):
            raise ValueError("An explicit users list is required")
        return users_from_mapping(data, fallback_schema=1)
    except (ValueError, TypeError, KeyError, csv.Error, yaml.YAMLError, ProviderError) as exc:
        raise ProviderError(
            f"Invalid {fmt.upper()} provider document.",
            suggestion=(
                "Provide a complete users list or CSV header and complete rows. "
                "Use an explicit empty users list or header-only CSV to remove all users."
            ),
        ) from exc
