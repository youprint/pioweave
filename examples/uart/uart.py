# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../../LICENSE.txt)
"""A UART transmitter and a UART receiver (8N1, 8 state machine cycles per bit) as Amaranth FSMs, tested against each other in the emulator.

    python examples/uart/uart.py [--out DIR]

The transmitter takes one word from the TX FIFO per frame (the low 8 bits, least significant bit first) and drives the board's ``uart_tx`` pin (GPIO 0 on a Pico). The receiver
waits for a start bit on ``uart_rx`` (GPIO 1), samples the middle of every bit and pushes one word whose top byte (bits 31..24) is the received byte. The script compiles both,
runs the transmitter in the emulator, feeds its pin waveform to the receiver as input and checks that the bytes come back. Nothing was run on a board.
"""
import argparse
import os

from amaranth.hdl import Module

from pioweave import PioDesign, SimOptions, compile_design
from pioweave.boards.pico import PicoPlatform
from pioweave.emulator import run_compiled

BYTES = [0x55, 0xC3, 0x00, 0xFF]            # at most 4: the TX FIFO holds 4 words
CYCLES_PER_BIT = 8


class UartTx(PioDesign):
    def elaborate(self, platform):
        m = Module()
        tx = platform.buffer(m, "o", "uart_tx", 0)
        sh = platform.shifter(m, out_shift="right")           # the OSR shifts right: bit 0 of the word goes out first
        x = platform.scratch("x")
        with m.FSM():
            with m.State("PULL"):                              # pull block, side-set 1: idle level and stop bit; waits while the TX FIFO is empty
                m.d.sync += tx.o.eq(1)
                with sh.pull(m):
                    m.next = "STOP1"
            for k in range(1, 8):                              # seven states without an effect: the delay of the pull, 8 cycles of stop bit
                with m.State("STOP%d" % k):
                    m.next = "STOP%d" % (k + 1) if k < 7 else "START"
            with m.State("START"):                             # set x, 7 side 0: the start bit (side-set) and the bit counter
                m.d.sync += [tx.o.eq(0), x.eq(7)]
                m.next = "S1"
            for k in range(1, 8):                              # seven more cycles: the start bit lasts 8 cycles
                with m.State("S%d" % k):
                    m.next = "S%d" % (k + 1) if k < 7 else "BIT"
            with m.State("BIT"):                               # out pins, 1 [6]: one data bit for 7 cycles ...
                sh.out(m, tx)
                m.next = "B1"
            for k in range(1, 7):
                with m.State("B%d" % k):
                    m.next = "B%d" % (k + 1) if k < 6 else "LOOP"
            with m.State("LOOP"):                              # ... and jmp x-- : the eighth cycle, and the loop over the 8 bits
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "BIT"
                with m.Else():
                    m.next = "PULL"
        return m


class UartRx(PioDesign):
    def elaborate(self, platform):
        m = Module()
        rx = platform.buffer(m, "i", "uart_rx", 0)
        sh = platform.shifter(m, in_shift="right")            # the ISR shifts right: after 8 bits the byte is in bits 31..24
        x = platform.scratch("x")
        with m.FSM():
            with m.State("WAIT_START"):                        # wait for the start bit: the line going low
                with m.If(~rx.i):
                    m.next = "SETX"
            with m.State("SETX"):
                m.d.sync += x.eq(7)
                m.next = "H1"
            for k in range(1, 11):                             # ten cycles: the first sample falls in the middle of the first data bit
                with m.State("H%d" % k):
                    m.next = "H%d" % (k + 1) if k < 10 else "BIT"
            with m.State("BIT"):                               # in pins, 1 [6]
                sh.in_(m, rx)
                m.next = "B1"
            for k in range(1, 7):
                with m.State("B%d" % k):
                    m.next = "B%d" % (k + 1) if k < 6 else "LOOP"
            with m.State("LOOP"):
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "BIT"
                with m.Else():
                    m.next = "PUSH"
            with m.State("PUSH"):                              # push the word with the byte on top
                with sh.push(m):
                    m.next = "WAIT_START"
        return m


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="build/uart", help="output directory (default build/uart)")
    a = ap.parse_args(argv)
    # the transmitter: project files, then the emulator
    tx_factory = lambda: (UartTx(), PicoPlatform(sm_clock_hz=1_000_000))          # noqa: E731
    design, platform = tx_factory()
    cycles = 100 * len(BYTES)
    platform.build(design, "uart_tx", os.path.join(a.out, "tx"), project="pio", sim=SimOptions(factory=tx_factory, cycles=cycles, tx=tuple(BYTES)))
    tx = compile_design(*tx_factory(), "uart_tx")
    line = run_compiled(tx, cycles, tx=BYTES).wave(tx.pin(0))
    # the receiver gets the transmitter's pin as its input (GPIO 1 on a Pico)
    rx_factory = lambda: (UartRx(), PicoPlatform(sm_clock_hz=1_000_000))          # noqa: E731
    rx_stim = {1: "1" * 4 + line + "1" * 40}
    design, platform = rx_factory()
    platform.build(design, "uart_rx", os.path.join(a.out, "rx"), project="pio", sim=SimOptions(factory=rx_factory, cycles=len(rx_stim[1]), stim=rx_stim))
    rx = compile_design(*rx_factory(), "uart_rx")
    got = run_compiled(rx, len(rx_stim[1]), rx_stim)
    received = [w >> 24 for _, w in got.rx]
    print("sent     %s" % " ".join("%02x" % b for b in BYTES))
    print("received %s" % " ".join("%02x" % b for b in received))
    print("a frame is %d cycles (start bit, 8 data bits, stop bit at %d cycles each)" % (10 * CYCLES_PER_BIT, CYCLES_PER_BIT))
    assert received == BYTES, (BYTES, received)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
