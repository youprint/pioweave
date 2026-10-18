# Copyright (c) 2026 youprint. MIT licence (see ../../LICENSE.txt)
"""Blink the user LED of a board, written as an Amaranth FSM.

    python examples/blink/blink.py [--board pico|pico2|weact|waveshare] [--out DIR]

One FSM state is one PIO instruction. The LED is asked for by name (``"led"``), so the same design runs on every board of pioweave.boards. The PIO has no timer: the half period
is made of a loop (``jmp x--``) around a delay of 30 hold states (folded by the compiler into ``nop [29]``), 31 passes of 32 cycles, about 994 state machine cycles. With the state
machine clock at 4 kHz that is about 0.25 s per half period. The result is checked in the emulator; nothing was run on a board.
"""
import argparse
import itertools
import os

from amaranth.hdl import Module

from pioweave import PioDesign, SimOptions, compile_design
from pioweave.boards.pico import PicoPlatform
from pioweave.boards.pico2 import Pico2Platform
from pioweave.boards.waveshare_core2350b import WaveshareCore2350BPlatform
from pioweave.boards.weact_studio_rp2350b_core import WeActStudioRP2350BCorePlatform
from pioweave.emulator import run_compiled

BOARDS = {"pico": PicoPlatform, "pico2": Pico2Platform, "weact": WeActStudioRP2350BCorePlatform, "waveshare": WaveshareCore2350BPlatform}
SM_CLOCK_HZ = 4000          # the state machine clock; a divider of the system clock (125 or 150 MHz [verify]) of about 30,000 to 40,000, inside the range of the divider [verify]


class Blink(PioDesign):
    """The LED is on for 994 cycles and off for 994 cycles."""

    def elaborate(self, platform):
        m = Module()
        led = platform.buffer(m, "o", "led", 0)          # the pin named "led" of the board
        x = platform.scratch("x")                        # scratch register X: the loop counter
        with m.FSM():
            for level, name, nxt in ((1, "ON", "OFF"), (0, "OFF", "ON")):
                with m.State(name):                      # set pins, level
                    m.d.sync += led.o.eq(level)
                    m.next = name + "_X"
                with m.State(name + "_X"):               # set x, 31
                    m.d.sync += x.eq(31)
                    m.next = name + "_W0"
                for k in range(30):                      # 30 states without an effect: they become the delay of one nop, nop [29]
                    with m.State("%s_W%d" % (name, k)):
                        m.next = "%s_W%d" % (name, k + 1) if k < 29 else name + "_T"
                with m.State(name + "_T"):               # jmp x-- : decrement x and loop while it was not zero
                    m.d.sync += x.eq(x - 1)
                    with m.If(x != 0):
                        m.next = name + "_W0"
                    with m.Else():
                        m.next = nxt
        return m


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--board", default="pico", choices=sorted(BOARDS))
    ap.add_argument("--out", default="build/blink", help="output directory (default build/blink)")
    a = ap.parse_args(argv)
    board = BOARDS[a.board]
    factory = lambda: (Blink(), board(sm_clock_hz=SM_CLOCK_HZ))        # noqa: E731
    design, platform = factory()
    platform.build(design, "blink", a.out, project="pio", sim=SimOptions(factory=factory, cycles=2200))   # blink.pio, bitweave.toml, README.md
    compiled = compile_design(*factory(), "blink")
    pin = compiled.pin(compiled.output_gpios[0])
    wave = run_compiled(compiled, 2200).wave(pin)
    runs = [(level, len(list(g))) for level, g in itertools.groupby(wave)]
    print("board %s, LED on GPIO %d, written to %s" % (a.board, compiled.output_gpios[0], os.path.join(a.out, "blink.pio")))
    print("emulator: (level, cycles) runs: %s" % runs[:4])
    assert [n for _, n in runs[:2]] == [994, 994], runs
    print("half period %d cycles = %.3f s at %d Hz" % (runs[0][1], runs[0][1] / SM_CLOCK_HZ, SM_CLOCK_HZ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
