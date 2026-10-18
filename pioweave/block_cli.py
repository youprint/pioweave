# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""The command line for the example blocks (`pioweave.block_designs.BLOCKS`): ``python -m pioweave list / build / emulate`` call these when the design name is a block.

Kept apart from ``__main__`` so that the single-machine commands stay as they were.
"""
import json
import os

from . import block_designs
from ._validate import UsageError, check_clock_hz, check_cycles, parse_stim
from .block import Block, emulate_block
from .platform import PLATFORMS

__all__ = ["is_block", "list_blocks", "build", "emulate"]


def is_block(name):
    """True when `name` is an example block.

    Parameters
    ----------
    name : str
        A design name given on the command line.

    Returns
    -------
    bool
        Whether ``BLOCKS`` has it.
    """
    return name in block_designs.BLOCKS


def list_blocks():
    """The ``list`` lines of the example blocks.

    Returns
    -------
    list of str
        One line per block.
    """
    out = []
    for name in sorted(block_designs.BLOCKS):
        block, sim = block_designs.entry(name)
        out.append("%s (block of %d state machines: %s)" % (name, len(block.machines), ", ".join(m.name for m in block.machines)))
    return out


def _entry(a):
    for what in ("tx", "tx_feed", "no_drain"):
        if getattr(a, what, None):
            raise UsageError("--%s is not available for a block: the words per machine are part of the example (pioweave.block.BlockSim)" % what.replace("_", "-"))
    if a.chip and a.chip not in PLATFORMS:
        raise UsageError("unknown chip %r (%s)" % (a.chip, ", ".join(sorted(PLATFORMS))))
    block, sim = block_designs.entry(a.design, a.chip)
    clock_hz = getattr(a, "clock_hz", None)
    if clock_hz is not None:
        block = Block(block.machines, block.platform, sm_clock_hz=check_clock_hz(clock_hz, UsageError), name=block.name)
    sim.max_cycles = getattr(a, "max_cycles", None) or sim.max_cycles
    if a.cycles is not None:
        sim.cycles = check_cycles(a.cycles, UsageError, sim.max_cycles, "--cycles")
    for item in a.stim or []:
        g, w = parse_stim(item)
        sim.stim[g] = w
    return block, sim


def build(a):
    """``python -m pioweave build BLOCK OUTDIR``: write the project of an example block and print the ``EMIT`` line.

    Parameters
    ----------
    a : argparse.Namespace
        The parsed arguments of the ``build`` command.

    Returns
    -------
    int
        The exit code.
    """
    block, sim = _entry(a)
    name = a.name or a.design
    out = os.path.join(a.outdir, "project")
    block.build(name, out, project=a.project, sim=sim)
    print("EMIT " + json.dumps({"path": os.path.join(out, name + ".pio")}))
    return 0


def emulate(a):
    """``python -m pioweave emulate BLOCK``: run the programs together in the emulator and print the pin waveforms and the words received.

    Parameters
    ----------
    a : argparse.Namespace
        The parsed arguments of the ``emulate`` command.

    Returns
    -------
    int
        The exit code.
    """
    block, sim = _entry(a)
    bc = block.compile(a.design)
    run = emulate_block(bc, sim)
    print(bc.program_text())
    print(bc.describe())
    for g in sorted(run["pads"]):
        print("GPIO %-3d %s" % (g, run["pads"][g]))
    for g in sorted(sim.stim):
        print("GPIO %-3d %s   (input)" % (g, "".join(sim.stim[g][min(t, len(sim.stim[g]) - 1)] for t in range(sim.cycles))))
    print("IRQ flags after each cycle: %s" % " ".join("%02x" % f for f in run["irq"]))
    for n in bc.names:
        if run["rx"][n] or run["rx_left"][n]:
            print("%s received: %s" % (n, ", ".join("0x%08x@%d" % (w, c) for c, w in run["rx"][n])))
    return 0
