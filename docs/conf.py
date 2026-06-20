from __future__ import annotations

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

myst_heading_anchors = 3
myst_enable_extensions = ["colon_fence"]
