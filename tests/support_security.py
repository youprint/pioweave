# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""Helpers of the security regression tests (tests/test_security_*.py): the command line in process, a Python subprocess with this tree on the path."""
import contextlib
import io
import os
import subprocess
import sys

from pioweave.__main__ import main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cli(*argv):
    """Run ``main(argv)`` in this process; returns (status, standard output, standard error)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def run_py(*args, cwd=None, env=None):
    """Run ``python ARGS`` with this tree first on PYTHONPATH; returns the CompletedProcess."""
    e = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
    e.update(env or {})
    return subprocess.run([sys.executable] + list(args), cwd=cwd, env=e, capture_output=True, text=True, timeout=300)
