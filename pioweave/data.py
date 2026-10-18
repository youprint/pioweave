# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""The data path of a PIO state machine for designs written in Amaranth: the TX and RX FIFOs and the OSR / ISR shifters (design note docs/design-notes/amaranth-pio.rst).

`platform.shifter(m)` returns a `Shifter`. Its methods add to the current FSM state exactly the register updates that one PIO instruction does, so the Amaranth
simulation of the design is the reference for the compiled program::

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
import os
import sys

from amaranth.hdl import Array, Cat, Const, Elaboratable, Module, Mux, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

__all__ = [
    "DEPTH", "PULL_BLOCK", "PULL_NOBLOCK", "PUSH_BLOCK", "PUSH_NOBLOCK", "OUT_PINS", "OUT_X", "OUT_Y", "OUT_NULL", "IN_PINS", "IN_X", "IN_Y", "IN_NULL", "OUT_ISR", "IN_ISR",
    "IN_OSR", "MOV", "OUT_PINDIRS", "IRQ_SET", "IRQ_CLEAR", "IRQ_WAIT", "WAIT_IRQ", "OUT_CODES", "IN_CODES", "MOV_DST", "MOV_SRC", "MOV_OP", "NAMES",
    "FifoModel", "IrqModel", "ShiftFlags", "Shifter",
]

DEPTH = 4          # words in each FIFO (RP2040 datasheet 3.5.3, RP2350 datasheet 11.4); fifo_join gives 8 and 0 or 0 and 8

# marker codes of the operation a state performs (assigned to Shifter.op)
PULL_BLOCK, PULL_NOBLOCK, PUSH_BLOCK, PUSH_NOBLOCK = 1, 2, 3, 4
OUT_PINS, OUT_X, OUT_Y, OUT_NULL = 5, 6, 7, 8
IN_PINS, IN_X, IN_Y, IN_NULL = 9, 10, 11, 12
OUT_ISR, IN_ISR, IN_OSR, MOV = 13, 14, 15, 16
IRQ_SET, IRQ_CLEAR, IRQ_WAIT, WAIT_IRQ = 17, 18, 19, 20
OUT_PINDIRS = 21
NAMES = {PULL_BLOCK: "pull block", PULL_NOBLOCK: "pull noblock", PUSH_BLOCK: "push block", PUSH_NOBLOCK: "push noblock", OUT_PINS: "out pins", OUT_X: "out x",
         OUT_Y: "out y", OUT_NULL: "out null", IN_PINS: "in pins", IN_X: "in x", IN_Y: "in y", IN_NULL: "in null", OUT_ISR: "out isr", IN_ISR: "in isr", IN_OSR: "in osr",
         MOV: "mov", IRQ_SET: "irq set", IRQ_CLEAR: "irq clear", IRQ_WAIT: "irq wait", WAIT_IRQ: "wait irq", OUT_PINDIRS: "out pindirs"}
OUT_CODES = (OUT_PINS, OUT_X, OUT_Y, OUT_NULL, OUT_ISR, OUT_PINDIRS)
IN_CODES = (IN_PINS, IN_X, IN_Y, IN_NULL, IN_ISR, IN_OSR)
# the encoding of a `mov` in the marker `arg` (the 3-bit fields of the instruction word): dst | src << 3 | op << 6
MOV_DST = {"pins": 0, "x": 1, "y": 2, "pindirs": 3, "isr": 6, "osr": 7}
MOV_SRC = {"pins": 0, "x": 1, "y": 2, "null": 3, "status": 5, "isr": 6, "osr": 7}
MOV_OP = {"none": 0, "invert": 1, "reverse": 2}


class FifoModel(wiring.Component):
    """The two FIFOs of a state machine, seen from the design (`tx_ready`, `tx_head`, `rx_space`, and the toggles `tx_pop` / `rx_push` with `rx_data`) and from
    the host (the `host_*` signals, driven by a testbench; ``host_rx_drain`` makes the host take every pushed word in the cycle it is pushed, ``host_rx_pop`` takes one stored word). A word taken or stored by the design in cycle t is applied one clock edge later, and the flags and the
    head of the FIFO already account for it, so the next instruction sees what the PIO shows it. A word the host writes (``host_tx_en`` in cycle t) is held for one
    cycle and also shown to the instruction of cycle t + 1 (so a FIFO that the design emptied or filled in cycle t accepts or drops it as the PIO does). The members of
    its signature are the signals; `In` means the design or the host drives it, `Out` means the model drives it.

    Parameters
    ----------
    tx_init : iterable of int
        Words already in the TX FIFO at cycle 0 (at most `tx_depth`; more are ignored).
    tx_depth, rx_depth : int
        The depth of each FIFO: 4 and 4, 8 and 0 (``fifo_join="tx"``) or 0 and 8 (``"rx"``).
    """

    def __init__(self, tx_init=(), tx_depth=DEPTH, rx_depth=DEPTH):
        if tx_depth not in (0, 4, 8) or rx_depth not in (0, 4, 8):
            raise ValueError("FIFO depths are 0, 4 or 8")
        self.tx_depth, self.rx_depth = tx_depth, rx_depth
        self.tx_init = list(tx_init)[:tx_depth]
        super().__init__(wiring.Signature({
            # driven by the design
            "tx_pop": In(1), "rx_push": In(1), "rx_data": In(32),
            # read by the design
            "tx_ready": Out(1), "tx_head": Out(32), "rx_space": Out(1),
            # the host side: a write into the TX FIFO, a read of the RX FIFO, and what a testbench needs to see
            "host_tx_en": In(1), "host_tx_data": In(32), "host_rx_pop": In(1), "host_rx_drain": In(1),
            "host_rx_level": Out(4), "host_rx_head": Out(32),
            "host_rx_pending": Out(1),          # the design pushed in the previous cycle: host_rx_data is that word
            "host_rx_data": Out(32), "host_tx_level": Out(4), "host_tx_dropped": Out(8),
            # the design's view of the fill levels (for `mov status`)
            "tx_count": Out(4), "rx_count": Out(4),
        }))

    def elaborate(self, platform):
        """Build the FIFO model (Amaranth's ``elaborate``); `platform` is not used."""
        m = Module()
        dt, dr = self.tx_depth, self.rx_depth
        n0 = len(self.tx_init)
        nt, nr = max(dt, 1), max(dr, 1)                 # storage; a FIFO of depth 0 keeps one unused word
        wt, wr_ = (nt - 1).bit_length(), (nr - 1).bit_length()
        tx_mem = Array([Signal(32, init=self.tx_init[i] if i < n0 else 0, name="tx_mem%d" % i) for i in range(nt)])
        tx_level, tx_rd, tx_wr, tx_seen = Signal(4, init=n0), Signal(wt), Signal(wt, init=n0 % nt), Signal()
        rx_mem = Array([Signal(32, name="rx_mem%d" % i) for i in range(nr)])
        rx_level, rx_rd, rx_wr, rx_seen = Signal(4), Signal(wr_), Signal(wr_), Signal()
        pend_tx = Signal()
        pend_rx = Signal()
        store_rx = Signal()                             # a pending push that stays in the FIFO (a draining host takes it in the cycle it is pushed)
        hw_en, hw_data = Signal(), Signal(32)           # the host write of the previous cycle, shown to the design in this one
        after_pop = Signal(4)                           # TX level once the design's pop of the previous cycle has happened
        hw_ok = Signal()                                # the pending host write finds room
        m.d.comb += [
            pend_tx.eq(self.tx_pop ^ tx_seen),
            pend_rx.eq(self.rx_push ^ rx_seen),
            store_rx.eq(pend_rx & ~self.host_rx_drain),
            after_pop.eq(tx_level - pend_tx),
            hw_ok.eq(hw_en & (after_pop < dt)),
            self.tx_ready.eq((after_pop + hw_ok) > 0),
            self.tx_head.eq(Mux(after_pop == 0, hw_data, tx_mem[(tx_rd + pend_tx)[:wt]] if wt else tx_mem[0])),
            self.rx_space.eq((rx_level + store_rx) < dr),
            self.host_rx_level.eq(rx_level),
            self.host_rx_head.eq(rx_mem[rx_rd] if wr_ else rx_mem[0]),
            self.host_rx_pending.eq(pend_rx),
            self.host_rx_data.eq(self.rx_data),
            self.host_tx_level.eq(after_pop),
            self.tx_count.eq(after_pop + hw_ok),
            self.rx_count.eq(rx_level + store_rx),
        ]
        # TX: apply the pending pop, then the host write of the previous cycle (dropped when the FIFO is full)
        m.d.sync += [tx_seen.eq(self.tx_pop), tx_level.eq(after_pop + hw_ok), tx_rd.eq(tx_rd + pend_tx), hw_en.eq(self.host_tx_en), hw_data.eq(self.host_tx_data)]
        with m.If(hw_ok):
            m.d.sync += [tx_mem[tx_wr].eq(hw_data), tx_wr.eq(tx_wr + 1)]
        with m.If(hw_en & ~hw_ok):
            m.d.sync += self.host_tx_dropped.eq(self.host_tx_dropped + 1)
        # RX: store the pending push, then the host read
        rd = Signal()
        m.d.comb += rd.eq(self.host_rx_pop & (rx_level != 0))
        m.d.sync += [rx_seen.eq(self.rx_push), rx_level.eq(rx_level + store_rx - rd), rx_rd.eq(rx_rd + rd)]
        with m.If(store_rx):
            m.d.sync += [rx_mem[rx_wr].eq(self.rx_data), rx_wr.eq(rx_wr + 1)]
        return m


def _caller():
    """The source location ``(file, line)`` of the first frame outside this module (the line of the design that called a helper)."""
    f = sys._getframe(1)
    while f is not None and os.path.abspath(f.f_code.co_filename) == os.path.abspath(__file__):
        f = f.f_back
    return (f.f_code.co_filename, f.f_lineno) if f is not None else None


class IrqModel(Elaboratable):
    """The eight IRQ flags of the PIO block as one state machine and the host see them (design note 2d).

    The design sets and clears flags through toggle registers (`set_tog`, `clr_tog`, one bit per flag, like the FIFO toggles): a toggle in cycle t shows up in the flags one
    cycle later, and the combinational views (`flag`, `view`, `after`) already account for it. The host side is `host_set` / `host_clr` (8 bits): events signalled in cycle t
    take effect at the start of cycle t + 1, after the program's effects of cycle t, clear before set (bitweave's `irq_clear` / `irq_set` entries of a case).

    Parameters
    ----------
    init_set : int
        Flags the host sets before cycle 0.

    Attributes
    ----------
    set_tog, clr_tog : list of amaranth.hdl.Signal
        The toggles the design drives.
    host_set, host_clr : amaranth.hdl.Signal
        The host events.
    flag : list of amaranth.hdl.Signal
        The flags as the instruction of this cycle sees them.
    view : amaranth.hdl.Signal
        The same, as an 8-bit value.
    after : amaranth.hdl.Signal
        The flags after the program's effects of the previous cycle and before the host events of this one (what the PIO simulators report at the end of that cycle).
    """

    def __init__(self, init_set=0):
        self.init_set = init_set & 0xFF
        self.set_tog = [Signal(name="irq_set_tog%d" % i) for i in range(8)]
        self.clr_tog = [Signal(name="irq_clr_tog%d" % i) for i in range(8)]
        self.host_set, self.host_clr = Signal(8), Signal(8)
        self.flag = [Signal(name="irq_flag%d" % i) for i in range(8)]
        self.view, self.after = Signal(8), Signal(8)
        self._MustUse__silence = True

    def elaborate(self, platform):
        """Build the flags (Amaranth's ``elaborate``); `platform` is not used."""
        m = Module()
        reg = Signal(8)                                  # the flags the previous cycle's instruction saw
        set_seen, clr_seen = Signal(8), Signal(8)
        hs, hc = Signal(8, init=self.init_set), Signal(8)    # host events of this cycle, sampled in the previous one
        pend_set, pend_clr = Cat(*self.set_tog) ^ set_seen, Cat(*self.clr_tog) ^ clr_seen
        m.d.comb += [
            self.after.eq((reg & ~pend_clr) | pend_set),          # a flag that is set and cleared in the same cycle ends up set (RP2350 datasheet 11.2.7)
            self.view.eq((self.after & ~hc) | hs),
        ]
        m.d.comb += [self.flag[i].eq(self.view[i]) for i in range(8)]
        m.d.sync += [reg.eq(self.view), set_seen.eq(Cat(*self.set_tog)), clr_seen.eq(Cat(*self.clr_tog)), hs.eq(self.host_set), hc.eq(self.host_clr)]
        return m


class ShiftFlags(Elaboratable):
    """Combinational flags of the data path (``osr_n < pull_thresh`` and the like), driven in a submodule so that the design module has no ``comb`` logic of its own.

    The helpers of `Shifter` create flags with `flag`; the compiler recognises the signals through ``Shifter.flag_info``. Created by `Shifter`, not by the user.
    """

    def __init__(self):
        self.defs = []
        self._MustUse__silence = True        # a design that is refused (or fails) before elaboration is not an error to report

    def flag(self, name, expr):
        """A new 1-bit flag equal to `expr`.

        Parameters
        ----------
        name : str
            The signal name.
        expr : amaranth.hdl.Value
            The expression the flag follows.

        Returns
        -------
        amaranth.hdl.Signal
            The flag.
        """
        sig = Signal(name=name)
        self.defs.append((sig, expr))
        return sig

    def elaborate(self, platform):
        """Build the flags (Amaranth's ``elaborate``); `platform` is not used."""
        m = Module()
        for sig, expr in self.defs:
            m.d.comb += sig.eq(expr)
        return m


class _Op:
    """What `Shifter.out` and `Shifter.in_` return: a context manager whose body runs in the cycle the instruction completes.

    A plain call (without ``with``) is fine while the instruction cannot stall; when it can (autopull for ``out``, autopush for ``in``), the helper registers the call
    and `Shifter.unentered` reports it if it was never used with ``with`` (the compiler refuses the design then).
    """

    def __init__(self, sh, m, go, emit, in_body, loc):
        self.sh, self.m, self.go, self.emit, self.in_body, self.loc = sh, m, go, emit, in_body, loc
        self._cm = None
        if go is None or not in_body:
            emit()
        if go is not None:
            sh._pending.append(self)

    def __enter__(self):
        if self in self.sh._pending:
            self.sh._pending.remove(self)
        self._cm = None
        if self.go is not None:
            self._cm = self.m.If(self.go)
            self._cm.__enter__()
            if self.in_body:
                self.emit()
        return None

    def __exit__(self, *exc):
        if self._cm is not None:
            self._cm.__exit__(*exc)
        return False


class Shifter:
    """OSR, ISR and the FIFOs of one state machine; created by ``platform.shifter(m)``.

    Parameters
    ----------
    platform : pioweave.platform.RP2040PioPlatform
        The platform; it allows one shifter per design.
    m : amaranth.hdl.Module
        The module of the design; the FIFO model is added to it as the submodule ``pio_fifo`` and the flags as ``pio_flags``.
    out_shift, in_shift : str
        ``"right"`` or ``"left"``, the shift direction of the OSR and the ISR.
    tx_init : iterable of int
        Words in the TX FIFO at cycle 0.
    autopull, autopush : bool
        Automatic refill of the OSR / automatic push of the ISR (section 2b of the design note).
    pull_thresh, push_thresh : int
        The thresholds, 1 to 32: the bit count of the OSR / ISR at which the automatic transfer happens (also the threshold of ``pull ifempty`` / ``push iffull``).
    status : str or None
        The source of ``mov ..., status``: ``"tx"``, ``"rx"`` (FIFO level below `status_n`) or ``"irq"`` (PIO version 1: flag `status_n` is set).
    status_n : int
        The level or flag number of `status`.
    fifo_join : str
        ``"none"`` (two FIFOs of 4 words), ``"tx"`` (one TX FIFO of 8 words, no RX FIFO) or ``"rx"`` (one RX FIFO of 8 words, no TX FIFO).

    Attributes
    ----------
    platform : pioweave.platform.RP2040PioPlatform
        The platform.
    out_shift, in_shift : str
        The shift directions as given.
    autopull, autopush, pull_thresh, push_thresh :
        The configuration as given.
    fifo : FifoModel
        The FIFO model.
    flags : ShiftFlags
        The submodule that drives the combinational flags.
    osr, osr_n : amaranth.hdl.Signal
        The output shift register and the number of bits shifted out of it (32 means empty).
    isr, isr_n : amaranth.hdl.Signal
        The input shift register and the number of bits shifted into it.
    op, arg : amaranth.hdl.Signal
        The marker registers the compiler reads: the operation code (see `NAMES`) and its bit count.
    in_sources : list of amaranth.hdl.Signal
        The input pin signals `in_` has read.

    Raises
    ------
    ValueError
        If a direction is not ``"right"`` or ``"left"``, a threshold is not 1 to 32, or the platform already has a shifter.
    """

    def __init__(self, platform, m, out_shift="right", in_shift="right", tx_init=(), autopull=False, pull_thresh=32, autopush=False, push_thresh=32, status=None, status_n=0,
                 fifo_join="none"):
        if out_shift not in ("right", "left") or in_shift not in ("right", "left"):
            raise ValueError('out_shift and in_shift are "right" or "left"')
        for what, v in (("pull_thresh", pull_thresh), ("push_thresh", push_thresh)):
            if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 32:
                raise ValueError("%s must be an integer from 1 to 32, not %r" % (what, v))
        if status not in (None, "tx", "rx", "irq"):
            raise ValueError('status is None, "tx", "rx" or "irq" (the source of `mov ..., status`)')
        if status == "irq" and getattr(platform, "pio_version", 1) < 1:
            raise ValueError('status="irq" needs PIO version 1 (RP2350, RP2354)')
        if not isinstance(status_n, int) or isinstance(status_n, bool) or not 0 <= status_n <= (7 if status == "irq" else 15):
            raise ValueError("status_n must be an integer from 0 to %d, not %r" % (7 if status == "irq" else 15, status_n))
        if fifo_join not in ("none", "tx", "rx"):
            raise ValueError('fifo_join is "none", "tx" (TX FIFO of 8 words, no RX FIFO) or "rx" (RX FIFO of 8 words, no TX FIFO), not %r' % (fifo_join,))
        if getattr(platform, "_shifter", None) is not None:
            raise ValueError("a state machine has one set of FIFOs and shifters: platform.shifter() was already called")
        self.platform = platform
        self.status, self.status_n = status, status_n
        self.fifo_join = fifo_join
        self.out_shift, self.in_shift = out_shift, in_shift
        self.out_right = out_shift == "right"
        self.in_right = in_shift == "right"
        self.autopull, self.pull_thresh, self.autopush, self.push_thresh = bool(autopull), pull_thresh, bool(autopush), push_thresh
        self.fifo = FifoModel(tx_init, {"none": 4, "tx": 8, "rx": 0}[fifo_join], {"none": 4, "tx": 0, "rx": 8}[fifo_join])
        m.submodules.pio_fifo = self.fifo
        self.flags = ShiftFlags()
        m.submodules.pio_flags = self.flags
        self.osr, self.osr_n = Signal(32, name="osr"), Signal(6, init=32, name="osr_n")      # the OSR counts what was shifted out; empty = 32
        self.isr, self.isr_n = Signal(32, name="isr"), Signal(6, name="isr_n")
        self.op, self.arg = Signal(5, name="pio_op"), Signal(8, name="pio_arg")
        self.in_pend = Signal(name="in_pend")        # autopush: the IN has shifted and waits for room in the RX FIFO
        self.irq_pend = Signal(name="irq_pend")      # irq wait: the flag has been set, the instruction waits for it to be cleared
        self.irq_model = None                        # the IrqModel, created by the first helper that needs it
        self.in_sources = []        # the input pin signals `in_` reads (the compiler needs to know which pin group is the IN group)
        self.flag_info = {id(self.fifo.tx_ready): ((PULL_BLOCK,), "body"), id(self.fifo.rx_space): ((PUSH_BLOCK,), "body")}   # flag -> (operations it can stall, style)
        self._flags = {}
        self.cond_flags = {}        # flags that are conditions of a jump: id(signal) -> kind ("osre")
        self._pending = []
        self.bg = None
        if self.autopull:
            f = self.fifo
            self.bg = self.flags.flag("bg_pull", (self.osr_n >= self.pull_thresh) & f.tx_ready)
            with m.If(self.bg):          # the background refill of every cycle that is not an executing OUT (design note 2b)
                m.d.sync += [self.osr.eq(f.tx_head), self.osr_n.eq(0), f.tx_pop.eq(~f.tx_pop)]
        platform._shifter = self

    # ---- helpers
    def _flag(self, key, expr, codes, style):
        if key not in self._flags:
            sig = self.flags.flag("pio_" + "_".join(str(k) for k in key), expr)
            self._flags[key] = sig
            self.flag_info[id(sig)] = (codes, style)
        return self._flags[key]

    def unentered(self):
        """The source locations of helper calls that can stall but were not used with ``with``.

        Returns
        -------
        list of tuple
            ``(file, line)`` of each such call.
        """
        return [op.loc for op in self._pending]

    def _sat(self, count, n):
        return Mux(count + n > 32, 32, count + n)

    def _mark(self, m, op, n=None):
        m.d.sync += self.op.eq(op)
        if n is not None:
            m.d.sync += self.arg.eq(n)

    def _out_data(self, n):
        return self.osr[:n] if self.out_right else self.osr[32 - n:]

    def _osr_shift(self, m, n):
        shifted = self.osr >> n if self.out_right else (self.osr << n)[:32]
        new_n = self._sat(self.osr_n, n)
        if self.autopull:
            f = self.fifo
            post = (new_n >= self.pull_thresh) & f.tx_ready          # the background refill after the shift
            m.d.sync += [self.osr.eq(Mux(post, f.tx_head, shifted)), self.osr_n.eq(Mux(post, 0, new_n)), f.tx_pop.eq(f.tx_pop ^ post)]
        else:
            m.d.sync += [self.osr.eq(shifted), self.osr_n.eq(new_n)]

    def _pull_noop(self, ifempty=False):
        """The condition under which `pull` does nothing, or None when it never does (emulator: ``autopull and osr_count == 0`` or ``ifempty and osr_count < pull_thresh``)."""
        terms = []
        if self.autopull:
            terms.append(self.osr_n == 0)
        if ifempty:
            terms.append(self.osr_n < self.pull_thresh)
        if not terms:
            return None
        return terms[0] | terms[1] if len(terms) == 2 else terms[0]

    def _push_noop(self, iffull=False):
        """The condition under which `push` does nothing, or None (emulator: ``iffull and isr_count < push_thresh``)."""
        return (self.isr_n < self.push_thresh) if iffull else None

    @staticmethod
    def _count(n, limit=32):
        if not isinstance(n, int) or not 1 <= n <= limit:
            raise ValueError("the bit count must be a constant from 1 to %d, not %r" % (limit, n))
        return n

    # ---- conditions
    @property
    def osr_not_empty(self):
        """A flag that is true while the OSR is not empty (``osr_n < pull_thresh``): ``with m.If(sh.osr_not_empty): m.next = LOOP`` is ``jmp !osre LOOP``. PIO can jump when the OSR is not
        empty but not when it is empty.

        Returns
        -------
        amaranth.hdl.Signal
            The flag.
        """
        if "osre" not in self._flags:
            self._flags["osre"] = self.flags.flag("pio_osr_not_empty", self.osr_n < self.pull_thresh)
            self.cond_flags[id(self._flags["osre"])] = "osre"
        return self._flags["osre"]

    # ---- instructions
    def pull(self, m, block=True, ifempty=False):
        """`pull block` (default): use as `with sh.pull(m): m.next = ...`; the body runs when a word was taken, otherwise the state stays. `block=False`
        (`pull noblock`): X is loaded into the OSR when the FIFO is empty (a plain call is enough). `ifempty=True` (`pull ifempty`): does nothing while fewer than
        ``pull_thresh`` bits have been shifted out of the OSR.

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module of the design, inside the FSM state that does the pull.
        block : bool
            ``pull block`` (True) or ``pull noblock`` (False).
        ifempty : bool
            ``pull ifempty``.

        Returns
        -------
        object
            A context manager; its body runs in the cycle in which a word was taken (always, for ``noblock``; also when the pull was a no-op). A blocking pull used without
            ``with`` is refused when the design is compiled.
        """
        f = self.fifo
        noop = self._pull_noop(ifempty)
        arg = 1 if ifempty else None
        x = self.platform.scratch("x")
        if noop is None:
            if block:
                def emit():
                    self._mark(m, PULL_BLOCK)
                    m.d.sync += [self.osr.eq(f.tx_head), self.osr_n.eq(0), f.tx_pop.eq(~f.tx_pop)]
                return _Op(self, m, f.tx_ready, emit, True, _caller())

            def emit():
                self._mark(m, PULL_NOBLOCK)
                m.d.sync += [self.osr.eq(Mux(f.tx_ready, f.tx_head, x)), self.osr_n.eq(0), f.tx_pop.eq(f.tx_pop ^ f.tx_ready)]
            return _Op(self, m, None, emit, False, _caller())
        # the pull is a no-op (it neither takes a word nor stalls) while `noop` holds
        take = ~noop & f.tx_ready
        if block:
            go = self._flag(("pull_go", bool(ifempty)), noop | f.tx_ready, (PULL_BLOCK,), "body")

            def emit():
                self._mark(m, PULL_BLOCK, arg)
                m.d.sync += [self.osr.eq(Mux(take, f.tx_head, self.osr)), self.osr_n.eq(Mux(take, 0, self.osr_n)), f.tx_pop.eq(f.tx_pop ^ take)]
            return _Op(self, m, go, emit, True, _caller())

        def emit():
            self._mark(m, PULL_NOBLOCK, arg)
            m.d.sync += [self.osr.eq(Mux(noop, self.osr, Mux(f.tx_ready, f.tx_head, x))), self.osr_n.eq(Mux(noop, self.osr_n, 0)), f.tx_pop.eq(f.tx_pop ^ take)]
        return _Op(self, m, None, emit, False, _caller())

    def push(self, m, block=True, iffull=False):
        """`push block` (default), used like `pull`; `block=False`: the word is dropped (ISR cleared) when the RX FIFO is full. `iffull=True` (`push iffull`): does nothing
        while fewer than ``push_thresh`` bits have been shifted into the ISR.

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module of the design, inside the FSM state that does the push.
        block : bool
            ``push block`` (True) or ``push noblock`` (False).
        iffull : bool
            ``push iffull``.

        Returns
        -------
        object
            A context manager; its body runs in the cycle in which the word was stored (always, for ``noblock``; also when the push was a no-op).
        """
        f = self.fifo
        noop = self._push_noop(iffull)
        arg = 1 if iffull else None
        if noop is None:
            if block:
                def emit():
                    self._mark(m, PUSH_BLOCK)
                    m.d.sync += [f.rx_data.eq(self.isr), f.rx_push.eq(~f.rx_push), self.isr.eq(0), self.isr_n.eq(0)]
                return _Op(self, m, f.rx_space, emit, True, _caller())

            def emit():
                self._mark(m, PUSH_NOBLOCK)
                m.d.sync += [f.rx_data.eq(self.isr), f.rx_push.eq(f.rx_push ^ f.rx_space), self.isr.eq(0), self.isr_n.eq(0)]
            return _Op(self, m, None, emit, False, _caller())
        take = ~noop & f.rx_space
        if block:
            go = self._flag(("push_go",), noop | f.rx_space, (PUSH_BLOCK,), "body")

            def emit():
                self._mark(m, PUSH_BLOCK, arg)
                m.d.sync += [f.rx_data.eq(self.isr), f.rx_push.eq(f.rx_push ^ take), self.isr.eq(Mux(take, 0, self.isr)), self.isr_n.eq(Mux(take, 0, self.isr_n))]
            return _Op(self, m, go, emit, True, _caller())

        def emit():
            self._mark(m, PUSH_NOBLOCK, arg)
            m.d.sync += [f.rx_data.eq(self.isr), f.rx_push.eq(f.rx_push ^ take), self.isr.eq(Mux(noop, self.isr, 0)), self.isr_n.eq(Mux(noop, self.isr_n, 0))]
        return _Op(self, m, None, emit, False, _caller())

    def out(self, m, dest, n=None):
        """`out dest, n`. `dest` is an output pin group from platform.buffer() (n defaults to its width), "x", "y", "null" or "isr".

        Without autopull a plain call is enough. With autopull the instruction can stall (design note 2b): write ``with sh.out(m, dest, n): m.next = ...``; the body
        runs in the cycle the instruction completes, and the state stays while the OSR waits for the TX FIFO. ``out isr, n`` also sets the ISR bit count to ``n``.
        ``out pindirs, n`` takes the direction register of a pin group (``platform.directions(buffer)``) as `dest` (design note 2f). ``out pc`` and ``out exec`` are not supported
        (design note 2c).

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module of the design, inside the FSM state that does the out.
        dest : object or str
            A buffer from ``platform.buffer(m, "o", ...)``, the direction register of a pin group (``out pindirs``), or ``"x"``, ``"y"``, ``"null"`` or ``"isr"``.
        n : int or None
            The bit count, 1 to 32 (a constant); None means the width of the pin group, or 32.

        Returns
        -------
        object
            A context manager (see above).

        Raises
        ------
        ValueError
            If `n` is not a constant in range, or `dest` is a destination the Amaranth front end does not support.
        """
        if isinstance(dest, str):
            if dest == "pindirs":
                raise ValueError("out pindirs needs the direction register of a pin group as the destination, not the string: %s" % UNSUPPORTED["pindirs"])
            if dest in ("pc", "exec"):
                raise ValueError("out %s is not supported by the Amaranth front end: %s" % (dest, UNSUPPORTED[dest]))
            if dest not in ("x", "y", "null", "isr"):
                raise ValueError('the destination of out is a pin group, "x", "y", "null" or "isr", not %r' % (dest,))
            n = self._count(32 if n is None else n)
            code = {"x": OUT_X, "y": OUT_Y, "null": OUT_NULL, "isr": OUT_ISR}[dest]
        elif isinstance(dest, Signal):
            if self.platform.dirs_info(dest) is None:
                raise ValueError("out pindirs needs the direction register of a pin group from platform.directions(buffer), not %r" % (dest.name,))
            n = self._count(len(dest) if n is None else n)
            code = OUT_PINDIRS
        else:
            n = self._count(len(dest.o) if n is None else n)
            code = OUT_PINS
        data = self._out_data(n)

        def emit():
            self._mark(m, code, n)
            if code in (OUT_X, OUT_Y):
                m.d.sync += self.platform.scratch(dest).eq(data)
            elif code == OUT_PINS:
                m.d.sync += dest.o.eq(data)
            elif code == OUT_PINDIRS:
                m.d.sync += dest.eq(data)
            elif code == OUT_ISR:
                m.d.sync += [self.isr.eq(data), self.isr_n.eq(n)]
            self._osr_shift(m, n)
        go = self._flag(("out_go",), self.osr_n < self.pull_thresh, OUT_CODES, "body") if self.autopull else None
        return _Op(self, m, go, emit, True, _caller())

    def in_(self, m, src, n=None):
        """`in src, n`. `src` is an input pin group from platform.buffer() (n defaults to its width), "x", "y", "null", "isr" or "osr".

        Without autopush a plain call is enough. With autopush the instruction can stall until the RX FIFO has room (design note 2b): write
        ``with sh.in_(m, src, n): m.next = ...``.

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module of the design, inside the FSM state that does the in.
        src : object or str
            A buffer from ``platform.buffer(m, "i", ...)``, or ``"x"``, ``"y"``, ``"null"``, ``"isr"`` or ``"osr"``.
        n : int or None
            The bit count, 1 to 32 (a constant; at most the pin group width for pins); None means the width of the pin group, or 32.

        Returns
        -------
        object
            A context manager (see above).

        Raises
        ------
        ValueError
            If `n` is not a constant in range.
        """
        if isinstance(src, str):
            if src not in ("x", "y", "null", "isr", "osr"):
                raise ValueError('the source of in is a pin group, "x", "y", "null", "isr" or "osr", not %r' % (src,))
            n = self._count(32 if n is None else n)
            code = {"x": IN_X, "y": IN_Y, "null": IN_NULL, "isr": IN_ISR, "osr": IN_OSR}[src]
            data = {IN_X: lambda: self.platform.scratch("x")[:n], IN_Y: lambda: self.platform.scratch("y")[:n], IN_NULL: lambda: Const(0, n),
                    IN_ISR: lambda: self.isr[:n], IN_OSR: lambda: self.osr[:n]}[code]()
        else:
            n = self._count(len(src.i) if n is None else n, len(src.i))
            data = src.i[:n]
            self.in_sources.append(src.i)
            code = IN_PINS
        isr, f = self.isr, self.fifo
        shifted = Cat(isr[n:], data) if self.in_right else Cat(data, isr[:32 - n])
        new_n = self._sat(self.isr_n, n)
        if not self.autopush:
            def emit():
                self._mark(m, code, n)
                m.d.sync += [isr.eq(shifted), self.isr_n.eq(new_n)]
            return _Op(self, m, None, emit, False, _caller())
        # autopush: shift once (in_pend remembers it), push when the count has reached the threshold, stall while the RX FIFO is full
        cur_isr = Mux(self.in_pend, isr, shifted)
        cur_n = Mux(self.in_pend, self.isr_n, new_n)
        trig = cur_n >= self.push_thresh
        go = self._flag(("in_go", n), ~trig | f.rx_space, IN_CODES, "both")
        push = go & trig

        def emit():
            self._mark(m, code, n)
            m.d.sync += [isr.eq(Mux(push, 0, cur_isr)), self.isr_n.eq(Mux(push, 0, cur_n)), self.in_pend.eq(~go), f.rx_data.eq(cur_isr), f.rx_push.eq(f.rx_push ^ push)]
        return _Op(self, m, go, emit, False, _caller())

    def mov(self, m, dst, src, op="none"):
        """`mov dst, [op] src` with the OSR, the ISR, pins or the status as one of the sides (a plain `x.eq(y)` / `x.eq(~y)` between X and Y needs no helper).

        ``dst``: ``"x"``, ``"y"``, ``"osr"``, ``"isr"`` or an output pin group from platform.buffer() (the OUT group: its width is the ``out_count``). ``src``: ``"x"``, ``"y"``,
        ``"null"``, ``"status"``, ``"isr"``, ``"osr"`` or an input pin group (PIO version 1 only: the program then masks the input to the width of the group with
        ``in_count``; PIO version 0 reads 32 pins). ``op``: ``"none"``, ``"invert"`` (``!``) or ``"reverse"`` (``::``, bit reverse). ``mov status`` needs ``status="tx"|"rx"|"irq"`` and
        ``status_n=N`` in ``platform.shifter``: all ones while the FIFO level is below N (``"irq"``, PIO version 1: while IRQ flag N is set). ``dst`` can also be the direction register of a pin group
        (``platform.directions(buffer)``): ``mov pindirs, src``, PIO version 1 only (design note 2f). ``mov pc`` and ``mov exec`` are not supported (design note 2c).

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module of the design, inside the FSM state that does the mov.
        dst, src : object or str
            See above.
        op : str
            See above.

        Raises
        ------
        ValueError
            If an argument is not supported.
        """
        if op not in MOV_OP:
            raise ValueError('op is "none", "invert" or "reverse", not %r' % (op,))
        if isinstance(dst, str):
            if dst == "pindirs":
                raise ValueError("mov pindirs needs the direction register of a pin group as the destination, not the string: %s" % UNSUPPORTED["pindirs"])
            if dst in ("pc", "exec"):
                raise ValueError("mov %s is not supported by the Amaranth front end: %s" % (dst, UNSUPPORTED[dst]))
            if dst not in ("x", "y", "osr", "isr"):
                raise ValueError('the destination of mov is a pin group, "x", "y", "osr" or "isr", not %r' % (dst,))
            dname = dst
        elif isinstance(dst, Signal):
            if self.platform.dirs_info(dst) is None:
                raise ValueError("mov pindirs needs the direction register of a pin group from platform.directions(buffer), not %r" % (dst.name,))
            if getattr(self.platform, "pio_version", 1) < 1:
                raise ValueError("mov pindirs needs PIO version 1 (RP2350 / RP2354); on the RP2040 use out pindirs or set pindirs")
            dname = "pindirs"
        else:
            dname = "pins"
        f = self.fifo
        if isinstance(src, str):
            if src not in ("x", "y", "null", "status", "isr", "osr"):
                raise ValueError('the source of mov is a pin group, "x", "y", "null", "status", "isr" or "osr", not %r' % (src,))
            sname = src
            if src == "status":
                if self.status is None:
                    raise ValueError('mov status needs platform.shifter(m, status="tx" | "rx" | "irq", status_n=N)')
                if self.status == "irq":
                    val = Mux(self._irq(m).flag[self.status_n], Const(0xFFFFFFFF, 32), Const(0, 32))
                else:
                    count = f.tx_count if self.status == "tx" else f.rx_count
                    val = Mux(count < self.status_n, Const(0xFFFFFFFF, 32), Const(0, 32))
            else:
                val = {"x": lambda: self.platform.scratch("x"), "y": lambda: self.platform.scratch("y"), "null": lambda: Const(0, 32), "isr": lambda: self.isr,
                       "osr": lambda: self.osr}[src]()
        else:
            if getattr(self.platform, "pio_version", 1) < 1:
                raise ValueError("mov from pins reads 32 pins on PIO version 0 (RP2040), which the model of one pin group cannot reproduce: use `in pins, n`, or a PIO version 1 chip "
                                 "(it masks the input to the group width with in_count)")
            sname = "pins"
            self.in_sources.append(src.i)
            val = src.i if len(src.i) == 32 else Cat(src.i, Const(0, 32 - len(src.i)))
        if op == "invert":
            val = ~val
        elif op == "reverse":
            val = val[::-1]
        self._mark(m, MOV)
        m.d.sync += self.arg.eq(MOV_DST[dname] | MOV_SRC[sname] << 3 | MOV_OP[op] << 6)
        if dname in ("x", "y"):
            m.d.sync += self.platform.scratch(dname).eq(val)
        elif dname == "pins":
            m.d.sync += dst.o.eq(val[:len(dst.o)])
        elif dname == "pindirs":
            m.d.sync += dst.eq(val[:len(dst)])
        elif dname == "isr":
            m.d.sync += [self.isr.eq(val), self.isr_n.eq(0)]
        else:
            m.d.sync += [self.osr.eq(val), self.osr_n.eq(0)]
            if self.autopull:
                m.d.sync += f.tx_pop.eq(f.tx_pop)         # cancels the background refill of this cycle: the count is 0 after the mov

    # ---- IRQ flags
    def _irq(self, m):
        if self.irq_model is None:
            hub = getattr(self.platform, "irq_hub", None)        # a state machine of a block (pioweave.block) shares the flags with the others
            self.irq_model = hub.port(self.platform.sm_index) if hub is not None else IrqModel(getattr(self.platform, "irq_init", 0))
            m.submodules.pio_irq = self.irq_model
        return self.irq_model

    @staticmethod
    def _flag_number(flag):
        if not isinstance(flag, int) or isinstance(flag, bool) or not 0 <= flag <= 7:
            raise ValueError("an IRQ flag number is a constant from 0 to 7, not %r" % (flag,))
        return flag

    def irq(self, m, flag, mode="set", rel=False):
        """`irq set | clear | wait flag [rel]`: raise or lower IRQ flag `flag` (0 to 7), or raise it and stall until it has been cleared (by the host or another state machine).

        ``set`` and ``clear`` are plain calls. ``wait`` can stall: write ``with sh.irq(m, 3, "wait"): m.next = ...``; the body runs in the cycle the instruction completes.
        ``rel=True`` adds the state machine number to the low two bits of the flag number; this model is state machine 0 (bitweave's default ``sm``), and the generated C project
        claims state machine 0 when a program uses ``rel``.

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module of the design, inside the FSM state that does the irq.
        flag : int
            0 to 7.
        mode : str
            ``"set"``, ``"clear"`` or ``"wait"``.
        rel : bool
            The ``rel`` form.

        Returns
        -------
        object
            A context manager (only ``"wait"`` needs it).

        Raises
        ------
        ValueError
            If the flag number or the mode is not valid.
        """
        if mode not in ("set", "clear", "wait"):
            raise ValueError('the mode of irq is "set", "clear" or "wait", not %r' % (mode,))
        if mode == "wait":
            return self._irq_wait(m, flag, rel)
        (self._irq_set if mode == "set" else self._irq_clear)(m, flag, rel)
        return _Op(self, m, None, lambda: None, False, _caller())

    def _irq_set(self, m, flag, rel=False):
        """`irq set flag`: raise IRQ flag `flag` (0 to 7) and go on. ``rel=True`` is ``irq set flag rel`` (the state machine number is added to the low two bits; this model
        is state machine 0, and the generated C project claims state machine 0).

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module of the design, inside the FSM state that does the irq.
        flag : int
            0 to 7.
        rel : bool
            The ``rel`` form.
        """
        irqm = self._irq(m)
        flag = self._flag_number(flag)
        self._mark(m, IRQ_SET, flag | bool(rel) << 3)
        m.d.sync += irqm.set_tog[flag].eq(~irqm.set_tog[flag])

    def _irq_clear(self, m, flag, rel=False):
        """`irq clear flag`: lower IRQ flag `flag` (0 to 7) and go on. See `Shifter._irq_set` for `rel`.

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module of the design, inside the FSM state that does the irq.
        flag : int
            0 to 7.
        rel : bool
            The ``rel`` form.
        """
        irqm = self._irq(m)
        flag = self._flag_number(flag)
        self._mark(m, IRQ_CLEAR, flag | bool(rel) << 3)
        m.d.sync += irqm.clr_tog[flag].eq(~irqm.clr_tog[flag])

    def _irq_wait(self, m, flag, rel=False):
        """`irq wait flag`: raise the flag, then stall until it has been cleared (by the host, or by another state machine). Write
        ``with sh.irq(m, 3, "wait"): m.next = ...``; the body runs in the cycle the instruction completes.

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module of the design, inside the FSM state that does the irq.
        flag : int
            0 to 7.
        rel : bool
            The ``rel`` form.

        Returns
        -------
        object
            A context manager.
        """
        irqm = self._irq(m)
        flag = self._flag_number(flag)
        go = self._flag(("irq_go", flag), self.irq_pend & ~irqm.flag[flag], (IRQ_WAIT,), "both")

        def emit():
            self._mark(m, IRQ_WAIT, flag | bool(rel) << 3)
            m.d.sync += [irqm.set_tog[flag].eq(irqm.set_tog[flag] ^ ~self.irq_pend), self.irq_pend.eq(~go)]
        return _Op(self, m, go, emit, False, _caller())

    def wait_irq(self, m, flag, polarity=1, rel=False):
        """`wait polarity irq flag`: stall until IRQ flag `flag` has the level `polarity`; ``wait 1 irq`` also clears the flag when it passes. Use as
        ``with sh.wait_irq(m, 3): m.next = ...``.

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module of the design, inside the FSM state that does the wait.
        flag : int
            0 to 7.
        polarity : int
            0 or 1.
        rel : bool
            The ``rel`` form.

        Returns
        -------
        object
            A context manager; its body runs in the cycle the wait is over.
        """
        irqm = self._irq(m)
        flag = self._flag_number(flag)
        if polarity not in (0, 1):
            raise ValueError("the polarity of wait irq is 0 or 1, not %r" % (polarity,))
        go = self._flag(("wait_irq", flag, polarity), irqm.flag[flag] == polarity, (WAIT_IRQ,), "body")

        def emit():
            self._mark(m, WAIT_IRQ, flag | bool(rel) << 3 | polarity << 4)
            if polarity:
                m.d.sync += irqm.clr_tog[flag].eq(~irqm.clr_tog[flag])          # wait 1 irq clears the flag when it passes
        return _Op(self, m, go, emit, True, _caller())


UNSUPPORTED = {
    "pindirs": "pass d = platform.directions(buffer) as the destination, which names the pin group (design note 2f)",
    "pc": "a computed jump: an FSM state cannot jump to an address that is data",
    "exec": "an executed instruction taken from data is not an FSM state",
}
