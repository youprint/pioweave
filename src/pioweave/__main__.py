# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Command line of pioweave, and the generator command for bitweave's PIO flow.

    python -m pioweave list
    python -m pioweave doctor [--strict]
    python -m pioweave build DESIGN OUTDIR [--chip CHIP] [--project pio|pico-c|micropython] [--clock-hz N] [--cycles N] [--stim GPIO=WAVE ...] [--name NAME]

DESIGN is an example name (see `list`) or `module:Class` of an Amaranth Elaboratable. The project is written to OUTDIR/project and `EMIT {"path": ".../NAME.pio"}`
is printed, which is what a bitweave `generator = [...]` expects.
"""
import argparse
import importlib
import json
import os
import sys

from . import designs
from .project import SimOptions
from .platform import PLATFORMS


def _resolve(a):
    if a.design in designs.PROGRAMS:
        cls, chip, stim, cycles, opts = designs.entry(a.design)
        name = a.name or a.design
    else:
        if ":" not in a.design:
            raise SystemExit("unknown design %r; available: %s, or give module:Class" % (a.design, ", ".join(sorted(designs.PROGRAMS))))
        mod, _, attr = a.design.partition(":")
        sys.path.insert(0, os.getcwd())
        cls = getattr(importlib.import_module(mod), attr)
        chip, stim, cycles, opts = None, {}, 64, {}
        name = a.name or attr.lower()
    chip = a.chip or chip
    if chip not in PLATFORMS:
        raise SystemExit("give --chip (%s)" % ", ".join(sorted(PLATFORMS)))
    stim = dict(stim)
    for item in a.stim or []:
        g, _, w = item.partition("=")
        stim[int(g)] = w
    opts = dict(opts)
    if a.tx:
        opts["tx"] = [int(w, 0) for w in a.tx.split(",")]
    if a.tx_feed:
        opts["tx_feed"] = [(int(c), int(w, 0)) for c, _, w in (item.partition("=") for item in a.tx_feed)]
    if a.no_drain:
        opts["drain"] = False
    return cls, chip, stim, a.cycles or cycles, name, opts


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pioweave", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list the example designs")
    d = sub.add_parser("doctor", help="check this environment: Amaranth version, packages, optional tools, a smoke test")
    d.add_argument("--strict", action="store_true", help="a missing optional tool is a failure too")
    b = sub.add_parser("build", help="compile a design and write a project")
    b.add_argument("design")
    b.add_argument("outdir")
    b.add_argument("--chip")
    b.add_argument("--project", default="pio", choices=["pio", "pico-c", "micropython"])
    b.add_argument("--clock-hz", type=int, default=None, help="state machine clock the C / MicroPython project asks for")
    b.add_argument("--cycles", type=int, default=None, help="length of the reference simulation")
    b.add_argument("--stim", action="append", help="input waveform GPIO=0101...")
    b.add_argument("--tx", help="words in the TX FIFO at cycle 0, comma separated (decimal or 0x...)")
    b.add_argument("--tx-feed", action="append", help="CYCLE=WORD: a word the host writes into the TX FIFO so that the instruction of that cycle sees it")
    b.add_argument("--no-drain", action="store_true", help="the host does not empty the RX FIFO (it can fill up)")
    b.add_argument("--name")
    a = ap.parse_args(argv)
    if a.cmd == "doctor":
        from .doctor import main as doctor
        return doctor(strict=a.strict)
    if a.cmd == "list":
        print("\n".join("%s (%s)" % (k, v[1]) for k, v in sorted(designs.PROGRAMS.items())))
        return 0
    cls, chip, stim, cycles, name, opts = _resolve(a)
    factory = lambda: (cls(), PLATFORMS[chip](sm_clock_hz=a.clock_hz))  # noqa: E731
    design, platform = factory()
    out = os.path.join(a.outdir, "project")
    platform.build(design, name, out, project=a.project, sim=SimOptions(factory=factory, cycles=cycles, stim=stim, **opts))
    print("EMIT " + json.dumps({"path": os.path.join(out, name + ".pio")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
