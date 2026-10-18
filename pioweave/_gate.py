# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""The Amaranth version gate, without importing Amaranth (so `import pioweave` can run it, and `import pioweave.builder` still does not load Amaranth).

`pioweave/__init__.py` calls `check_installed` when the package is imported: it reads the version of the installed ``amaranth`` distribution from its metadata
(`importlib.metadata`), which does not import it. `pioweave/_compat.py`, imported where Amaranth is first needed (the compiler, the projects, the blocks), calls `check_version`
again with ``amaranth.__version__``, so a version that differs from its metadata (a patched module, a development checkout) is caught there too.
"""
import importlib.metadata
import os
import warnings

__all__ = ["UnsupportedAmaranth", "UntestedAmaranthWarning", "TESTED", "TESTED_DEV", "check_version", "check_installed"]

TESTED = "0.5.1 to 0.5.10"
TESTED_DEV = ("0.6.0.dev140",)         # GitHub main at commit 90449f1e6d5abdfb8cac7519ac7b34d83a9f42b7, 2026-10-17


class UnsupportedAmaranth(ImportError):
    """The installed Amaranth is not a version this package was tested with.

    It is an `ImportError`, raised by the version gate in `check_version` when ``pioweave`` is imported (from the metadata of the installed Amaranth) and again when the compiler
    module is imported (from ``amaranth.__version__``), unless ``PIOWEAVE_ALLOW_UNTESTED=1`` is set.
    """


class UntestedAmaranthWarning(UserWarning):
    """``PIOWEAVE_ALLOW_UNTESTED=1`` let an Amaranth version through that this package was not tested with."""


def check_version(version, allow=None):
    """Check an Amaranth version against the versions this package was tested with (`TESTED`, `TESTED_DEV`).

    Parameters
    ----------
    version : str
        The version string.
    allow : bool or None
        Do not raise for an untested version (a `UntestedAmaranthWarning` is issued instead). None reads the environment variable ``PIOWEAVE_ALLOW_UNTESTED`` (``"1"`` allows).

    Returns
    -------
    bool
        True when the version is a tested one, False when it is untested but allowed.

    Raises
    ------
    UnsupportedAmaranth
        If the version is untested and not allowed.
    """
    v = version
    allow = os.environ.get("PIOWEAVE_ALLOW_UNTESTED") == "1" if allow is None else allow
    parts = v.split(".") if isinstance(v, str) else []
    try:
        ok = v in TESTED_DEV or (parts[0] == "0" and parts[1] == "5" and len(parts) == 3 and parts[2].isascii() and parts[2].isdigit() and 1 <= int(parts[2]) <= 10)
    except IndexError:
        ok = False
    if not ok and not allow:
        raise UnsupportedAmaranth("pioweave was tested with Amaranth %s and %s; found %s. Its compiler reads Amaranth's internals. "
                                  "Set PIOWEAVE_ALLOW_UNTESTED=1 to try anyway.\n  hint: install a tested version with pip install 'amaranth>=0.5.1,<0.6'; "
                                  "scripts/matrix.sh shows what the tests say about other versions." % (TESTED, ", ".join(TESTED_DEV), v))
    if not ok:
        warnings.warn("pioweave was not tested with Amaranth %s; PIOWEAVE_ALLOW_UNTESTED=1 lets it through, results are on your own responsibility" % (v,), UntestedAmaranthWarning,
                      stacklevel=3)
    return ok


def check_installed():
    """Run the gate on the installed ``amaranth`` distribution, without importing it.

    Returns
    -------
    bool or None
        What `check_version` returns, or None when Amaranth is not installed as a distribution (nothing to check; the compiler's own import then fails by itself).

    Raises
    ------
    UnsupportedAmaranth
        As `check_version`.
    """
    try:
        version = importlib.metadata.version("amaranth")
    except importlib.metadata.PackageNotFoundError:
        return None
    return check_version(version)
