# Copyright (c) 2026 youprint. MIT licence (see ../../LICENSE.txt)
"""A WS2812-style serial LED waveform, written with the Python builder (pioweave.builder).

    python examples/ws2812/ws2812.py [--out DIR]

Why the builder and not the Amaranth front end: the usual program drives the data line with side-set on every instruction, and its ``jmp !x`` has a different side-set value on
the two branches (a one is high for longer than a zero). The Amaranth front end refuses that on purpose ("the side-set value differs between the two branches of the test"), because
one PIO instruction applies one value. With the builder the instruction is written directly.

Each bit takes T1 + T2 + T3 = 2 + 5 + 3 = 10 state machine cycles: the line is high for T1 + T2 = 7 cycles for a one and for T1 = 2 cycles for a zero, and low for the rest. At a state
machine clock of 8 MHz that is 800 kHz, the usual WS2812 bit rate [verify against the datasheet of the LED in use; the timing limits of the chip are not checked here]. The word in the TX
FIFO carries a 24-bit colour in its upper bits (autopull at 24 bits, shifting left), most significant bit first.
"""
import argparse
import os

from pioweave.builder import Program, NOT_X, PINS, X, write_pio
from pioweave.emulator import run_case

T1, T2, T3 = 2, 5, 3                  # cycles: high before the data decides, the data-dependent high part, low after
CYCLES_PER_BIT = T1 + T2 + T3


def ws2812(chip="rp2040"):
    """The program: one pull per 24 bits (autopull), then for every bit ``out x, 1`` and two jumps.

    Parameters
    ----------
    chip : str
        The chip name for pioweave.builder.Program.

    Returns
    -------
    pioweave.builder.Program
        The program with a one-bit side-set on the data line.
    """
    p = Program("ws2812", chip=chip, side_set=1)
    p.config_out(32, direction="left", auto=True, threshold=24)
    with p.wrapped():
        p.label("bitloop")
        p.out(X, 1, side=0, delay=T3 - 1)                 # next bit into X, line low (the low part of the previous bit ends here)
        p.jmp("do_zero", when=NOT_X, side=1, delay=T1 - 1)  # line high for T1 cycles; a zero jumps
        p.jmp("bitloop", side=1, delay=T2 - 1)            # a one: stays high T2 more cycles
        p.label("do_zero")
        p.nop(side=0, delay=T2 - 1)                       # a zero: low for T2 cycles
    return p


def decode(wave):
    """Read the bits back from the line: a bit is a one when the line is high for more than half of its 10 cycles."""
    bits = []
    for i in range(0, len(wave) - CYCLES_PER_BIT + 1, CYCLES_PER_BIT):
        bits.append(1 if wave[i:i + CYCLES_PER_BIT].count("1") > CYCLES_PER_BIT // 2 else 0)
    return bits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="build/ws2812", help="output directory (default build/ws2812)")
    a = ap.parse_args(argv)
    p = ws2812()
    os.makedirs(a.out, exist_ok=True)
    write_pio([p], os.path.join(a.out, "ws2812.pio"))
    print(p.pio_text())
    colours = [0xFF0000, 0x00FF00, 0x0000A5]              # green, red, blue bytes of the WS2812 order GRB, as 24-bit values
    words = [c << 8 for c in colours]                      # the upper 24 bits are shifted out, most significant first
    cycles = CYCLES_PER_BIT * 24 * len(colours)
    r = run_case(p, cycles, tx=words, side_base=0, dirs=1, out_shift="left", autopull=True, pull_thresh=24)
    wave = r.wave(0)
    bits = decode(wave)
    want = [int(b) for c in colours for b in format(c, "024b")]
    print("line: %s ..." % wave[:60])
    print("decoded %d bits, equal to the colours: %s" % (len(bits), bits == want))
    assert bits == want, (bits, want)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
