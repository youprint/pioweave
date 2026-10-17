# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Command line of pioweave.builder, and the generator command for bitweave's PIO flow.

    python -m pioweave.builder list
    python -m pioweave.builder emit PROGRAM OUTDIR [CHIP]     writes OUTDIR/PROGRAM.pio and prints `EMIT {"path": ...}` (bitweave generator protocol)
"""
import argparse
import json
import os
import sys

from . import examples

__all__ = ["main"]


def main(argv=None):
    """Run the command line (see the module documentation).

    Parameters
    ----------
    argv : list of str or None
        The arguments; None means ``sys.argv[1:]``.

    Returns
    -------
    int
        The exit code.
    """
    ap = argparse.ArgumentParser(prog="python -m pioweave.builder", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list the example programs")
    e = sub.add_parser("emit", help="write an example program as a .pio file and print EMIT {json}")
    e.add_argument("program")
    e.add_argument("outdir")
    e.add_argument("chip", nargs="?", help="rp2040, rp2350, rp2354, rp2350a, rp2350b, rp2354a or rp2354b (default: the example's own)")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        print("\n".join(sorted(examples.PROGRAMS)))
        return 0
    if a.program not in examples.PROGRAMS:
        raise SystemExit("unknown program %r; available: %s" % (a.program, ", ".join(sorted(examples.PROGRAMS))))
    prog = examples.PROGRAMS[a.program](*([a.chip] if a.chip else []))
    os.makedirs(a.outdir, exist_ok=True)
    path = os.path.join(a.outdir, a.program + ".pio")
    with open(path, "w") as fh:
        fh.write(prog.pio_text())
    print("EMIT " + json.dumps({"path": path}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
