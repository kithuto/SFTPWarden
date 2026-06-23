from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

project = "SFTPWarden"
author = "Ignasi Rovira"
copyright = "2026, Ignasi Rovira"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

master_doc = "index"
html_theme = "sphinx_rtd_theme"
html_title = "SFTPWarden"
html_static_path = ["_static"]
templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
html_theme_options = {
    "collapse_navigation": False,
    "navigation_depth": 2,
    "sticky_navigation": True,
    "titles_only": False,
}
html_css_files = [
    "custom.css",
]
html_js_files = [
    "sidebar_scroll.js",
    "search_modal.js",
    "sidebar_sections.js",
]

myst_heading_anchors = 3
myst_enable_extensions = ["colon_fence"]


def copy_readme_static_assets(app, exception) -> None:
    """Copy README-relative assets into the published documentation tree."""
    if exception is not None:
        return

    source_dir = Path(app.srcdir) / "_static"
    target_dir = Path(app.outdir) / "docs" / "_static"
    target_dir.mkdir(parents=True, exist_ok=True)

    source = source_dir / "logo-sftpwarden.png"
    if source.exists():
        shutil.copy2(source, target_dir / "logo-sftpwarden.png")


def setup(app) -> None:
    app.connect("build-finished", copy_readme_static_assets)
