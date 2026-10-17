# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Generator for bitweave's PIO flow writes a program of pio_examples.py as a .pio file and prints `EMIT {json}`.

Usage: python bitweave_pio_emit.py <program> <outdir> [chip]     (see bitweave.toml, [pio.*_dsl])
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(argv):
    if len(argv) not in (3, 4):
        raise SystemExit(__doc__)
    import pio_examples
    name, out = argv[1], argv[2]
    if name not in pio_examples.PROGRAMS:
        raise SystemExit("unknown program %r; available: %s" % (name, ", ".join(sorted(pio_examples.PROGRAMS))))
    prog = pio_examples.PROGRAMS[name](*argv[3:])
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, name + ".pio")
    with open(path, "w") as fh:
        fh.write(prog.pio_text())
    print("EMIT " + json.dumps({"path": path}))


if __name__ == "__main__":
    main(sys.argv)
