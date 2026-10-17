# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""The only place that touches Amaranth's private intermediate representation (amaranth.hdl._ast, amaranth.hdl._ir), and the version gate.

The compiler reads `Fragment.statements`, which Amaranth does not promise to keep. The package was written against and tested with Amaranth 0.5.10; versions
0.5.10 and later 0.5.x are accepted, anything else is refused with a clear message unless AMARANTH_RP_ALLOW_UNTESTED=1 is set.
"""
import os

import amaranth

TESTED = "0.5.10"


class UnsupportedAmaranth(ImportError):
    """The installed Amaranth is not a version this package was tested with."""


def check_version(version=None, allow=None):
    v = version if version is not None else amaranth.__version__
    allow = os.environ.get("AMARANTH_RP_ALLOW_UNTESTED") == "1" if allow is None else allow
    parts = v.split(".")
    try:
        ok = parts[0] == "0" and parts[1] == "5" and int("".join(c for c in parts[2] if c.isdigit()) or "0") >= 10
    except (IndexError, ValueError):
        ok = False
    if not ok and not allow:
        raise UnsupportedAmaranth("amaranth-rp was tested with Amaranth %s (0.5.x from 0.5.10); found %s. Its compiler reads Amaranth's private IR. "
                                  "Set AMARANTH_RP_ALLOW_UNTESTED=1 to try anyway." % (TESTED, v))
    return ok


check_version()

from amaranth.hdl import _ast as ast  # noqa: E402
from amaranth.hdl._ir import Fragment  # noqa: E402

__all__ = ["ast", "Fragment", "check_version", "UnsupportedAmaranth", "TESTED"]
