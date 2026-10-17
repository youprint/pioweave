# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""The data path of a PIO state machine for designs written in Amaranth: the TX and RX FIFOs and the OSR / ISR shifters (design note docs/amaranth-pio.md).

`platform.shifter(m)` returns a `Shifter`. Its methods add to the current FSM state exactly the register updates that one PIO instruction does, so the Amaranth
simulation of the design is the reference for the compiled program:

    sh = platform.shifter(m, out_shift="right", in_shift="right")
    with m.State("PULL"):
        with sh.pull(m):                 # pull block: the state stays while the TX FIFO is empty
            m.next = "BIT"
    with m.State("BIT"):
        sh.out(m, tx)                    # out pins, N   (N = width of the pin group; "x", "y" and "null" are the other destinations)
        m.next = "BIT"
    with m.State("RECV"):
        sh.in_(m, rx)                    # in pins, N
    with m.State("DONE"):
        with sh.push(m):                 # push block
            m.next = "RECV"

The FIFOs are a model (`FifoModel`, a submodule) that is driven by registers of the design: a toggle per word taken or stored, so that the design needs no
combinational logic and a pull / push takes effect in the same cycle for the next instruction. The compiler reads the helper statements through two marker
registers (`op`, `arg`) and checks that the statements of a state on the shifter registers are exactly those of the one operation the marker names.
"""
import contextlib

from amaranth.hdl import Cat, Const, Elaboratable, Module, Mux, Signal
from amaranth.hdl import Array

DEPTH = 4          # words in each FIFO (RP2040 datasheet 3.5.3, RP2350 datasheet 11.4; joining the two FIFOs to 8 is not modelled)

# marker codes of the operation a state performs (assigned to Shifter.op)
PULL_BLOCK, PULL_NOBLOCK, PUSH_BLOCK, PUSH_NOBLOCK = 1, 2, 3, 4
OUT_PINS, OUT_X, OUT_Y, OUT_NULL = 5, 6, 7, 8
IN_PINS, IN_X, IN_Y, IN_NULL = 9, 10, 11, 12
NAMES = {PULL_BLOCK: "pull block", PULL_NOBLOCK: "pull noblock", PUSH_BLOCK: "push block", PUSH_NOBLOCK: "push noblock", OUT_PINS: "out pins", OUT_X: "out x",
         OUT_Y: "out y", OUT_NULL: "out null", IN_PINS: "in pins", IN_X: "in x", IN_Y: "in y", IN_NULL: "in null"}


class FifoModel(Elaboratable):
    """The two FIFOs of a state machine, seen from the design (`tx_ready`, `tx_head`, `rx_space`, and the toggles `tx_pop` / `rx_push` with `rx_data`) and from
    the host (the `host_*` signals, driven by a testbench). A word taken or stored by the design in cycle t is applied one clock edge later, and the flags and the
    head of the FIFO already account for it, so the next instruction sees what the PIO shows it."""

    def __init__(self, tx_init=()):
        self.tx_init = list(tx_init)[:DEPTH]
        # driven by the design
        self.tx_pop = Signal()
        self.rx_push = Signal()
        self.rx_data = Signal(32)
        # read by the design
        self.tx_ready = Signal()
        self.tx_head = Signal(32)
        self.rx_space = Signal()
        # the host side: a write into the TX FIFO, a read of the RX FIFO, and what a testbench needs to see
        self.host_tx_en = Signal()
        self.host_tx_data = Signal(32)
        self.host_rx_pop = Signal()
        self.host_rx_level = Signal(3)
        self.host_rx_head = Signal(32)
        self.host_rx_pending = Signal()          # the design pushed in the previous cycle: host_rx_data is that word
        self.host_rx_data = Signal(32)
        self.host_tx_level = Signal(3)
        self.host_tx_dropped = Signal(8)

    def elaborate(self, platform):
        m = Module()
        n0 = len(self.tx_init)
        tx_mem = Array([Signal(32, init=self.tx_init[i] if i < n0 else 0, name="tx_mem%d" % i) for i in range(DEPTH)])
        tx_level, tx_rd, tx_wr, tx_seen = Signal(3, init=n0), Signal(2), Signal(2, init=n0 % DEPTH), Signal()
        rx_mem = Array([Signal(32, name="rx_mem%d" % i) for i in range(DEPTH)])
        rx_level, rx_rd, rx_wr, rx_seen = Signal(3), Signal(2), Signal(2), Signal()
        pend_tx = Signal()
        pend_rx = Signal()
        m.d.comb += [
            pend_tx.eq(self.tx_pop ^ tx_seen),
            pend_rx.eq(self.rx_push ^ rx_seen),
            self.tx_ready.eq(tx_level > pend_tx),
            self.tx_head.eq(tx_mem[(tx_rd + pend_tx)[:2]]),
            self.rx_space.eq((rx_level + pend_rx) < DEPTH),
            self.host_rx_level.eq(rx_level),
            self.host_rx_head.eq(rx_mem[rx_rd]),
            self.host_rx_pending.eq(pend_rx),
            self.host_rx_data.eq(self.rx_data),
            self.host_tx_level.eq(tx_level),
        ]
        # TX: apply the pending pop, then the host write (dropped when the FIFO is full)
        tx_after_pop = Signal(3)
        m.d.comb += tx_after_pop.eq(tx_level - pend_tx)
        write = Signal()
        m.d.comb += write.eq(self.host_tx_en & (tx_after_pop < DEPTH))
        m.d.sync += [tx_seen.eq(self.tx_pop), tx_level.eq(tx_after_pop + write), tx_rd.eq(tx_rd + pend_tx)]
        with m.If(write):
            m.d.sync += [tx_mem[tx_wr].eq(self.host_tx_data), tx_wr.eq(tx_wr + 1)]
        with m.If(self.host_tx_en & ~write):
            m.d.sync += self.host_tx_dropped.eq(self.host_tx_dropped + 1)
        # RX: store the pending push, then the host read
        rd = Signal()
        m.d.comb += rd.eq(self.host_rx_pop & (rx_level != 0))
        m.d.sync += [rx_seen.eq(self.rx_push), rx_level.eq(rx_level + pend_rx - rd), rx_rd.eq(rx_rd + rd)]
        with m.If(pend_rx):
            m.d.sync += [rx_mem[rx_wr].eq(self.rx_data), rx_wr.eq(rx_wr + 1)]
        return m


class Shifter:
    """OSR, ISR and the FIFOs of one state machine; created by `platform.shifter(m)`."""

    def __init__(self, platform, m, out_shift="right", in_shift="right", tx_init=()):
        if out_shift not in ("right", "left") or in_shift not in ("right", "left"):
            raise ValueError('out_shift and in_shift are "right" or "left"')
        if getattr(platform, "_shifter", None) is not None:
            raise ValueError("a state machine has one set of FIFOs and shifters: platform.shifter() was already called")
        self.platform = platform
        self.out_shift, self.in_shift = out_shift, in_shift
        self.out_right = out_shift == "right"
        self.in_right = in_shift == "right"
        self.fifo = FifoModel(tx_init)
        m.submodules.pio_fifo = self.fifo
        self.osr, self.osr_n = Signal(32, name="osr"), Signal(6, init=32, name="osr_n")      # the OSR counts what was shifted out; empty = 32
        self.isr, self.isr_n = Signal(32, name="isr"), Signal(6, name="isr_n")
        self.op, self.arg = Signal(5, name="pio_op"), Signal(6, name="pio_arg")
        self.in_sources = []        # the input pin signals `in_` reads (the compiler needs to know which pin group is the IN group)
        platform._shifter = self

    # ---- helpers
    def _sat(self, count, n):
        return Mux(count + n > 32, 32, count + n)

    def _mark(self, m, op, n=None):
        m.d.sync += self.op.eq(op)
        if n is not None:
            m.d.sync += self.arg.eq(n)

    def _out_data(self, n):
        return self.osr[:n] if self.out_right else self.osr[32 - n:]

    def _osr_shift(self, m, n):
        m.d.sync += [self.osr.eq(self.osr >> n if self.out_right else (self.osr << n)[:32]), self.osr_n.eq(self._sat(self.osr_n, n))]

    def _isr_shift(self, m, data, n):
        new = Cat(self.isr[n:], data) if self.in_right else Cat(data, self.isr[:32 - n])
        m.d.sync += [self.isr.eq(new), self.isr_n.eq(self._sat(self.isr_n, n))]

    @staticmethod
    def _count(n, limit=32):
        if not isinstance(n, int) or not 1 <= n <= limit:
            raise ValueError("the bit count must be a constant from 1 to %d, not %r" % (limit, n))
        return n

    # ---- instructions
    @contextlib.contextmanager
    def pull(self, m, block=True):
        """`pull block` (default): use as `with sh.pull(m): m.next = ...`; the body runs when a word was taken, otherwise the state stays. `block=False`
        (`pull noblock`): a plain call, X is loaded into the OSR when the FIFO is empty."""
        f = self.fifo
        if block:
            with m.If(f.tx_ready):
                self._mark(m, PULL_BLOCK)
                m.d.sync += [self.osr.eq(f.tx_head), self.osr_n.eq(0), f.tx_pop.eq(~f.tx_pop)]
                yield
        else:
            x = self.platform.scratch("x")
            self._mark(m, PULL_NOBLOCK)
            m.d.sync += [self.osr.eq(Mux(f.tx_ready, f.tx_head, x)), self.osr_n.eq(0), f.tx_pop.eq(f.tx_pop ^ f.tx_ready)]
            yield

    @contextlib.contextmanager
    def push(self, m, block=True):
        """`push block` (default), used like `pull`; `block=False`: the word is dropped (ISR cleared) when the RX FIFO is full."""
        f = self.fifo
        if block:
            with m.If(f.rx_space):
                self._mark(m, PUSH_BLOCK)
                m.d.sync += [f.rx_data.eq(self.isr), f.rx_push.eq(~f.rx_push), self.isr.eq(0), self.isr_n.eq(0)]
                yield
        else:
            self._mark(m, PUSH_NOBLOCK)
            m.d.sync += [f.rx_data.eq(self.isr), f.rx_push.eq(f.rx_push ^ f.rx_space), self.isr.eq(0), self.isr_n.eq(0)]
            yield

    def out(self, m, dest, n=None):
        """`out dest, n`. `dest` is an output pin group from platform.buffer() (n defaults to its width), "x", "y" or "null"."""
        if dest in ("x", "y", "null"):
            n = self._count(32 if n is None else n)
            data = self._out_data(n)
            self._mark(m, {"x": OUT_X, "y": OUT_Y, "null": OUT_NULL}[dest], n)
            if dest != "null":
                m.d.sync += self.platform.scratch(dest).eq(data)
        else:
            n = self._count(len(dest.o) if n is None else n)
            data = self._out_data(n)
            self._mark(m, OUT_PINS, n)
            m.d.sync += dest.o.eq(data)
        self._osr_shift(m, n)

    def in_(self, m, src, n=None):
        """`in src, n`. `src` is an input pin group from platform.buffer() (n defaults to its width), "x", "y" or "null"."""
        if src in ("x", "y"):
            n = self._count(32 if n is None else n)
            data = self.platform.scratch(src)[:n]
            self._mark(m, IN_X if src == "x" else IN_Y, n)
        elif src == "null":
            n = self._count(32 if n is None else n)
            data = Const(0, n)
            self._mark(m, IN_NULL, n)
        else:
            n = self._count(len(src.i) if n is None else n, len(src.i))
            data = src.i[:n]
            self.in_sources.append(src.i)
            self._mark(m, IN_PINS, n)
        self._isr_shift(m, data, n)
