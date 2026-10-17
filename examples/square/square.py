# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../../LICENSE.txt)
"""A square wave generator driven from the builder: choose the high and the low time in state machine cycles.

    python examples/square/square.py [--high N] [--low N] [--chip rp2040|rp2350] [--out DIR]

Each level is one ``set pins`` with a delay, so it lasts 1 + delay cycles (1 to 32). The script prints the program, runs it in the emulator and measures the high and low times
from the pin waveform. A longer level than 32 cycles would need a counting loop (``set x`` and ``jmp x--``); see examples/blink for that in the Amaranth front end.
"""
import argparse
import itertools
import os

from pioweave.builder import PINS, Program, write_pio
from pioweave.emulator import run_case


def square(high, low, chip="rp2040"):
    """Build the program.

    Parameters
    ----------
    high, low : int
        The time of each level in state machine cycles, 1 to 32.
    chip : str
        The chip name for pioweave.builder.Program.

    Returns
    -------
    pioweave.builder.Program
        The program; it wraps from the last instruction to the first.
    """
    for what, v in (("high", high), ("low", low)):
        if not 1 <= v <= 32:
            raise ValueError("%s must be 1 to 32 cycles, got %r" % (what, v))
    p = Program("square", chip=chip)
    with p.wrapped():
        p.set(PINS, 1, delay=high - 1)
        p.set(PINS, 0, delay=low - 1)
    return p


def measure(wave):
    """The (level, length) runs of a waveform, without the last (cut) run."""
    return [(level, len(list(g))) for level, g in itertools.groupby(wave)][:-1]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--high", type=int, default=5)
    ap.add_argument("--low", type=int, default=3)
    ap.add_argument("--chip", default="rp2040")
    ap.add_argument("--out", default="build/square", help="output directory (default build/square)")
    a = ap.parse_args(argv)
    p = square(a.high, a.low, a.chip)
    os.makedirs(a.out, exist_ok=True)
    write_pio([p], os.path.join(a.out, "square.pio"))
    print(p.pio_text())
    version = 1 if a.chip != "rp2040" else 0
    wave = run_case(p, 6 * (a.high + a.low), version=version, set_base=0, set_count=1, dirs=1).wave(0)
    runs = measure(wave)
    print("pin 0: %s" % wave[:60])
    print("measured: %s" % runs[:4])
    assert runs[0] == ("1", a.high) and runs[1] == ("0", a.low), runs
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
