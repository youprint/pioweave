# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""Command line of pioweave, and the generator command for bitweave's PIO flow.

    python -m pioweave list
    python -m pioweave doctor [--strict]
    python -m pioweave new NAME [--template TEMPLATE] [--dir PARENT]      (new --list shows the templates)
    python -m pioweave --version
    python -m pioweave build DESIGN OUTDIR [--chip CHIP] [--project pio|pico-c|micropython] [--clock-hz N] [--cycles N] [--stim GPIO=WAVE ...] [--name NAME]

DESIGN is an example name (see `list`) or `module:Class` of an Amaranth Elaboratable. The project is written to OUTDIR/project and `EMIT {"path": ".../NAME.pio"}`
is printed, which is what a bitweave `generator = [...]` expects.
"""
import argparse
import importlib
import json
import os
import sys

from . import block_cli, designs
from ._validate import DEFAULT_MAX_CYCLES, UsageError, check_clock_hz, check_cycles, check_name, parse_stim, parse_tx, parse_tx_feed
from .compile import PioCompileError
from .emulator.core import EmulationError
from .project import PioProjectError, SimOptions
from .scaffold import ScaffoldError
from .platform import PLATFORMS

__all__ = ["main"]


def _resolve(a):
    if a.design in designs.PROGRAMS:
        cls, chip, stim, cycles, opts = designs.entry(a.design)
        name = a.name or a.design
        chip = a.chip or chip
    else:
        if ":" not in a.design:
            raise UsageError("unknown design %r; available: %s, or give module:Class" % (a.design, ", ".join(sorted(designs.PROGRAMS))))
        mod, _, attr = a.design.partition(":")
        if not mod or not attr:
            raise UsageError("a design is NAME or module:Class, not %r" % a.design)
        chip, stim, cycles, opts = a.chip, {}, 64, {}
        if chip not in PLATFORMS:            # before the module is imported: importing runs its code
            raise UsageError("give --chip (%s)" % ", ".join(sorted(PLATFORMS)))
        name = a.name or attr.lower()
        sys.path.insert(0, os.getcwd())
        try:
            cls = getattr(importlib.import_module(mod), attr)
        except ImportError as e:
            raise UsageError("cannot import %r: %s" % (mod, e)) from None
        except AttributeError:
            raise UsageError("module %r has no attribute %r" % (mod, attr)) from None
    if chip not in PLATFORMS:
        raise UsageError("give --chip (%s)" % ", ".join(sorted(PLATFORMS)))
    check_name(name, UsageError)
    stim = dict(stim)
    for item in a.stim or []:
        g, w = parse_stim(item)
        stim[g] = w
    opts = dict(opts)
    if a.tx:
        opts["tx"] = parse_tx(a.tx)
    if a.tx_feed:
        opts["tx_feed"] = [parse_tx_feed(item) for item in a.tx_feed]
    if a.no_drain:
        opts["drain"] = False
    max_cycles = getattr(a, "max_cycles", None) or DEFAULT_MAX_CYCLES
    cycles = check_cycles(a.cycles if a.cycles is not None else cycles, UsageError, max_cycles, "--cycles" if a.cycles is not None else "cycles")
    return cls, chip, stim, cycles, name, opts


def _make(cls):
    """Make the design: ``cls()``. A design is code the user wrote; what it raises becomes a message."""
    try:
        return cls()
    except SystemExit as e:
        raise UsageError("the design %r exited instead of returning a design (%s)" % (getattr(cls, "__name__", cls), e.code)) from None


def _emulate(a):
    from .compile import compile_design
    from .emulator import run_compiled, to_vcd
    cls, chip, stim, cycles, name, opts = _resolve(argparse.Namespace(design=a.design, name=None, chip=a.chip, stim=a.stim, cycles=a.cycles, max_cycles=a.max_cycles, tx=a.tx,
                                                                      tx_feed=a.tx_feed, no_drain=a.no_drain))
    if a.vcd and cycles > VCD_MAX_CYCLES:
        raise UsageError("--vcd records every cycle (about 3 kB each): %d cycles is over the limit of %d\n  hint: use --cycles %d or fewer" % (cycles, VCD_MAX_CYCLES, VCD_MAX_CYCLES))
    compiled = compile_design(_make(cls), PLATFORMS[chip](), name)
    res = run_compiled(compiled, cycles, stim, opts.get("tx", ()), opts.get("tx_feed", ()), opts.get("drain", True), record=bool(a.vcd))
    print(compiled.program.pio_text())
    for g in compiled.output_gpios:
        print("GPIO %-3d %s" % (g, res.wave(compiled.pin(g))))
        if g in compiled.dynamic_gpios:       # a pin of a group with a direction register: the pad above, the direction here
            print("GPIO %-3d %s   (direction, 1 = output)" % (g, "".join(str((d >> compiled.pin(g)) & 1) for d in res.dirs)))
    for g in sorted(set(stim) - set(compiled.dynamic_gpios)):
        print("GPIO %-3d %s   (input)" % (g, "".join(stim[g][min(t, len(stim[g]) - 1)] for t in range(cycles))))
    if res.rx or res.rx_left:
        print("received: %s" % ", ".join("0x%08x@%d" % (w, c) for c, w in res.rx))
        if res.rx_left:
            print("left in the RX FIFO: %s" % ", ".join("0x%08x" % w for w in res.rx_left))
    if a.vcd:
        with open(a.vcd, "w") as fh:
            fh.write(to_vcd(res.trace, [compiled.pin(g) for g in compiled.output_gpios]))
        print("wrote %s" % a.vcd)
    return 0


# ----------------------------------------------------------------------------- `new`: kept apart from the other commands

def _add_new_parser(sub):
    n = sub.add_parser("new", help="create a project directory from a template (see `new --list`)")
    n.add_argument("name", nargs="?", help="project name (letters, digits, - and _)")
    n.add_argument("--template", default="blink", help="template name (default blink)")
    n.add_argument("--dir", default=".", help="the directory in which to create the project (default: the current directory)")
    n.add_argument("--list", action="store_true", help="list the templates")


def _new(a):
    from . import scaffold
    if a.list:
        print("\n".join("%-15s %s" % (k, scaffold.TEMPLATES[k][0]) for k in scaffold.template_names()))
        return 0
    if not a.name:
        raise UsageError("give a project name, for example: python -m pioweave new my-blink   (python -m pioweave new --list shows the templates)")
    files = scaffold.scaffold(a.name, a.template, a.dir)
    print("created %s with %s" % (os.path.dirname(files[0]), ", ".join(os.path.basename(f) for f in files)))
    print("next: cd %s && python -m unittest" % os.path.dirname(files[0]))
    return 0


# What is a mistake of the user (or of the design they wrote) and not a bug of pioweave: printed as `error: ...`, exit status 2. PIOWEAVE_TRACEBACK=1 shows the traceback instead.
VCD_MAX_CYCLES = 100_000       # the trace of a run takes about 2.8 kB per cycle (measured: 2.8 GB for 1,000,000 cycles)

_USER_ERRORS = (PioProjectError, PioCompileError, EmulationError, ScaffoldError, UsageError, ValueError, KeyError, OSError, ImportError, AttributeError, TypeError, RecursionError, OverflowError, EOFError)


def _resource_errors():
    """Amaranth's `ResourceError` (a design asks the platform for a pin the chip does not have) as a tuple; empty when Amaranth is not installed."""
    try:
        from amaranth.build.res import ResourceError
    except ImportError:
        return ()
    return (ResourceError,)


def _version():
    from . import __version__
    return "pioweave %s" % __version__


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
    ap = argparse.ArgumentParser(prog="python -m pioweave", description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version=_version())
    sub = ap.add_subparsers(dest="cmd", required=True)
    _add_new_parser(sub)
    sub.add_parser("list", help="list the example designs")
    em = sub.add_parser("emulate", help="run a design in the PIO emulator and print the pin waveforms and the words received")
    em.add_argument("design")
    em.add_argument("--chip")
    em.add_argument("--cycles", type=int, default=None)
    em.add_argument("--max-cycles", type=int, default=None, help="raise the ceiling of --cycles (default %d)" % DEFAULT_MAX_CYCLES)
    em.add_argument("--stim", action="append", help="input waveform GPIO=0101...")
    em.add_argument("--tx", help="words in the TX FIFO at cycle 0, comma separated (decimal or 0x...)")
    em.add_argument("--tx-feed", action="append", help="CYCLE=WORD")
    em.add_argument("--no-drain", action="store_true")
    em.add_argument("--vcd", help="also write a VCD of the output pins to this file")
    d = sub.add_parser("doctor", help="check this environment: Amaranth version, packages, optional tools, a smoke test")
    d.add_argument("--strict", action="store_true", help="a missing optional tool is a failure too")
    b = sub.add_parser("build", help="compile a design and write a project")
    b.add_argument("design")
    b.add_argument("outdir")
    b.add_argument("--chip")
    b.add_argument("--project", default="pio", choices=["pio", "pico-c", "micropython"])
    b.add_argument("--clock-hz", type=int, default=None, help="state machine clock the C / MicroPython project asks for")
    b.add_argument("--cycles", type=int, default=None, help="length of the reference simulation")
    b.add_argument("--max-cycles", type=int, default=None, help="raise the ceiling of --cycles (default %d)" % DEFAULT_MAX_CYCLES)
    b.add_argument("--stim", action="append", help="input waveform GPIO=0101...")
    b.add_argument("--tx", help="words in the TX FIFO at cycle 0, comma separated (decimal or 0x...)")
    b.add_argument("--tx-feed", action="append", help="CYCLE=WORD: a word the host writes into the TX FIFO so that the instruction of that cycle sees it")
    b.add_argument("--no-drain", action="store_true", help="the host does not empty the RX FIFO (it can fill up)")
    b.add_argument("--name")
    a = ap.parse_args(argv)
    try:
        return _dispatch(a)
    except Exception as e:
        if not isinstance(e, _USER_ERRORS + _resource_errors()) or os.environ.get("PIOWEAVE_TRACEBACK") == "1":
            raise
        text = e.args[0] if isinstance(e, KeyError) and e.args else e
        print("error: %s" % (text,), file=sys.stderr)
        return 2


def _dispatch(a):
    if a.cmd == "new":
        return _new(a)
    if a.cmd in ("emulate", "build") and block_cli.is_block(a.design):        # an example block (several state machines): pioweave/block_cli.py
        return getattr(block_cli, a.cmd)(a)
    if a.cmd == "emulate":
        return _emulate(a)
    if a.cmd == "doctor":
        from .doctor import main as doctor
        return doctor(strict=a.strict)
    if a.cmd == "list":
        print("\n".join(["%s (%s)" % (k, v[1]) for k, v in sorted(designs.PROGRAMS.items())] + block_cli.list_blocks()))
        return 0
    cls, chip, stim, cycles, name, opts = _resolve(a)
    check_clock_hz(a.clock_hz, UsageError)
    factory = lambda: (_make(cls), PLATFORMS[chip](sm_clock_hz=a.clock_hz))  # noqa: E731
    sim = SimOptions(factory=factory, cycles=cycles, stim=stim, max_cycles=max(a.max_cycles or 0, DEFAULT_MAX_CYCLES), **opts)    # checks the values before a design is made
    design, platform = factory()
    out = os.path.join(a.outdir, "project")
    platform.build(design, name, out, project=a.project, sim=sim)
    print("EMIT " + json.dumps({"path": os.path.join(out, name + ".pio")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
