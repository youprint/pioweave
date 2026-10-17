# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""The only place that touches Amaranth's internals, and the version gate (study: docs/design-notes/amaranth-ir-study.rst).

What the compiler needs from Amaranth, and where it comes from:
  Fragment                          `amaranth.hdl` (exported; its attributes `statements` and `subfragments` are not documented)
  Assign, Switch, Operator, Concat, Slice  the private module `amaranth.hdl._ast` (not exported by `amaranth.hdl`; Slice is read for `wait pin`: `.value`, `.start`, `.stop`)
  the names of the states of an FSM `fsm_state_names()` below: `Signal.decoder` (0.5.x) or the signal's format object (0.6 development versions)

Versions (python/scripts/matrix.sh, 2026-10-17): the whole test suite passes on 0.5.1, 0.5.10 and the 0.6 development version in TESTED_DEV (there is no 0.6 beta release yet: PyPI has
no pre-release and the newest tag is v0.6.0.dev0); the quick suite (refusals, builder, boards, the instruction words of every example) passes on every 0.5.x from 0.5.0 to 0.5.10. Those
are accepted; any other version is refused with a clear message unless PIOWEAVE_ALLOW_UNTESTED=1 is set.
"""
import os

import amaranth

TESTED = "0.5.0 to 0.5.10"
TESTED_DEV = ("0.6.0.dev140",)         # GitHub main at commit 90449f1e6d5abdfb8cac7519ac7b34d83a9f42b7, 2026-10-17


class UnsupportedAmaranth(ImportError):
    """The installed Amaranth is not a version this package was tested with.

    It is an `ImportError`, raised at import time by the version gate in `check_version`, unless ``PIOWEAVE_ALLOW_UNTESTED=1`` is set.
    """


def check_version(version=None, allow=None):
    """Check an Amaranth version against the versions this package was tested with (`TESTED`, `TESTED_DEV`).

    Parameters
    ----------
    version : str or None
        The version string; None means the installed Amaranth.
    allow : bool or None
        Do not raise for an untested version. None reads the environment variable ``PIOWEAVE_ALLOW_UNTESTED`` (``"1"`` allows).

    Returns
    -------
    bool
        True when the version is a tested one, False when it is untested but allowed.

    Raises
    ------
    UnsupportedAmaranth
        If the version is untested and not allowed.

    Examples
    --------
    >>> from pioweave._compat import check_version
    >>> check_version("0.5.3")
    True
    >>> check_version("0.4.0", allow=True)
    False
    >>> check_version("0.4.0", allow=False)  # doctest: +ELLIPSIS
    Traceback (most recent call last):
      ...
    pioweave._compat.UnsupportedAmaranth: pioweave was tested with Amaranth 0.5.0 to 0.5.10 and ...; found 0.4.0. ...
    """
    v = version if version is not None else amaranth.__version__
    allow = os.environ.get("PIOWEAVE_ALLOW_UNTESTED") == "1" if allow is None else allow
    parts = v.split(".")
    try:
        ok = v in TESTED_DEV or (parts[0] == "0" and parts[1] == "5" and len(parts) == 3 and parts[2].isdigit() and int(parts[2]) <= 10)
    except (IndexError, ValueError):
        ok = False
    if not ok and not allow:
        raise UnsupportedAmaranth("pioweave was tested with Amaranth %s and %s; found %s. Its compiler reads Amaranth's internals. "
                                  "Set PIOWEAVE_ALLOW_UNTESTED=1 to try anyway." % (TESTED, ", ".join(TESTED_DEV), v))
    return ok


check_version()

from amaranth.hdl import Fragment  # noqa: E402
from amaranth.hdl import _ast as ast  # noqa: E402


def fsm_state_names(sig):
    """The state names of an FSM.

    Parameters
    ----------
    sig : amaranth.hdl.Signal
        A signal; the state signal of an ``m.FSM()`` has a decoder (0.5.x) or an enum format (0.6 development) that names the states.

    Returns
    -------
    dict or None
        ``{state number: "NAME/number"}`` when `sig` is the state signal of an ``m.FSM()``, else None.
    """
    decoder = getattr(sig, "decoder", None)
    if callable(decoder):                                          # 0.5.x: the decoder function of the signal
        names = {}
        for n in range(1 << min(len(sig), 12)):
            try:
                text = str(decoder(n))
            except (KeyError, IndexError, ValueError):             # a number that is not a state
                continue
            if "/" in text:
                names[n] = text
        return names or None
    variants = getattr(getattr(sig, "_format", None), "_variants", None)      # 0.6 development: the enum format of the signal
    if isinstance(variants, dict) and variants and all("/" in str(v) for v in variants.values()):
        return {int(k): str(v) for k, v in variants.items()}
    return None


def clock_period(us=1):
    """The period for ``Simulator.add_clock``: a ``Period`` where Amaranth has one (RFC 66, 0.6 development), else seconds as a float (0.5.x).

    Parameters
    ----------
    us : int or float
        The period in microseconds.

    Returns
    -------
    float or amaranth.sim.Period
        The period in the form the installed Amaranth takes.

    Examples
    --------
    >>> from pioweave._compat import clock_period
    >>> clock_period(1)
    1e-06
    """
    try:
        from amaranth.sim import Period
    except ImportError:
        return us * 1e-6
    return Period(us=us)


__all__ = ["ast", "Fragment", "check_version", "fsm_state_names", "clock_period", "UnsupportedAmaranth", "TESTED", "TESTED_DEV"]
