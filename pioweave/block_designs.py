# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""Example blocks with several state machines (docs/blocks.rst): each machine is an ordinary single-machine design, the machines handshake through the shared IRQ flags.

- ``uart_baud``: a baud tick generator (state machine 0) and a UART transmitter (state machine 1) that waits for the tick before every bit
- ``handshake``: a producer (state machine 0) puts nibbles on a four-bit bus and raises REQ (flag 1); a consumer (state machine 1) waits for REQ, reads the bus of the other
  machine, raises ACK (flag 2) and pushes a byte for every two nibbles
- ``ring``: four stages in a ring: stage k waits for flag k, pulses GPIO k for k + 1 cycles, raises flag k + 1 (mod 4); the host raises flag 0 to start it
- ``shared_pin``: two machines write the same GPIO (the higher-numbered one wins when both write in the same cycle)

`BLOCKS` maps a name to ``(function returning the Block, BlockSim keyword arguments)`` for the command line (``python -m pioweave list / build / emulate``) and the tests.
"""
from amaranth.build import Pins, Resource
from amaranth.hdl import Module

from .block import Block, BlockSim, Machine
from .designs import PioDesign
from .platform import PLATFORMS

__all__ = ["BaudTick", "BaudUartTx", "NibbleProducer", "NibbleConsumer", "RingStage", "SlowToggle", "FastToggle", "uart_baud", "handshake", "ring", "shared_pin", "BLOCKS", "entry"]


class BaudTick(PioDesign):
    """A baud tick generator: GPIO 1 (side-set) goes high for one cycle and IRQ flag 0 is raised once every 8 cycles (`irq set 0`)."""

    def elaborate(self, platform):
        m = Module()
        tick = platform.buffer(m, "o", "gpio", 1)
        platform.pio_role(tick, "side")
        sh = platform.shifter(m)
        x = platform.scratch("x")
        with m.FSM():
            with m.State("RAISE"):
                m.d.sync += tick.o.eq(1)
                sh.irq(m, 0, "set")                  # irq set 0 side 1
                m.next = "FALL"
            with m.State("FALL"):
                m.d.sync += tick.o.eq(0)             # nop side 0
                m.next = "LOAD"
            with m.State("LOAD"):
                m.d.sync += x.eq(4)
                m.next = "COUNT"
            with m.State("COUNT"):                   # five passes: 3 + 5 = 8 cycles per tick
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "COUNT"
                with m.Else():
                    m.next = "RAISE"
        return m


class BaudUartTx(PioDesign):
    """An 8N1 UART transmitter on GPIO 0 that sends one bit per tick of IRQ flag 0 (`wait 1 irq 0` before the start bit, every data bit and the stop bit)."""

    def elaborate(self, platform):
        m = Module()
        tx = platform.buffer(m, "o", "gpio", 0)
        sh = platform.shifter(m, out_shift="right")
        x = platform.scratch("x")
        with m.FSM():
            with m.State("PULL"):
                m.d.sync += tx.o.eq(1)               # idle level (side-set) while waiting for a word
                with sh.pull(m):
                    m.next = "WSTART"
            with m.State("WSTART"):
                with sh.wait_irq(m, 0):
                    m.next = "START"
            with m.State("START"):
                m.d.sync += [tx.o.eq(0), x.eq(7)]    # start bit and the bit counter
                m.next = "WBIT"
            with m.State("WBIT"):
                with sh.wait_irq(m, 0):
                    m.next = "BIT"
            with m.State("BIT"):
                sh.out(m, tx)                        # out pins, 1
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


class NibbleProducer(PioDesign):
    """Takes a word from the TX FIFO and puts its two top nibbles on GPIO 0 to 3 (`out pins, 4`, shifting left), raising REQ (flag 1) after each and waiting for ACK (flag 2)."""

    def elaborate(self, platform):
        m = Module()
        platform.add_resources([Resource("bus", 0, Pins("0 1 2 3", dir="o"))])
        bus = platform.buffer(m, "o", "bus", 0)
        sh = platform.shifter(m, out_shift="left")
        x = platform.scratch("x")
        with m.FSM():
            with m.State("PULL"):
                with sh.pull(m):
                    m.next = "INIT"
            with m.State("INIT"):
                m.d.sync += x.eq(1)
                m.next = "PUT"
            with m.State("PUT"):
                sh.out(m, bus)                       # out pins, 4
                m.next = "REQ"
            with m.State("REQ"):
                sh.irq(m, 1, "set")                  # irq set 1
                m.next = "WACK"
            with m.State("WACK"):
                with sh.wait_irq(m, 2):              # wait 1 irq 2 (lowers the flag when it passes)
                    m.next = "DEC"
            with m.State("DEC"):
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "PUT"
                with m.Else():
                    m.next = "PULL"
        return m


class NibbleConsumer(PioDesign):
    """Waits for REQ (flag 1), reads the four-bit bus on GPIO 0 to 3 that the other machine drives, raises ACK (flag 2); autopush at 8 bits sends a byte to the RX FIFO."""

    def elaborate(self, platform):
        m = Module()
        platform.add_resources([Resource("bus", 0, Pins("0 1 2 3", dir="i"))])
        bus = platform.buffer(m, "i", "bus", 0)
        sh = platform.shifter(m, in_shift="left", autopush=True, push_thresh=8)
        with m.FSM():
            with m.State("WREQ"):
                with sh.wait_irq(m, 1):
                    m.next = "SAMPLE"
            with m.State("SAMPLE"):
                with sh.in_(m, bus):                 # in pins, 4 (autopush at 8 bits)
                    m.next = "ACK"
            with m.State("ACK"):
                sh.irq(m, 2, "set")
                m.next = "WREQ"
        return m


class RingStage(PioDesign):
    """Stage `k` of a ring: wait for flag k, drive GPIO k high for k + 1 cycles, low, raise flag (k + 1) mod 4."""

    def __init__(self, k):
        super().__init__()
        self.k = k

    def elaborate(self, platform):
        m = Module()
        k = self.k
        pin = platform.buffer(m, "o", "gpio", k)
        sh = platform.shifter(m)
        with m.FSM():
            with m.State("WAIT"):
                with sh.wait_irq(m, k):
                    m.next = "HIGH"
            with m.State("HIGH"):
                m.d.sync += pin.o.eq(1)
                m.next = "HOLD0" if k else "LOW"
            for j in range(k):                       # k further cycles of the pulse: the delay of the instruction above
                with m.State("HOLD%d" % j):
                    m.next = "HOLD%d" % (j + 1) if j + 1 < k else "LOW"
            with m.State("LOW"):
                m.d.sync += pin.o.eq(0)
                m.next = "PASS"
            with m.State("PASS"):
                sh.irq(m, (k + 1) % 4, "set")
                m.next = "WAIT"
        return m


class SlowToggle(PioDesign):
    """Writes GPIO 2 high for 2 cycles and low for 6 (period 8)."""

    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", 2)
        with m.FSM():
            with m.State("HIGH"):
                m.d.sync += pin.o.eq(1)
                m.next = "H1"
            with m.State("H1"):
                m.next = "LOW"
            with m.State("LOW"):
                m.d.sync += pin.o.eq(0)
                m.next = "L1"
            for j in range(1, 6):
                with m.State("L%d" % j):
                    m.next = "L%d" % (j + 1) if j < 5 else "HIGH"
        return m


class FastToggle(PioDesign):
    """Writes GPIO 2 high and low in turn with a period of 6 cycles (3 high, 3 low); state machine 1 of `shared_pin`, so it wins when both write in the same cycle."""

    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", 2)
        with m.FSM():
            with m.State("HIGH"):
                m.d.sync += pin.o.eq(1)
                m.next = "H1"
            with m.State("H1"):
                m.next = "H2"
            with m.State("H2"):
                m.next = "LOW"
            with m.State("LOW"):
                m.d.sync += pin.o.eq(0)
                m.next = "L1"
            with m.State("L1"):
                m.next = "L2"
            with m.State("L2"):
                m.next = "HIGH"
        return m


def uart_baud(chip="rp2040"):
    """The `uart_baud` block."""
    return Block([Machine("baud", BaudTick), Machine("tx", BaudUartTx)], PLATFORMS[chip], sm_clock_hz=1_000_000, name="uart_baud")


def handshake(chip="rp2040"):
    """The `handshake` block."""
    return Block([Machine("producer", NibbleProducer), Machine("consumer", NibbleConsumer)], PLATFORMS[chip], sm_clock_hz=1_000_000, name="handshake")


def ring(chip="rp2040"):
    """The `ring` block."""
    return Block([Machine("stage%d" % k, lambda k=k: RingStage(k)) for k in range(4)], PLATFORMS[chip], sm_clock_hz=1_000_000, name="ring")


def shared_pin(chip="rp2040"):
    """The `shared_pin` block."""
    return Block([Machine("slow", SlowToggle), Machine("fast", FastToggle)], PLATFORMS[chip], sm_clock_hz=1_000_000, name="shared_pin")


#: name -> (function returning the Block for a chip name, keyword arguments of BlockSim)
BLOCKS = {
    "uart_baud": (uart_baud, {"cycles": 120, "tx": {"tx": [0x41, 0x5A]}}),
    "handshake": (handshake, {"cycles": 80, "tx": {"producer": [0xA5000000, 0x3C000000]}}),
    "ring": (ring, {"cycles": 60, "irq_set": ((0, 0),)}),
    "shared_pin": (shared_pin, {"cycles": 40}),
}


def entry(name, chip=None):
    """The registry entry of an example block.

    Parameters
    ----------
    name : str
        A key of ``BLOCKS``.
    chip : str or None
        A chip name; None means ``"rp2040"``.

    Returns
    -------
    tuple
        ``(Block, BlockSim)`` (a new object each time).

    Raises
    ------
    KeyError
        If `name` is not an example.

    Examples
    --------
    >>> from pioweave.block_designs import entry
    >>> block, sim = entry("ring")
    >>> [m.name for m in block.machines], sim.cycles
    (['stage0', 'stage1', 'stage2', 'stage3'], 60)
    """
    build, opts = BLOCKS[name]
    return build(chip or "rp2040"), BlockSim(**{k: (dict(v) if isinstance(v, dict) else v) for k, v in opts.items()})
