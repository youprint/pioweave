# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Generator for bitweave's PIO flow from the Amaranth designs of pio_designs.py (tasks AM4 and AM5): compiles the design with the PIO platform and
prints `EMIT {"path": ".../NAME.pio"}`.

Usage: python bitweave_pio_amaranth.py <design> <outdir>        (see bitweave.toml, [pio.*_am])
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(argv):
    if len(argv) != 3:
        raise SystemExit(__doc__)
    import pio_designs
    from platform_rp import PLATFORMS
    name, out = argv[1], argv[2]
    if name not in pio_designs.PROGRAMS:
        raise SystemExit("unknown design %r; available: %s" % (name, ", ".join(sorted(pio_designs.PROGRAMS))))
    cls, chip, stim, cycles = pio_designs.PROGRAMS[name]
    design, platform = cls(), PLATFORMS[chip]()
    os.makedirs(out, exist_ok=True)
    platform.build(design, name, os.path.join(out, "project"), project="pio")
    print("EMIT " + json.dumps({"path": os.path.join(out, "project", name + ".pio")}))


if __name__ == "__main__":
    main(sys.argv)
