from __future__ import annotations

import argparse
import os
import re
import tomllib
from pathlib import Path


def chart_value(chart_text: str, name: str) -> str:
    """Return a top-level Chart.yaml scalar value."""
    match = re.search(rf"^{name}:\s*\"?([^\"\n]+)\"?\s*$", chart_text, re.MULTILINE)
    if not match:
        raise SystemExit(f"Missing {name} in Chart.yaml.")
    return match.group(1)


def values_image_tag(values_text: str) -> str:
    """Return the top-level image.tag value from values.yaml."""
    image_match = re.search(r"(?ms)^image:\n(?P<body>(?:^[ \t]+[^\n]*\n?)*)", values_text)
    if not image_match:
        raise SystemExit("Missing image block in values.yaml.")
    tag_match = re.search(
        r"(?m)^[ \t]+tag:\s*(?:\"([^\"]*)\"|'([^']*)'|([^#\n]*))",
        image_match.group("body"),
    )
    if not tag_match:
        raise SystemExit("Missing image.tag in values.yaml.")
    value = next(group for group in tag_match.groups() if group is not None)
    return value.strip()


def pyproject_version(path: Path) -> str:
    """Read the Python package version from pyproject.toml."""
    return tomllib.loads(path.read_text(encoding="utf-8"))["project"]["version"]


def verify(*, version: str, chart_path: Path, values_path: Path) -> None:
    """Verify Helm release metadata matches the package release contract."""
    chart = chart_path.read_text(encoding="utf-8")
    values = values_path.read_text(encoding="utf-8")

    if chart_value(chart, "version") != version:
        raise SystemExit("Chart.yaml version must match pyproject.toml version.")
    if chart_value(chart, "appVersion") != version:
        raise SystemExit("Chart.yaml appVersion must match pyproject.toml version.")
    if values_image_tag(values) != "":
        raise SystemExit(
            "values.yaml image.tag must stay empty so the chart defaults to appVersion."
        )


def main() -> None:
    """CLI entrypoint for release workflow metadata checks."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=os.environ.get("VERSION"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--chart", type=Path, default=Path("charts/sftpwarden/Chart.yaml"))
    parser.add_argument("--values", type=Path, default=Path("charts/sftpwarden/values.yaml"))
    args = parser.parse_args()

    version = args.version or pyproject_version(args.pyproject)
    verify(version=version, chart_path=args.chart, values_path=args.values)


if __name__ == "__main__":
    main()
