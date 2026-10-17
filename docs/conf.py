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
    "sphinx.ext.autodoc",
    "sphinx.ext.doctest",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.todo",
    "sphinx_rtd_theme",
]
source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
root_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}
todo_include_todos = True

napoleon_google_docstring = False
napoleon_numpy_docstring = True
napoleon_use_ivar = True
napoleon_include_init_with_doc = True

autodoc_member_order = "bysource"
autodoc_default_options = {"members": True}
autodoc_preserve_defaults = True

myst_heading_anchors = 3

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
