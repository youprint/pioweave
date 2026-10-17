# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""Example PIO programs written in Amaranth, compiled by pioweave.compile for the platforms of pioweave.platform.

Each one is an ordinary Amaranth Elaboratable: one FSM, pins from platform.buffer() (an amaranth.lib.io.Buffer on a requested pin group), scratch registers from platform.scratch(). One FSM state
is one PIO instruction (one clock cycle); states without effects that follow an instruction become its delay. The comments give the cycle count.
`PROGRAMS` maps a name to (design class, platform name, simulation stimulus, cycles) for the project writers and the tests.
"""
from amaranth.build import Pins, Resource
from amaranth.hdl import Module
from amaranth.lib import wiring

__all__ = [
    "PioDesign", "SquareWave", "PulseTrain", "ButtonPulse", "ClockedBits", "PinBranch", "HighPins", "UartTx", "UartRx", "ByteSwap", "NoBlockEcho", "SpiMaster", "OsreLoop", "IrqHandshake", "PinGate", "TxJoin", "OpenDrain", "BusTurnaround", "PROGRAMS", "entry",
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
                m.next = "START_HOLD"
            with m.State("START_HOLD"):               # no effect: the delay of START, so the start bit lasts 2 cycles like every other bit
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


class SpiMaster(PioDesign):
    """A full-duplex SPI master (mode 0, most significant bit first, 2 cycles per half bit) with **autopull and autopush**: `out pins, 1` takes the next bit of the OSR and
    the OSR is refilled from the TX FIFO by itself every 8 bits (the threshold), `in pins, 1` shifts the sampled bit into the ISR and the ISR is pushed by itself every 8 bits.
    The clock is the side-set pin; the data go out on the OUT pin and are sampled from the IN pin. A TX word supplies one byte, in its most significant byte; the received
    byte arrives in the low byte of the RX word."""

    def elaborate(self, platform):
        m = Module()
        clk = platform.buffer(m, "o", "gpio", 2)
        mosi = platform.buffer(m, "o", "gpio", 3)
        miso = platform.buffer(m, "i", "gpio", 4)
        sh = platform.shifter(m, out_shift="left", in_shift="left", autopull=True, pull_thresh=8, autopush=True, push_thresh=8)
        with m.FSM():
            with m.State("OUT"):
                m.d.sync += clk.o.eq(0)               # assigned before the `with`, so that it applies in the stalled cycles too
                with sh.out(m, mosi, 1):              # out pins, 1 side 0 [1]: stalls while the TX FIFO has no word for the refill
                    m.next = "OUT_HOLD"
            with m.State("OUT_HOLD"):
                m.next = "IN"
            with m.State("IN"):
                m.d.sync += clk.o.eq(1)
                with sh.in_(m, miso, 1):              # in pins, 1 side 1 [1]: stalls while the RX FIFO is full
                    m.next = "IN_HOLD"
            with m.State("IN_HOLD"):
                m.next = "OUT"
        return m


class OsreLoop(PioDesign):
    """`pull ifempty`, `jmp !osre`, `mov ..., status` and `mov pins`: bytes go out of the OUT pin least significant bit first, one bit per three cycles; with a threshold of 8 a
    word supplies one byte (its low byte), because `pull ifempty` does nothing until 8 bits have been shifted out and `jmp !osre` loops while the OSR is not empty (fewer than 8 bits
    shifted). After each byte the pin shows whether the TX FIFO holds fewer than 2 words (`mov y, status`, `mov pins, y`)."""

    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", 3)
        sh = platform.shifter(m, out_shift="right", pull_thresh=8, status="tx", status_n=2)
        with m.FSM():
            with m.State("PULL"):
                with sh.pull(m, ifempty=True):        # pull ifempty block
                    m.next = "OUT"
            with m.State("OUT"):
                sh.out(m, pin, 1)                     # out pins, 1 [1]
                m.next = "OUT_HOLD"
            with m.State("OUT_HOLD"):
                m.next = "AGAIN"
            with m.State("AGAIN"):
                with m.If(sh.osr_not_empty):          # jmp !osre PULL
                    m.next = "PULL"
                with m.Else():
                    m.next = "STATUS"
            with m.State("STATUS"):
                sh.mov(m, "y", "status")              # mov y, status: all ones while the TX FIFO holds fewer than 2 words
                m.next = "SHOW"
            with m.State("SHOW"):
                sh.mov(m, pin, "y")                   # mov pins, y
                m.next = "SHOW_HOLD"
            with m.State("SHOW_HOLD"):
                m.next = "PULL"
        return m


class IrqHandshake(PioDesign):
    """IRQ flags between the program and the host: the output goes high, `irq wait 0` raises flag 0 and stalls until the host lowers it, the output goes low, `irq set 2 rel`
    raises flag 2 (state machine 0, so flag 2), `wait 1 irq 1` stalls until the host raises flag 1 (and lowers it again by passing), and `irq clear 2` lowers flag 2."""

    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", 0)
        sh = platform.shifter(m)
        with m.FSM():
            with m.State("HIGH"):
                m.d.sync += pin.o.eq(1)
                m.next = "RAISE"
            with m.State("RAISE"):
                with sh.irq(m, 0, "wait"):            # irq wait 0
                    m.next = "LOW"
            with m.State("LOW"):
                m.d.sync += pin.o.eq(0)
                m.next = "FLAG2"
            with m.State("FLAG2"):
                sh.irq(m, 2, "set", rel=True)         # irq set 2 rel
                m.next = "GO"
            with m.State("GO"):
                with sh.wait_irq(m, 1):               # wait 1 irq 1
                    m.next = "DROP2"
            with m.State("DROP2"):
                sh.irq(m, 2, "clear")                 # irq clear 2
                m.next = "HIGH"
        return m


class PinGate(PioDesign):
    """`wait pin`: a three-pin input bus on GPIO 4 to 6 (the IN group, added to the platform by the design); the output on GPIO 0 goes high when bit 1 of the bus (GPIO 5) goes
    high (`wait 1 pin 1`) and low when bit 0 (GPIO 4) goes low (`wait 0 pin 0`)."""

    def elaborate(self, platform):
        m = Module()
        platform.add_resources([Resource("bus", 0, Pins("4 5 6", dir="i"))])
        out = platform.buffer(m, "o", "gpio", 0)
        bus = platform.buffer(m, "i", "bus", 0)
        with m.FSM():
            with m.State("WAIT_HIGH"):
                with m.If(bus.i[1]):                  # wait 1 pin 1: relative to the IN base (GPIO 4), so GPIO 5
                    m.next = "UP"
            with m.State("UP"):
                m.d.sync += out.o.eq(1)
                m.next = "WAIT_LOW"
            with m.State("WAIT_LOW"):
                with m.If(~bus.i[0]):                 # wait 0 pin 0
                    m.next = "DOWN"
            with m.State("DOWN"):
                m.d.sync += out.o.eq(0)
                m.next = "WAIT_HIGH"
        return m


class TxJoin(PioDesign):
    """Joined FIFOs (`fifo_join="tx"`): the TX FIFO holds eight words and there is no RX FIFO. Eight words are in it at cycle 0 (a normal TX FIFO keeps only four, the
    host's other writes are dropped); each round `pull block` takes one and `out pins, 4` shows its low nibble on GPIO 4 to 7."""

    def elaborate(self, platform):
        m = Module()
        platform.add_resources([Resource("nibble", 0, Pins("4 5 6 7", dir="o"))])
        nib = platform.buffer(m, "o", "nibble", 0)
        sh = platform.shifter(m, fifo_join="tx")
        with m.FSM():
            with m.State("PULL"):
                with sh.pull(m):
                    m.next = "OUT"
            with m.State("OUT"):
                sh.out(m, nib)                        # out pins, 4 [1]
                m.next = "HOLD"
            with m.State("HOLD"):
                m.next = "PULL"
        return m


class OpenDrain(PioDesign):
    """An open-drain line on GPIO 0 with a pull-up outside (pin directions, design note 2f): the level register stays 0, ``set pindirs, 1`` pulls the line low for three
    cycles and ``set pindirs, 0`` lets it go; then `wait 1 gpio 0` waits until the line is high (another device may hold it low), and two idle cycles follow.
    The pad is the output where the pin is an output and the outside world elsewhere; the simulations report the directions too."""

    def elaborate(self, platform):
        m = Module()
        line = platform.buffer(m, "io", "gpio", 0)
        d = platform.directions(line)             # initial direction: input (the line is released)
        with m.FSM():
            with m.State("PULL_LOW"):
                m.d.sync += d.eq(1)               # set pindirs, 1 [2]: the output register is 0, so the line goes low
                m.next = "HOLD1"
            with m.State("HOLD1"):
                m.next = "HOLD2"
            with m.State("HOLD2"):
                m.next = "RELEASE"
            with m.State("RELEASE"):
                m.d.sync += d.eq(0)               # set pindirs, 0
                m.next = "WAIT_HIGH"
            with m.State("WAIT_HIGH"):
                with m.If(line.i):                # wait 1 gpio 0: the pad, which another device may hold low
                    m.next = "IDLE1"
            with m.State("IDLE1"):
                m.next = "IDLE2"
            with m.State("IDLE2"):
                m.next = "PULL_LOW"
        return m


class BusTurnaround(PioDesign):
    """A four-pin bidirectional bus on GPIO 4 to 7 (RP2350: `mov pindirs`). Each round takes a word from the TX FIFO: its low nibble is driven as the levels (``out pins, 4``), the next
    nibble selects which pins are outputs (``out pindirs, 4``; the others are inputs), ``in pins, 4`` reads the pad (own output where driven, the outside world elsewhere) and
    pushes it, and ``mov pindirs, !null`` ... no: ``mov pindirs, null`` releases the bus (all inputs) before the next round."""

    def elaborate(self, platform):
        m = Module()
        platform.add_resources([Resource("bus", 0, Pins("4 5 6 7", dir="io"))])
        bus = platform.buffer(m, "io", "bus", 0)
        d = platform.directions(bus)
        sh = platform.shifter(m)
        with m.FSM():
            with m.State("PULL"):
                with sh.pull(m):
                    m.next = "LEVEL"
            with m.State("LEVEL"):
                sh.out(m, bus, 4)                 # out pins, 4
                m.next = "DIRS"
            with m.State("DIRS"):
                sh.out(m, d, 4)                   # out pindirs, 4
                m.next = "READ"
            with m.State("READ"):
                sh.in_(m, bus, 4)                 # in pins, 4: the pad
                m.next = "PUSH"
            with m.State("PUSH"):
                with sh.push(m):
                    m.next = "RELEASE"
            with m.State("RELEASE"):
                sh.mov(m, d, "null")              # mov pindirs, null: all pins are inputs again
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
    "osre_loop": (OsreLoop, "rp2040", {}, 90, {"tx": [0xA5, 0x3C, 0xF0], "tx_feed": [(60, 0x99)]}),
    "irq_handshake": (IrqHandshake, "rp2040", {}, 70, {"irq_clear": [(12, 0), (46, 0)], "irq_set": [(5, 1), (30, 1), (52, 1)]}),
    "pin_gate": (PinGate, "rp2040", {4: "0001111000111110000", 5: "0000111111000011111", 6: "0"}, 60),
    "tx_join": (TxJoin, "rp2040", {}, 48, {"tx": [0x1, 0x2, 0x4, 0x8, 0x3, 0x5, 0xA, 0xF, 0xC], "tx_feed": [(30, 0x6)]}),
    "open_drain": (OpenDrain, "rp2040", {0: "1111111100001111111111110000000011111111"}, 60),
    "bus_turnaround": (BusTurnaround, "rp2350", {4: "0011010110", 5: "1100101011", 6: "0101100110", 7: "1110001101"}, 48, {"tx": [0x31, 0xA5, 0x0F, 0x72]}),
    "spi_master": (SpiMaster, "rp2040", {4: "0011010111001010" * 8}, 80, {"tx": [0xA5000000, 0x3C000000], "tx_feed": [(50, 0x81000000)]}),
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
        ``(design class, chip, stimulus, cycles, options)``; `options` may hold ``tx``, ``tx_feed``, ``drain``, ``irq_set`` and ``irq_clear``.

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
