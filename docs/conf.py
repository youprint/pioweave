import os
import sys
import time

sys.path.insert(0, os.path.abspath(".."))

import pioweave

project = "pioweave"
version = pioweave.__version__
release = version
copyright = time.strftime("2026—%Y, the project owner")

extensions = [
    "myst_parser",
    "sphinx.ext.intersphinx",
    "sphinx.ext.todo",
    "sphinx_rtd_theme",
]
source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}
todo_include_todos = True

myst_heading_anchors = 3

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
