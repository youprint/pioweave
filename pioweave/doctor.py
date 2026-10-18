# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""`python -m pioweave doctor`: what this Python environment can do. Checks the Python and Amaranth versions against the version gate, the three packages, the
tools that are optional (pioasm, bitweave, cmake, arm-none-eabi-gcc, picotool, the Pico SDK), and compiles one example as a smoke test. Exit code 0 when the
essentials work (a missing optional tool is a note, not a failure, unless `--strict`)."""
import importlib
import importlib.metadata
import os
import shutil
import sys

from ._compat import TESTED, TESTED_DEV, check_version

__all__ = ["TOOLS", "report", "main"]

TOOLS = (
    ("pioasm", "PIOASM", "assembles .pio files (needed for MicroPython projects and for bitweave's pio flow); build it from the Pico SDK (tools/pioasm) or `bitweave guide pio`"),
    ("bitweave", "BITWEAVE", "checks a generated program in a PIO simulator, builds firmware, loads boards (github.com/youprint/bitweave)"),
    ("cmake", None, "builds the generated Pico SDK C projects"),
    ("arm-none-eabi-gcc", None, "compiles the generated Pico SDK C projects"),
    ("picotool", None, "loads a UF2 onto a board in BOOTSEL mode (bitweave runs it)"),
)


def _which(name, env):
    if env and os.environ.get(env) and os.path.exists(os.environ[env]):
        return os.environ[env]
    return shutil.which(name) or (os.path.join(os.path.dirname(os.path.abspath(sys.executable)), name) if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), name)) else None)


def report():
    """Check the Python environment.

    Returns
    -------
    list of tuple
        ``(status, name, detail)`` rows; `status` is ``'ok'``, ``'note'`` (an optional item is missing) or ``'fail'`` (an essential does not work).
    """
    rows = []
    v = sys.version_info
    rows.append(("ok" if v >= (3, 9) else "fail", "python", "%d.%d.%d at %s" % (v[0], v[1], v[2], sys.executable)))
    try:
        import amaranth
        ok = check_version(amaranth.__version__, allow=False)
        rows.append(("ok" if ok else "fail", "amaranth", "%s %s (tested: %s and %s)" % (amaranth.__version__, "accepted" if ok else "NOT tested with this pioweave", TESTED, ", ".join(TESTED_DEV))))
    except ImportError:
        rows.append(("fail", "amaranth", "not installed (pip install 'amaranth>=0.5.1,<0.6')"))
    try:
        rows.append(("ok", "pioweave", importlib.metadata.version("pioweave")))
    except importlib.metadata.PackageNotFoundError:
        rows.append(("note", "pioweave", "no installed distribution (running from a source tree?)"))
    for sub in ("builder", "boards"):
        try:
            importlib.import_module("pioweave." + sub)
            rows.append(("ok", "pioweave." + sub, "imports"))
        except ImportError as e:
            rows.append(("fail", "pioweave." + sub, "does not import: %s" % e))
    for name, env, what in TOOLS:
        p = _which(name, env)
        rows.append(("ok" if p else "note", name, p or "not found: " + what))
    sdk = os.environ.get("PICO_SDK_PATH")
    rows.append(("ok" if sdk and os.path.exists(os.path.join(sdk, "pico_sdk_init.cmake")) else "note", "Pico SDK", sdk or "PICO_SDK_PATH is not set (needed to build generated C projects)"))
    rows.append(_smoke())
    return rows


def _smoke():
    try:
        from .compile import compile_design
        from .designs import SquareWave
        from .platform import RP2040PioPlatform
        words = compile_design(SquareWave(), RP2040PioPlatform(), "square").program.words()
        if words == [0xE101, 0xE100]:
            return ("ok", "smoke test", "an Amaranth FSM compiled to set pins,1 [1] / set pins,0 [1] (words E101 E100)")
        return ("fail", "smoke test", "the example compiled to %s, expected [0xE101, 0xE100]" % [hex(w) for w in words])
    except Exception as e:          # noqa: BLE001 - any failure is the answer
        return ("fail", "smoke test", "%s: %s" % (type(e).__name__, e))


def main(strict=False, out=None):
    """Print the report of `report` and a verdict.

    Parameters
    ----------
    strict : bool
        Count a missing optional item as a failure.
    out : file-like or None
        Where to write; None means ``sys.stdout``.

    Returns
    -------
    int
        The exit code: 0 when the essentials work, else 1.
    """
    out = out or sys.stdout
    rows = report()
    for status, name, detail in rows:
        out.write("[%-4s] %-18s %s\n" % (status, name, detail))
    failed = [r for r in rows if r[0] == "fail"]
    missing = [r for r in rows if r[0] == "note"]
    out.write("\n%s\n" % ("pioweave works here." if not failed else "pioweave does not work here: %d check(s) failed." % len(failed)))
    if missing:
        out.write("%d optional item(s) missing; they are needed only for the steps named above.\n" % len(missing))
    return 1 if failed or (strict and missing) else 0
