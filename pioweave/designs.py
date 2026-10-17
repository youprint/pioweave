# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Example PIO programs written in Amaranth, compiled by pio_compile.py for the platforms of platform_rp.py.

Each one is an ordinary Amaranth Elaboratable: one FSM, pins from platform.buffer() (an amaranth.lib.io.Buffer on a requested pin group), scratch registers from platform.scratch(). One FSM state
is one PIO instruction (one clock cycle); states without effects that follow an instruction become its delay. The comments give the cycle count.
`PROGRAMS` maps a name to (design class, platform name, simulation stimulus, cycles) for the project writers and the tests.
"""
from amaranth.hdl import Module
from amaranth.lib import wiring

__all__ = [
    "PioDesign", "SquareWave", "PulseTrain", "ButtonPulse", "ClockedBits", "PinBranch", "HighPins", "UartTx", "UartRx", "ByteSwap", "NoBlockEcho", "PROGRAMS", "entry",
]


class PioDesign(wiring.Component):
    """Base class of a design for the PIO platforms: an `amaranth.lib.wiring.Component` without ports of its own (its pins come from the platform, `platform.buffer()`,
    and its data path from `platform.shifter()`). Any Elaboratable works as well; this is the form current Amaranth recommends for a module with an interface.

    The example designs of this module (`SquareWave` and the others) are subclasses; ``PROGRAMS`` maps their names to the chip, stimulus and length used by the
    project writers and the tests.
    """

    def __init__(self):
        super().__init__(wiring.Signature({}))


class SquareWave(PioDesign):
    """A square wave on GPIO 0: 2 cycles high, 2 cycles low (period 4 cycles)."""

    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", 0)
        with m.FSM():
            with m.State("HIGH"):
                m.d.sync += pin.o.eq(1)
                m.next = "HIGH_HOLD"
            with m.State("HIGH_HOLD"):          # no effect: folded into the delay of the instruction before it
                m.next = "LOW"
            with m.State("LOW"):
                m.d.sync += pin.o.eq(0)
                m.next = "LOW_HOLD"
            with m.State("LOW_HOLD"):
                m.next = "HIGH"
        return m


class PulseTrain(PioDesign):
    """Three pulses of 2 cycles high / 2 low on GPIO 0, then 4 idle cycles, repeated; X counts the pulses (one `jmp x--` per pulse)."""

    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", 0)
        x = platform.scratch("x")
        with m.FSM():
            with m.State("LOAD"):
                m.d.sync += x.eq(2)             # 3 passes: x = 2, 1, 0
                m.next = "ON"
            with m.State("ON"):
                m.d.sync += pin.o.eq(1)
                m.next = "ON_HOLD"
            with m.State("ON_HOLD"):
                m.next = "OFF"
            with m.State("OFF"):
                m.d.sync += pin.o.eq(0)
                m.next = "OFF_HOLD"
            with m.State("OFF_HOLD"):
                m.next = "COUNT"
            with m.State("COUNT"):              # jmp x-- ON: loop while x was not zero, decrementing it
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "ON"
                with m.Else():
                    m.next = "IDLE"
            with m.State("IDLE"):
                m.next = "IDLE2"
            with m.State("IDLE2"):
                m.next = "IDLE3"
            with m.State("IDLE3"):
                m.next = "LOAD"
        return m


class ButtonPulse(PioDesign):
    """Waits for GPIO 4 to go high, drives GPIO 0 high for 3 cycles, waits for GPIO 4 to go low again (`wait`), and repeats."""

    def elaborate(self, platform):
        m = Module()
        out = platform.buffer(m, "o", "gpio", 0)
        button = platform.buffer(m, "i", "gpio", 4)
        with m.FSM():
            with m.State("WAIT_HIGH"):          # stays here until the pin is high: wait 1 gpio 4
                with m.If(button.i):
                    m.next = "PULSE"
            with m.State("PULSE"):
                m.d.sync += out.o.eq(1)
                m.next = "PULSE_1"
            with m.State("PULSE_1"):
                m.next = "PULSE_2"
            with m.State("PULSE_2"):
                m.next = "END"
            with m.State("END"):
                m.d.sync += out.o.eq(0)
                m.next = "WAIT_LOW"
            with m.State("WAIT_LOW"):           # wait 0 gpio 4
                with m.If(~button.i):
                    m.next = "WAIT_HIGH"
        return m


class ClockedBits(PioDesign):
    """A clock on GPIO 1 (side-set) and a data pin on GPIO 0 (set pins): the data pin gets 1, 0, 1 and the clock toggles with every bit."""

    def elaborate(self, platform):
        m = Module()
        data = platform.buffer(m, "o", "gpio", 0)
        clk = platform.buffer(m, "o", "gpio", 1)
        platform.pio_role(clk, "side")
        with m.FSM():
            with m.State("B1"):
                m.d.sync += data.o.eq(1)
                m.next = "C1"
            with m.State("C1"):                 # a clock edge in a state of its own: side-set carries it with a `nop`
                m.d.sync += clk.o.eq(1)
                m.next = "B2"
            with m.State("B2"):
                m.d.sync += data.o.eq(0)
                m.d.sync += clk.o.eq(0)         # data (set) and clock (side-set) in the same cycle
                m.next = "C2"
            with m.State("C2"):
                m.d.sync += clk.o.eq(1)
                m.next = "B3"
            with m.State("B3"):
                m.d.sync += data.o.eq(1)
                m.d.sync += clk.o.eq(0)
                m.next = "C3"
            with m.State("C3"):
                m.d.sync += clk.o.eq(1)
                m.next = "B1"
        return m


class PinBranch(PioDesign):
    """GPIO 4 selects the pattern on GPIO 0: when it is high the output pulses twice (jmp pin), otherwise once."""

    def elaborate(self, platform):
        m = Module()
        out = platform.buffer(m, "o", "gpio", 0)
        sel = platform.buffer(m, "i", "gpio", 4)
        with m.FSM():
            with m.State("START"):
                with m.If(sel.i):                # jmp pin TWICE
                    m.next = "TWICE"
                with m.Else():
                    m.next = "ONCE"
            with m.State("ONCE"):
                m.d.sync += out.o.eq(1)
                m.next = "ONCE_OFF"
            with m.State("ONCE_OFF"):
                m.d.sync += out.o.eq(0)
                m.next = "GAP"
            with m.State("GAP"):
                m.next = "GAP2"
            with m.State("GAP2"):
                m.next = "START"                 # a jump back to the first state
            with m.State("TWICE"):
                m.d.sync += out.o.eq(1)
                m.next = "TWICE_OFF"
            with m.State("TWICE_OFF"):
                m.d.sync += out.o.eq(0)
                m.next = "TWICE_ON"
            with m.State("TWICE_ON"):
                m.d.sync += out.o.eq(1)
                m.next = "TWICE_END"
            with m.State("TWICE_END"):
                m.d.sync += out.o.eq(0)
                m.next = "START"
        return m


class HighPins(PioDesign):
    """For the 48-GPIO parts (RP2350B, RP2354B): a pulse on GPIO 40 when GPIO 44 is high, a clock on GPIO 41 (side-set). The PIO window is GPIO 16 to 47,
    so the compiler picks GPIO base 16; `wait gpio 44` keeps the real number and the SDK flips bit 4 when it loads the program."""

    def elaborate(self, platform):
        m = Module()
        out = platform.buffer(m, "o", "gpio", 40)
        clk = platform.buffer(m, "o", "gpio", 41)
        platform.pio_role(clk, "side")
        trig = platform.buffer(m, "i", "gpio", 44)
        with m.FSM():
            with m.State("IDLE"):
                m.d.sync += clk.o.eq(0)
                with m.If(trig.i):
                    m.next = "UP"
            with m.State("UP"):
                m.d.sync += out.o.eq(1)
                m.d.sync += clk.o.eq(1)
                m.next = "UP_HOLD"
            with m.State("UP_HOLD"):
                m.next = "DOWN"
            with m.State("DOWN"):
                m.d.sync += out.o.eq(0)
                m.d.sync += clk.o.eq(0)
                m.next = "WAIT_LOW"
            with m.State("WAIT_LOW"):
                with m.If(~trig.i):
                    m.next = "IDLE"
        return m


class UartTx(PioDesign):
    """8N1 UART transmitter on GPIO 0, 2 clock cycles per bit: the program of the Pico SDK's uart_tx example written as an FSM. One word from the TX FIFO is one
    frame (low 8 bits, least significant first). The pin is driven by side-set (idle level and start bit) and by `out pins`."""

    def elaborate(self, platform):
        m = Module()
        tx = platform.buffer(m, "o", "gpio", 0)
        sh = platform.shifter(m, out_shift="right")
        x = platform.scratch("x")
        with m.FSM():
            with m.State("PULL"):
                m.d.sync += tx.o.eq(1)               # idle level, side-set while waiting for a word
                with sh.pull(m):
                    m.next = "PULL_HOLD"
            with m.State("PULL_HOLD"):                # no effect: the delay of the pull
                m.next = "START"
            with m.State("START"):
                m.d.sync += [tx.o.eq(0), x.eq(7)]     # start bit (side-set) and the bit counter
                m.next = "BIT"
            with m.State("BIT"):
                sh.out(m, tx)                         # out pins, 1
                m.next = "LOOP"
            with m.State("LOOP"):
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "BIT"
                with m.Else():
                    m.next = "PULL"
        return m


class UartRx(PioDesign):
    """8N1 UART receiver on GPIO 4, 8 clock cycles per bit (the Pico SDK's uart_rx example): wait for the start bit, sample the middle of every data bit with
    `in pins, 1` (shifting right: the byte ends up in bits 31..24 of the word), push the word."""

    def elaborate(self, platform):
        m = Module()
        rx = platform.buffer(m, "i", "gpio", 4)
        sh = platform.shifter(m, in_shift="right")
        x = platform.scratch("x")
        with m.FSM():
            with m.State("WAIT_START"):
                with m.If(~rx.i):
                    m.next = "SETX"
            with m.State("SETX"):
                m.d.sync += x.eq(7)
                m.next = "H1"
            for k in range(1, 11):                    # ten cycles of delay: the next sample is in the middle of the first data bit
                with m.State("H%d" % k):
                    m.next = "H%d" % (k + 1) if k < 10 else "BIT"
            with m.State("BIT"):
                sh.in_(m, rx)                         # in pins, 1 [6]: the six hold states below are its delay, so a bit lasts 8 cycles
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
            with m.State("PUSH"):
                with sh.push(m):
                    m.next = "WAIT_START"
        return m


class ByteSwap(PioDesign):
    """Pulls a word, splits two bytes into X and Y with `out x, 8` / `out y, 8` (shifting left: the most significant byte first), and pushes them in the other
    order with `in y, 8` / `in x, 8` (shifting left), after 16 zero bits from `in null, 16`: one word in, one word out."""

    def elaborate(self, platform):
        m = Module()
        sh = platform.shifter(m, out_shift="left", in_shift="left")
        with m.FSM():
            with m.State("PULL"):
                with sh.pull(m):
                    m.next = "GET_X"
            with m.State("GET_X"):
                sh.out(m, "x", 8)
                m.next = "GET_Y"
            with m.State("GET_Y"):
                sh.out(m, "y", 8)
                m.next = "ZEROS"
            with m.State("ZEROS"):
                sh.in_(m, "null", 16)
                m.next = "PUT_Y"
            with m.State("PUT_Y"):
                sh.in_(m, "y", 8)
                m.next = "PUT_X"
            with m.State("PUT_X"):
                sh.in_(m, "x", 8)
                m.next = "PUSH"
            with m.State("PUSH"):
                with sh.push(m):
                    m.next = "PULL"
        return m


class NoBlockEcho(PioDesign):
    """`pull noblock` and `push noblock`: X holds 21; each round loads the OSR from the TX FIFO, or from X when the FIFO is empty, and sends it on. With a drained
    RX FIFO nothing is lost; without draining, the fifth word is dropped (the ISR is cleared either way)."""

    def elaborate(self, platform):
        m = Module()
        sh = platform.shifter(m)
        with m.FSM():
            with m.State("INIT"):
                m.d.sync += platform.scratch("x").eq(21)
                m.next = "PULL"
            with m.State("PULL"):
                with sh.pull(m, block=False):
                    m.next = "MOVE"
            with m.State("MOVE"):                     # no effect: lets the pull settle before the OSR is read
                m.next = "OUT"
            with m.State("OUT"):
                sh.out(m, "y", 32)
                m.next = "IN"
            with m.State("IN"):
                sh.in_(m, "y", 32)
                m.next = "PUSH"
            with m.State("PUSH"):
                with sh.push(m, block=False):
                    m.next = "PULL"
        return m


def _uart_frames(bytes_, cycles_per_bit=8, idle=6):
    """A serial line carrying `bytes_` as 8N1 frames, least significant bit first, high when idle."""
    bits = "1" * idle
    for b in bytes_:
        bits_ = "0" + "".join(str((b >> k) & 1) for k in range(8)) + "1"
        bits += "".join(c * cycles_per_bit for c in bits_) + "1" * 4
    return bits


PROGRAMS = {
    "square_wave": (SquareWave, "rp2040", {}, 24),
    "pulse_train": (PulseTrain, "rp2040", {}, 60),
    "button_pulse": (ButtonPulse, "rp2350", {4: "000011111100000111000011111111"}, 60),
    "clocked_bits": (ClockedBits, "rp2040", {}, 30),
    "pin_branch": (PinBranch, "rp2350", {4: "0000000000000000000011111111111111111111"}, 60),
    "high_pins": (HighPins, "rp2350b", {44: "000011110000011111000000111100"}, 50),
    # with a data path: the options give the words in the TX FIFO at cycle 0, words the host writes later, and whether the host empties the RX FIFO
    "uart_tx": (UartTx, "rp2040", {}, 60, {"tx": [0x55, 0xC3]}),
    "uart_rx": (UartRx, "rp2040", {4: _uart_frames([0x5A, 0xC3])}, 230),
    "byte_swap": (ByteSwap, "rp2350", {}, 40, {"tx": [0x11223344], "tx_feed": [(14, 0xAABBCCDD)]}),
    "noblock_echo": (NoBlockEcho, "rp2040", {}, 40, {"tx": [7, 9]}),
}


def entry(name):
    """The registry entry of an example design, always with an options dict.

    Parameters
    ----------
    name : str
        A key of ``PROGRAMS``.

    Returns
    -------
    tuple
        ``(design class, chip, stimulus, cycles, options)``; `options` may hold ``tx``, ``tx_feed`` and ``drain``.

    Raises
    ------
    KeyError
        If `name` is not an example.

    Examples
    --------
    >>> from pioweave.designs import entry
    >>> entry("square_wave")
    (<class 'pioweave.designs.SquareWave'>, 'rp2040', {}, 24, {})
    """
    e = PROGRAMS[name]
    return (e + ({},))[:5] if len(e) == 4 else e
