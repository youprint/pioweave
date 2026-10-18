# Copyright (c) 2026 youprint. MIT licence (see ../../LICENSE.txt)
"""Two state machines in one PIO block: a baud generator and a UART transmitter that waits for the generator's tick before every bit.

    python examples/block/block.py [--out DIR]

State machine 0 raises IRQ flag 0 every 8 cycles (and shows the tick on the board's ``led`` pin by side-set); state machine 1 takes a word from the TX FIFO and sends it as an 8N1
frame on the board's ``uart_tx`` pin, one bit per tick (``wait 1 irq 0``). The script builds the project (``Block.build``: one ``.pio`` file with two programs, a ``bitweave.toml``
with one case per machine), runs the whole block in the Amaranth simulation and in the emulator, checks that they agree, and decodes the frame from the pin waveform. Nothing was run
on a board.
"""
import argparse
import os

from amaranth.hdl import Module

from pioweave import PioDesign
from pioweave.block import Block, BlockSim, Machine, block_run, emulate_block
from pioweave.boards.pico import PicoPlatform

WORDS = [0x41, 0xC3]


class Baud(PioDesign):
    def elaborate(self, platform):
        m = Module()
        led = platform.buffer(m, "o", "led", 0)
        platform.pio_role(led, "side")
        sh = platform.shifter(m)
        x = platform.scratch("x")
        with m.FSM():
            with m.State("RAISE"):                      # irq set 0 side 1
                m.d.sync += led.o.eq(1)
                sh.irq(m, 0, "set")
                m.next = "FALL"
            with m.State("FALL"):                       # nop side 0
                m.d.sync += led.o.eq(0)
                m.next = "LOAD"
            with m.State("LOAD"):
                m.d.sync += x.eq(4)
                m.next = "COUNT"
            with m.State("COUNT"):                      # jmp x-- COUNT: with the three states above, 8 cycles per tick
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "COUNT"
                with m.Else():
                    m.next = "RAISE"
        return m


class Frame(PioDesign):
    def elaborate(self, platform):
        m = Module()
        tx = platform.buffer(m, "o", "uart_tx", 0)
        sh = platform.shifter(m, out_shift="right")
        x = platform.scratch("x")
        with m.FSM():
            with m.State("PULL"):
                m.d.sync += tx.o.eq(1)
                with sh.pull(m):
                    m.next = "WSTART"
            with m.State("WSTART"):
                with sh.wait_irq(m, 0):                 # wait 1 irq 0: one tick, and the flag is lowered again
                    m.next = "START"
            with m.State("START"):
                m.d.sync += [tx.o.eq(0), x.eq(7)]
                m.next = "WBIT"
            with m.State("WBIT"):
                with sh.wait_irq(m, 0):
                    m.next = "BIT"
            with m.State("BIT"):
                sh.out(m, tx)
                m.next = "LOOP"
            with m.State("LOOP"):
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "WBIT"
                with m.Else():
                    m.next = "WSTOP"
            with m.State("WSTOP"):
                with sh.wait_irq(m, 0):
                    m.next = "STOP"
            with m.State("STOP"):
                m.d.sync += tx.o.eq(1)
                m.next = "PULL"
        return m


def decode(wave, start, per_bit=8):
    """The byte of the 8N1 frame whose start bit begins at cycle `start`, sampled in the middle of each bit."""
    bits = [wave[start + per_bit * k + per_bit // 2] for k in range(10)]
    assert bits[0] == "0" and bits[9] == "1", bits
    return int("".join(bits[1:9])[::-1], 2)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="build/block", help="output directory (default build/block)")
    a = ap.parse_args(argv)
    block = Block([Machine("baud", Baud), Machine("frame", Frame)], PicoPlatform, sm_clock_hz=1_000_000, name="uart_baud")
    sim = BlockSim(cycles=40 + 100 * len(WORDS), tx={"frame": WORDS})
    block.build("uart_baud", a.out, project="pico-c", sim=sim)
    bc = block.compile()
    print(bc.describe())
    ref, emu = block_run(block, sim, bc), emulate_block(bc, sim)
    assert ref["pads"] == emu["pads"] and ref["irq"] == emu["irq"], "the Amaranth simulation and the emulator disagree"
    tx = ref["pads"][0]                                  # GPIO 0 is the board's uart_tx pin
    got, t = [], 0
    while "0" in tx[t:] and len(got) < len(WORDS):
        t = tx.index("0", t)
        got.append(decode(tx, t))
        t += 80
    print("sent     %s" % " ".join("%02x" % w for w in WORDS))
    print("received %s" % " ".join("%02x" % w for w in got))
    assert got == WORDS, (WORDS, got)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
