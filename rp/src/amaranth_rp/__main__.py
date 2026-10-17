# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Command line of amaranth-rp, and the generator command for bitweave's PIO flow.

    python -m amaranth_rp list
    python -m amaranth_rp build DESIGN OUTDIR [--chip CHIP] [--project pio|pico-c|micropython] [--clock-hz N] [--cycles N] [--stim GPIO=WAVE ...] [--name NAME]

DESIGN is an example name (see `list`) or `module:Class` of an Amaranth Elaboratable. The project is written to OUTDIR/project and `EMIT {"path": ".../NAME.pio"}`
is printed, which is what a bitweave `generator = [...]` expects.
"""
import argparse
import importlib
import json
import os
import sys

from . import designs
from .platform import PLATFORMS


def _resolve(a):
    if a.design in designs.PROGRAMS:
        cls, chip, stim, cycles = designs.PROGRAMS[a.design]
        name = a.name or a.design
    else:
        if ":" not in a.design:
            raise SystemExit("unknown design %r; available: %s, or give module:Class" % (a.design, ", ".join(sorted(designs.PROGRAMS))))
        mod, _, attr = a.design.partition(":")
        sys.path.insert(0, os.getcwd())
        cls = getattr(importlib.import_module(mod), attr)
        chip, stim, cycles = None, {}, 64
        name = a.name or attr.lower()
    chip = a.chip or chip
    if chip not in PLATFORMS:
        raise SystemExit("give --chip (%s)" % ", ".join(sorted(PLATFORMS)))
    for item in a.stim or []:
        g, _, w = item.partition("=")
        stim[int(g)] = w
    return cls, chip, stim, a.cycles or cycles, name


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m amaranth_rp", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list the example designs")
    b = sub.add_parser("build", help="compile a design and write a project")
    b.add_argument("design")
    b.add_argument("outdir")
    b.add_argument("--chip")
    b.add_argument("--project", default="pio", choices=["pio", "pico-c", "micropython"])
    b.add_argument("--clock-hz", type=int, default=None, help="state machine clock the C / MicroPython project asks for")
    b.add_argument("--cycles", type=int, default=None, help="length of the reference simulation")
    b.add_argument("--stim", action="append", help="input waveform GPIO=0101...")
    b.add_argument("--name")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        print("\n".join("%s (%s)" % (k, v[1]) for k, v in sorted(designs.PROGRAMS.items())))
        return 0
    cls, chip, stim, cycles, name = _resolve(a)
    factory = lambda: (cls(), PLATFORMS[chip](sm_clock_hz=a.clock_hz))  # noqa: E731
    design, platform = factory()
    out = os.path.join(a.outdir, "project")
    platform.build(design, name, out, project=a.project, factory=factory, cycles=cycles, stim=stim)
    print("EMIT " + json.dumps({"path": os.path.join(out, name + ".pio")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
