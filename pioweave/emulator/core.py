# Copyright (c) 2026 youprint. MIT licence (see ../../LICENSE.txt)
"""An emulator for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354, in Python.

One `PioBlock` has a shared 32-word instruction memory, four state machines, the eight IRQ flags and the GPIO side of the block (levels, directions, inputs). It executes
the instruction set cycle by cycle as the datasheets describe it (RP2040 datasheet chapter 3, RP2350 datasheet chapter 11): stalls, delays, side-set (applied in the first
cycle of an instruction, after its own pin write), wrapping, autopush and autopull with thresholds, PUSH IFFULL / PULL IFEMPTY, the FIFO joins, EXEC, forced instructions,
IRQ flags (set wins over clear in the same cycle), and on PIO version 1 the extra instructions (WAIT JMPPIN, MOV PINDIRS, MOV to and from the RX FIFO registers, IRQ PREV / NEXT
for several blocks through `PioBlock.neighbours`, IN with a count, MOV status from the IRQ flags).

    block = PioBlock(version=0)
    loaded = block.load(program)                    # a pioweave.builder.Program, or `load(words, side=...)`
    sm = block.sm[0]
    sm.configure(program=loaded, set_base=0, set_count=1, clkdiv=1)
    sm.enable()                                     # a state machine runs only when enabled
    sm.put(0x55)                                    # a word into the TX FIFO
    block.run(100)                                  # 100 system clock cycles
    sm.get()                                        # a word out of the RX FIFO, or None

Sources: the RP2040 datasheet (build-date 2025-02-20, build-version 3184e62-clean, chapter 3) and the RP2350 datasheet (Release 8, build date 01/10/2026, build version
80d627281ed5, chapter 11), cited as "RP2040 p.NNN" and "RP2350 p.NNN" with the printed page numbers of the PDF (the page numbers of the PDF viewer are one higher). Facts still
marked [verify] are not stated in these documents. Nothing here has run on a chip; the emulator is checked against the PIO simulator of bitweave
(tests/test_emulator_differential.py) and against the Amaranth designs of pioweave (tests/test_compile.py).
"""
from collections import deque

__all__ = ["MASK32", "EmulationError", "Fifo", "Config", "StateMachine", "Loaded", "PioBlock"]

MASK32 = 0xFFFFFFFF


def _mask(n):
    return MASK32 if n >= 32 else (1 << n) - 1


class EmulationError(Exception):
    """The program does something the hardware leaves undefined or reserved, or reads empty instruction memory.

    Raised while a state machine runs (`PioBlock.step`, `PioBlock.run`, and `run_case` unless called with ``on_error="stop"``) and by `PioBlock.load` when the
    program does not fit.
    """


class Fifo:
    """A FIFO of 32-bit words.

    Parameters
    ----------
    depth : int
        The number of words it holds.

    Attributes
    ----------
    depth : int
        The number of words it holds.
    q : collections.deque
        The words, oldest first.
    """

    def __init__(self, depth):
        self.depth = depth
        self.q = deque()

    def __len__(self):
        return len(self.q)

    def full(self):
        """True when the FIFO holds `depth` words."""
        return len(self.q) >= self.depth

    def push(self, word):
        """Append a word (masked to 32 bits).

        Parameters
        ----------
        word : int
            The word.

        Returns
        -------
        bool
            False when the FIFO is full and the word was not stored.
        """
        if self.full():
            return False
        self.q.append(word & MASK32)
        return True

    def pop(self):
        """Remove the oldest word.

        Returns
        -------
        int or None
            The word, or None when the FIFO is empty.
        """
        return self.q.popleft() if self.q else None


class Config:
    """The configuration of one state machine, with the names of the registers it comes from.

    `StateMachine.configure` sets the fields; the reset values are those of the constructor.

    Attributes
    ----------
    in_base, in_count : int
        First pin of the IN mapping and its width (32 at reset; IN_COUNT exists on PIO version 1 only, RP2350 Table 1053 p.858; `StateMachine.configure` takes 0 as 32, the register encoding).
    out_base, out_count : int
        First pin of the OUT mapping and its width (0 at reset, RP2040 Table 386 p.378).
    set_base, set_count : int
        First pin of the SET mapping and its width (the count is 5 at reset, RP2040 Table 386 p.378, RP2350 Table 1056 p.859).
    side_base : int
        First pin of the side-set mapping.
    jmp_pin : int
        The pin tested by ``jmp pin`` and used by ``wait jmppin``.
    in_right, out_right : bool
        Shift direction of the ISR and OSR (right at reset: IN_SHIFTDIR and OUT_SHIFTDIR reset to 0x1, RP2040 Table 383 p.377, RP2350 Table 1053 p.857).
    autopush, autopull : bool
        Automatic push and pull.
    push_thresh, pull_thresh : int
        The thresholds, 1 to 32 (the register value 0 means 32 and is the reset value, RP2040 Table 383 p.377).
    wrap_bottom, wrap_top : int
        The wrap range, as absolute instruction addresses.
    side_bits, side_opt, side_pindirs : int, bool, bool
        The side-set field: bit count (including the enable bit of an optional side-set), optional, and whether it drives pin directions.
    status_sel : str
        The ``mov status`` source: ``"tx"`` (all-ones if the TX FIFO level is below `status_n`), ``"rx"`` (the RX FIFO level), or on PIO version 1 ``"irq"`` (all-ones if the
        indexed IRQ flag is raised). RP2040 Table 382 p.376, RP2350 Table 1052 p.856.
    status_n : int
        The level, or the IRQ flag: 0 to 7 for a flag of this block, 8 to 15 for a flag of the next lower-numbered block, 16 to 23 for the next higher-numbered block (RP2350
        Table 1052 p.856).
    fifo_join : str
        ``"none"``, ``"tx"`` (TX FIFO 8 deep, RX FIFO unavailable), ``"rx"`` (the reverse), ``"both"`` (both unavailable: FJOIN_TX and FJOIN_RX set, RP2040 3.5.3 p.335); on PIO
        version 1 also ``"rx_put"``, ``"rx_get"`` or ``"rx_put_get"``.
    clkdiv : float
        The clock divider, 1.0 to 65536 in steps of 1/256 (1.0 at reset; the register field INT = 0 means 65536, RP2040 Table 381 p.376).
    out_sticky : bool
        EXECCTRL_OUT_STICKY: the most recent OUT / SET pin write is asserted again on every cycle (RP2040 Table 382 p.376; the exact behaviour is [verify]).
    inline_out_en : bool
        EXECCTRL_INLINE_OUT_EN: bit `out_en_sel` of the OUT data is an auxiliary write enable; with `out_sticky` a write with enable 0 deasserts the latest pin write
        (same table; the exact behaviour is [verify]).
    out_en_sel : int
        EXECCTRL_OUT_EN_SEL: the data bit used as the inline OUT enable.
    """
    __slots__ = ("in_base", "in_count", "out_base", "out_count", "set_base", "set_count", "side_base", "jmp_pin", "in_right", "out_right", "autopush", "autopull", "push_thresh",
                 "pull_thresh", "wrap_bottom", "wrap_top", "side_bits", "side_opt", "side_pindirs", "status_sel", "status_n", "fifo_join", "clkdiv", "out_sticky", "inline_out_en", "out_en_sel")

    def __init__(self):
        self.in_base = self.out_base = self.set_base = self.side_base = self.jmp_pin = 0
        self.in_count = 32
        self.out_count = 0
        self.set_count = 5                               # PINCTRL_SET_COUNT resets to 5 (RP2040 Table 386 p.378)
        self.in_right = self.out_right = True            # IN_SHIFTDIR and OUT_SHIFTDIR reset to 1 = right (RP2040 Table 383 p.377)
        self.autopush = self.autopull = False
        self.push_thresh = self.pull_thresh = 32
        self.wrap_bottom, self.wrap_top = 0, 31
        self.side_bits, self.side_opt, self.side_pindirs = 0, False, False
        self.status_sel, self.status_n = "tx", 0
        self.fifo_join = "none"                          # none, tx, rx; version 1 also rx_put, rx_get, rx_put_get
        self.clkdiv = 1.0
        self.out_sticky = self.inline_out_en = False
        self.out_en_sel = 0


class StateMachine:
    """One state machine of a `PioBlock`, with its registers, FIFOs and `Config`.

    The host side (`configure`, `enable`, `put`, `get`, `force`, `set_pc`, `reset`) mirrors what a program on the RP2040 or RP2350 does through the SDK; the instruction
    execution is internal and driven by `PioBlock.step`.

    Parameters
    ----------
    block : PioBlock
        The block the state machine belongs to.
    index : int
        Its number, 0 to 3.

    Attributes
    ----------
    block : PioBlock
        The block.
    index : int
        The state machine number.
    cfg : Config
        The configuration.
    pc : int
        The program counter, an absolute address 0 to 31.
    x, y, isr, osr : int
        The scratch registers and the shift registers.
    isr_count, osr_count : int
        Bits shifted into the ISR and out of the OSR (the OSR is empty at 32).
    tx, rx : Fifo
        The FIFOs.
    rx_regs : list of int
        The four RX FIFO storage registers of PIO version 1.
    enabled : bool
        A state machine runs only after `enable`.
    executed, stall_cycles, delay_cycles : int
        Counters of executed instructions, stalled cycles and delay cycles.
    rx_lost, tx_dropped : int
        Counters of words lost by ``push noblock`` on a full RX FIFO and of words `put` dropped by a full TX FIFO.
    """

    def __init__(self, block, index):
        self.block, self.index = block, index
        self.cfg = Config()
        self.reset()

    # ------------------------------------------------------------------ host side
    def reset(self):
        """Return the state machine to the power-on state of the model: PC, X, Y, ISR and OSR 0, ISR shift counter 0 and OSR shift counter 32 (RP2040 3.5.4 p.338: nothing
        shifted in, nothing left to shift out), FIFOs emptied, clock divider restarted, everything else cleared, the state machine disabled.

        The datasheets do not state a power-on value for X, Y, the ISR or the OSR (they have no reset value in the register list); the model starts them at 0, as the Amaranth
        reference simulation does, and the generated C and MicroPython projects clear them explicitly. `restart` is the SM_RESTART of the chip and does not do this."""
        self.pc = 0
        self.x = self.y = 0
        self.isr = self.osr = 0
        self.tx, self.rx = Fifo(4), Fifo(4)
        self.rx_regs = [0, 0, 0, 0]
        self.enabled = False                              # like the hardware, a state machine runs only after the host enabled it: sm.enable()
        self.executed = self.stall_cycles = self.delay_cycles = 0
        self.rx_lost = self.tx_dropped = 0
        self.last = ("-", 0, 0)
        self.restart()
        self.clkdiv_restart()
        self._apply_join()

    def restart(self):
        """The SM_RESTART of the chip (CTRL register, RP2040 p.372, RP2350 p.851-852; `pio_sm_restart` in the Pico SDK).

        Cleared: the input and output shift counters (ISR 0, OSR 32), the contents of the ISR, the delay counter, the waiting-on-IRQ state, any stalled instruction written to
        SMx_INSTR or run by OUT / MOV EXEC, and any pin write left asserted by OUT_STICKY. Not affected: the program counter, the contents of the OSR, the X and Y registers (the
        datasheet says so in so many words), the FIFOs, the enable and the clock divider (see `clkdiv_restart`).

        Returns
        -------
        StateMachine
            `self`.
        """
        self.isr = 0
        self.isr_count, self.osr_count = 0, 32
        self.delay_left = 0
        self.side_done = False
        self.in_shifted = False
        self.irq_set_done = False
        self.exec_word = None
        self.cur_forced = None
        self.cur_source = "exec"                          # where the latched instruction came from: "exec" (OUT / MOV EXEC) or "host" (SMx_INSTR)
        self.force_fresh = False                          # a host-forced instruction that has not been tried yet
        self.saved_flags = None
        self.sticky = {}                                  # OUT_STICKY: the most recent OUT / SET write per target (levels or directions)
        return self

    def clkdiv_restart(self):
        """Restart the clock divider (CLKDIV_RESTART; `pio_sm_clkdiv_restart` in the Pico SDK): the next period starts afresh.

        Returns
        -------
        StateMachine
            `self`.
        """
        self.cd_left = 0                                  # clock divider: system cycles left in the current division period (0 = not started)
        self.cd_total = 0                                 # clock divider: running total of the fractional part, in 1/256
        return self

    def _apply_join(self):
        """FIFO depths (TX, RX): 4 and 4; joined to 8 and 0 or 0 and 8, or 0 and 0 with both join bits (RP2040 3.5.3 p.335: "Setting both FJOIN_RX and FJOIN_TX makes both FIFOs
        unavailable"); with the RX FIFO registers of PIO version 1 the RX FIFO is disabled and its storage is four registers (RP2350 Table 1053 p.858), the TX FIFO is not mentioned
        there and keeps four words."""
        self.tx.depth, self.rx.depth = {"none": (4, 4), "tx": (8, 0), "rx": (0, 8), "both": (0, 0), "rx_put": (4, 0), "rx_get": (4, 0), "rx_put_get": (4, 0)}[self.cfg.fifo_join]

    def configure(self, program=None, **kw):
        """Set the configuration. `program`: what `PioBlock.load()` returned (wrap range and side-set are taken from it and the PC is set to its start). Keywords: in_base, in_count,
        out_base, out_count, set_base, set_count, side_base, jmp_pin, in_shift / out_shift ("right" or "left"), autopush, autopull, push_thresh, pull_thresh, status_sel,
        status_n, fifo_join, clkdiv, side_bits, side_opt, side_pindirs, wrap_bottom, wrap_top (absolute addresses), out_sticky, inline_out_en, out_en_sel. Changing
        `fifo_join` discards the words in both FIFOs, as the hardware does.

        Parameters
        ----------
        program : Loaded or None
            What `PioBlock.load` returned.
        **kw :
            The configuration keywords listed above (see `Config`).

        Returns
        -------
        StateMachine
            `self`.

        Raises
        ------
        TypeError
            If `program` is not a `Loaded`, or a keyword is unknown.
        ValueError
            If a direction, threshold, pin base, pin count or FIFO join is not valid, or a join needs PIO version 1, or autopush is combined with an RX FIFO PUT / GET join.
        """
        c = self.cfg
        join_before = c.fifo_join
        if program is not None:
            if not isinstance(program, Loaded):
                raise TypeError("program must be what PioBlock.load() returned")
            c.wrap_bottom, c.wrap_top = program.offset + program.wrap_target, program.offset + program.wrap
            c.side_bits, c.side_opt, c.side_pindirs = program.side_bits, program.side_opt, program.side_pindirs
            self.pc = program.offset
        for key, val in kw.items():
            if key in ("in_shift", "out_shift"):
                if val not in ("left", "right"):
                    raise ValueError("%s is 'left' or 'right'" % key)
                setattr(c, key.replace("shift", "right"), val == "right")
            elif key in Config.__slots__:
                setattr(c, key, val)
            else:
                raise TypeError("unknown configuration %r" % key)
        for key in ("push_thresh", "pull_thresh"):
            v = getattr(c, key)
            if not 1 <= v <= 32:
                raise ValueError("%s must be 1..32" % key)
        if c.fifo_join not in ("none", "tx", "rx", "both", "rx_put", "rx_get", "rx_put_get"):
            raise ValueError("fifo_join: none, tx, rx, both, rx_put, rx_get or rx_put_get")
        if not 0 <= c.in_count <= 32:
            raise ValueError("in_count is 0 to 32")
        if c.in_count == 0:                       # RP2350 Table 1053 p.857-858: "A count of 32 is encoded with a field value of 0"
            c.in_count = 32
        if c.status_sel not in ("tx", "rx", "irq"):
            raise ValueError("status_sel: tx, rx or irq")
        if c.status_sel == "irq" and self.block.version < 1:
            raise ValueError("MOV status from an IRQ flag is a PIO version 1 feature")
        if not 1.0 <= c.clkdiv <= 65536.0:
            raise ValueError("clkdiv is 1 to 65536")
        for key, low, high in (("in_base", 0, 31), ("out_base", 0, 31), ("set_base", 0, 31), ("side_base", 0, 31), ("jmp_pin", 0, 31), ("in_count", 0, 32), ("out_count", 0, 32),
                               ("set_count", 0, 5), ("side_bits", 0, 5), ("status_n", 0, 23)):
            v = getattr(c, key)
            if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
                raise ValueError("%s must be a whole number from %d to %d, not %r" % (key, low, high, v))
        if not 0 <= c.out_en_sel <= 31:
            raise ValueError("out_en_sel must be 0..31")
        if c.fifo_join in ("rx_put", "rx_get", "rx_put_get") and self.block.version < 1:
            raise ValueError("the RX FIFO register join is a PIO version 1 feature")
        if c.autopush and c.fifo_join in ("rx_put", "rx_get", "rx_put_get"):
            raise ValueError("autopush must not be enabled with an RX FIFO PUT / GET join (undefined)")
        self._apply_join()
        if c.fifo_join != join_before:                      # "FIFOs are flushed when this bit is changed" (RP2040 Table 383 p.377, RP2350 Table 1053 p.857)
            self.tx.q.clear()
            self.rx.q.clear()
        return self

    def enable(self):
        """Start the state machine (SM_ENABLE). Returns `self`."""
        self.enabled = True
        return self

    def disable(self):
        """Stop the state machine (SM_ENABLE cleared). Returns `self`."""
        self.enabled = False
        return self

    def set_pc(self, address):
        """Set the program counter (absolute address, masked to 5 bits). Returns `self`."""
        self.pc = address & 31
        return self

    def put(self, word):
        """Write the TX FIFO as the host does.

        Parameters
        ----------
        word : int
            The word.

        Returns
        -------
        bool
            False (and counted in `tx_dropped`) when the FIFO is full.
        """
        if self.tx.push(word):
            return True
        self.tx_dropped += 1
        return False

    def get(self):
        """Read the RX FIFO as the host does.

        Returns
        -------
        int or None
            The oldest word, or None when the FIFO is empty.
        """
        return self.rx.pop()

    def force(self, word):
        """Execute `word` in the next cycle instead of the program (writing SMx_INSTR). As the datasheets say (RP2040 3.5.7 p.343-344, RP2350 11.5.7 p.825): the instruction executes
        immediately, "ignoring the state machine clock divider" and whether or not the state machine is enabled (it is how the SDK sets a state machine up), its delay cycles
        are ignored, and the program counter does not advance, so the program continues where it left off unless the forced instruction jumps. A forced instruction may stall; it is
        then latched and retried on the cycles in which the state machine runs, until it completes (EXECCTRL_EXEC_STALLED), and it shares the latch of an OUT / MOV EXEC instruction (RP2040 3.5.7 p.344 CAUTION: a forced instruction that stalls overwrites the executee in flight). The model runs a forced instruction that completes
        first and then still runs the pending executee; whether the chip does is not stated in the datasheet [verify on a chip]. Its side-set is applied like any other [verify].

        Parameters
        ----------
        word : int
            A 16-bit instruction word.

        Returns
        -------
        StateMachine
            `self`.
        """
        self.cur_forced = word & 0xFFFF
        self.cur_source = "host"
        self.force_fresh = True
        return self

    # ------------------------------------------------------------------ helpers
    def _in_pins(self, view):
        n = self.cfg.in_base & 31
        v = ((view >> n) | (view << (32 - n))) & MASK32 if n else view
        return (v & _mask(self.cfg.in_count)) if self.block.version >= 1 else v

    def _flag(self, idx):
        flag = idx & 7
        if self.block.version == 0:
            return (flag & 4) | ((flag + self.index) & 3) if idx & 0x10 else flag, 0
        mode = (idx >> 3) & 3
        if mode == 0:
            return flag, 0
        if mode == 2:
            return (flag & 4) | ((flag + self.index) & 3), 0
        return flag, 1 if mode == 3 else -1               # 1: NEXT block, -1: PREV block (the neighbour blocks, see PioBlock.neighbours)

    def _shift_in(self, data, n):
        d = data & _mask(n)
        if self.cfg.in_right:
            self.isr = ((self.isr >> n) | (d << (32 - n))) & MASK32
        else:
            self.isr = ((self.isr << n) | d) & MASK32
        self.isr_count = min(self.isr_count + n, 32)

    def _shift_out(self, n):
        if self.cfg.out_right:
            data, rest = self.osr & _mask(n), self.osr >> n
        else:
            data, rest = (self.osr >> (32 - n)) & _mask(n), (self.osr << n) & MASK32
        self.osr = rest & MASK32
        self.osr_count = min(self.osr_count + n, 32)
        return data

    def _status(self):
        c = self.cfg
        if c.status_sel == "irq":
            mode, flag = (c.status_n >> 3) & 3, c.status_n & 7           # 0: this block, 1: next lower-numbered block, 2: next higher-numbered (RP2350 Table 1052 p.856)
            if mode == 3:
                raise EmulationError("STATUS_N 24 to 31 is reserved for an IRQ status")
            block = self.block if mode == 0 else self.block.neighbour(-1 if mode == 1 else 1)
            if block is None:
                raise EmulationError("MOV status refers to an IRQ flag of another PIO block that is not part of this emulation")
            on = bool(block.irq & (1 << flag))
        elif c.status_sel == "rx":
            on = len(self.rx) < c.status_n        # [verify] status_n above the FIFO depth is accepted here (the RP2350 datasheet calls such values reserved)
        else:
            on = len(self.tx) < c.status_n
        return MASK32 if on else 0

    def _background_pull(self):
        c = self.cfg
        if c.autopull and self.osr_count >= c.pull_thresh and len(self.tx):
            self.osr, self.osr_count = self.tx.pop(), 0

    def _write(self, stage, base, count, value, dirs=False):
        for j in range(min(count, 32)):
            stage.append((dirs, (base + j) & 31, (value >> j) & 1))

    def _pin_write(self, ctx, base, count, value, dirs=False, inline=False):
        """An OUT, SET or MOV write to the pins or pin directions. With EXECCTRL_INLINE_OUT_EN an OUT write whose enable bit (bit OUT_EN_SEL of the data) is 0 does not happen, and with
        EXECCTRL_OUT_STICKY it also deasserts the latest write; with OUT_STICKY the write is remembered and asserted again on every cycle (RP2040 Table 382 p.376; details [verify])."""
        c = self.cfg
        if inline and c.inline_out_en and not (value >> c.out_en_sel) & 1:
            self.sticky.pop(dirs, None)
            return
        self._write(ctx.writes, base, count, value, dirs)
        if c.out_sticky:
            self.sticky[dirs] = (base, count, value)

    def _side_set(self, word, stage):
        c = self.cfg
        n = c.side_bits
        if n == 0:
            return
        field = (word >> 8) & 0x1F
        delay_bits = max(5 - n, 0)
        if c.side_opt:
            value_bits, enabled = n - 1, bool((field >> 4) & 1)
        else:
            value_bits, enabled = n, True
        if not enabled:
            return
        self._write(stage, c.side_base, value_bits, (field >> delay_bits) & _mask(value_bits), c.side_pindirs)

    def _delay_of(self, word):
        return ((word >> 8) & 0x1F) & _mask(max(5 - self.cfg.side_bits, 0))

    # ------------------------------------------------------------------ one instruction
    def _exec(self, word, view, ctx):
        """Execute one word. Returns (status, jump) with status 'done' or 'stall'; pin writes go to ctx.writes, IRQ changes to ctx.irq_set / ctx.irq_clear."""
        c, blk = self.cfg, self.block
        first = not self.side_done      # [verify] side-set is applied once per instruction, not again on each retry of a stalled one (the datasheet says: on the first cycle)
        op, a = word >> 13, word & 0xFF
        is_out = False
        jump = None
        res = "done"
        if op == 0:                                                          # JMP
            addr, cond = a & 31, (a >> 5) & 7
            if cond == 0:
                taken = True
            elif cond == 1:
                taken = self.x == 0
            elif cond == 2:
                taken = self.x != 0
                self.x = (self.x - 1) & MASK32
            elif cond == 3:
                taken = self.y == 0
            elif cond == 4:
                taken = self.y != 0
                self.y = (self.y - 1) & MASK32
            elif cond == 5:
                taken = self.x != self.y
            elif cond == 6:
                taken = bool((view >> (c.jmp_pin & 31)) & 1)
            else:
                taken = self.osr_count < c.pull_thresh
            jump = addr if taken else None
        elif op == 1:                                                        # WAIT
            pol, src, idx = (a >> 7) & 1, (a >> 5) & 3, a & 31
            if src == 0:
                level = (view >> idx) & 1
            elif src == 1:
                level = (self._in_pins(view) >> idx) & 1
            elif src == 2:
                f, other = self._flag(idx)
                if other:
                    nb = blk.neighbour(other)
                    if nb is None:
                        raise EmulationError("IRQ PREV / NEXT refers to another PIO block that is not part of this emulation")
                    level = (nb.irq >> f) & 1
                    if level == pol == 1:
                        ctx.irq_clear_other.append((nb, f))
                else:
                    level = (blk.irq >> f) & 1
                    if level == pol == 1:
                        ctx.irq_clear.add(f)
            else:
                if blk.version == 0:
                    raise EmulationError("WAIT source 11 is reserved on PIO version 0")
                if idx > 3:
                    raise EmulationError("WAIT JMPPIN index above 3 is reserved")
                level = (view >> ((c.jmp_pin + idx) & 31)) & 1
            res = "done" if level == pol else "stall"
        elif op == 2:                                                        # IN
            n = 32 if a & 31 == 0 else a & 31
            # [verify on a chip] a stalled IN with autopush (RX FIFO full) is modelled as shifting once and retrying only the push; the datasheet pseudocode (RP2040 3.5.4.1 p.338)
            # does not say whether the retry shifts again (audit COR-09)
            if not self.in_shifted:
                s = (a >> 5) & 7
                if s == 0:
                    data = self._in_pins(view)
                elif s == 1:
                    data = self.x
                elif s == 2:
                    data = self.y
                elif s == 3:
                    data = 0
                elif s == 6:
                    data = self.isr
                elif s == 7:
                    data = self.osr
                else:
                    raise EmulationError("IN source 100 / 101 is reserved")
                self._shift_in(data, n)
                self.in_shifted = True
            if c.autopush and self.isr_count >= c.push_thresh:
                if not self.rx.full():
                    self.rx.push(self.isr)
                    self.isr, self.isr_count, self.in_shifted = 0, 0, False
                else:
                    res = "stall"
            else:
                self.in_shifted = False
        elif op == 3:                                                        # OUT
            is_out = True
            n = 32 if a & 31 == 0 else a & 31
            if c.autopull and self.osr_count >= c.pull_thresh:
                if len(self.tx):
                    self.osr, self.osr_count = self.tx.pop(), 0
                res = "stall"
            else:
                data = self._shift_out(n)
                d = (a >> 5) & 7
                if d == 0:
                    self._pin_write(ctx, c.out_base, c.out_count, data, False, True)
                elif d == 1:
                    self.x = data
                elif d == 2:
                    self.y = data
                elif d == 4:
                    self._pin_write(ctx, c.out_base, c.out_count, data, True, True)
                elif d == 5:
                    jump = data & 31
                elif d == 6:
                    self.isr, self.isr_count = data, n
                elif d == 7:
                    self.exec_word = data & 0xFFFF
                self._background_pull()
        elif op == 4:                                                        # PUSH / PULL (and MOV to / from the RX FIFO registers on version 1)
            if blk.version >= 1 and word & 0x10 and (word >> 5) & 3 == 0:       # bits 7:4 = x001 (RP2350 Table 1027); Index is bits 2:0, IdxI bit 3
                if not word & 8 and word & 7:
                    raise EmulationError("MOV rxfifo[y]: a non-zero Index with IdxI clear is a reserved encoding (RP2350 11.4.8 p.809)")
                idx = (word & 3) if word & 8 else (self.y & 3)
                if not word & 0x80:
                    if c.fifo_join not in ("rx_put", "rx_put_get"):
                        raise EmulationError("MOV rxfifo[..], isr needs fifo_join = rx_put or rx_put_get (undefined otherwise)")
                    self.rx_regs[idx] = self.isr
                else:
                    if c.fifo_join not in ("rx_get", "rx_put_get"):
                        raise EmulationError("MOV osr, rxfifo[..] needs fifo_join = rx_get or rx_put_get (undefined otherwise)")
                    self.osr, self.osr_count = self.rx_regs[idx], 0
            elif c.fifo_join in ("rx_put", "rx_get", "rx_put_get"):
                raise EmulationError("PUSH / PULL are undefined while an RX FIFO PUT / GET join is active")
            elif not word & 0x80:                                            # PUSH
                iff, blk_ = bool((a >> 6) & 1), bool((a >> 5) & 1)
                if iff and self.isr_count < c.push_thresh:
                    pass
                elif not self.rx.full():
                    self.rx.push(self.isr)
                    self.isr, self.isr_count = 0, 0
                elif blk_:
                    res = "stall"
                else:
                    self.isr, self.isr_count = 0, 0
                    self.rx_lost += 1
            else:                                                            # PULL
                ife, blk_ = bool((a >> 6) & 1), bool((a >> 5) & 1)
                if (c.autopull and self.osr_count == 0) or (ife and self.osr_count < c.pull_thresh):
                    pass
                elif len(self.tx):
                    self.osr, self.osr_count = self.tx.pop(), 0
                elif blk_:
                    res = "stall"
                else:
                    self.osr, self.osr_count = self.x, 0
        elif op == 5:                                                        # MOV
            s = a & 7
            if s == 0:
                src = self._in_pins(view)
            elif s == 1:
                src = self.x
            elif s == 2:
                src = self.y
            elif s == 3:
                src = 0
            elif s == 5:
                src = self._status()
            elif s == 6:
                src = self.isr
            elif s == 7:
                src = self.osr
            else:
                raise EmulationError("MOV source 100 is reserved")
            o = (a >> 3) & 3
            if o == 0:
                val = src
            elif o == 1:
                val = ~src & MASK32
            elif o == 2:
                val = int(format(src, "032b")[::-1], 2)
            else:
                raise EmulationError("MOV operation 11 is reserved")
            d = (a >> 5) & 7
            if d == 0:
                self._pin_write(ctx, c.out_base, c.out_count, val)
            elif d == 1:
                self.x = val
            elif d == 2:
                self.y = val
            elif d == 3:
                if blk.version == 0:
                    raise EmulationError("MOV PINDIRS is not supported on PIO version 0")
                self._pin_write(ctx, c.out_base, c.out_count, val, True)
            elif d == 4:
                self.exec_word = val & 0xFFFF
            elif d == 5:
                jump = val & 31
            elif d == 6:
                self.isr, self.isr_count = val, 0
            else:
                self.osr, self.osr_count = val, 0
        elif op == 6:                                                        # IRQ
            clr, wait = bool((a >> 6) & 1), bool((a >> 5) & 1)
            f, other = self._flag(a & 31)
            if other:
                nb = blk.neighbour(other)
                if nb is None:
                    raise EmulationError("IRQ PREV / NEXT refers to another PIO block that is not part of this emulation")
                flags, set_, clear = nb.irq, (lambda fl, nb=nb: ctx.irq_set_other.append((nb, fl))), (lambda fl, nb=nb: ctx.irq_clear_other.append((nb, fl)))
            else:
                flags, set_, clear = blk.irq, ctx.irq_set.add, ctx.irq_clear.add
            if clr:
                clear(f)
            else:
                just_set = not self.irq_set_done
                if just_set:
                    set_(f)
                    self.irq_set_done = True
                if wait and (just_set or (flags >> f) & 1):      # IRQ WAIT: stall until the flag has been cleared (by another state machine or the host)
                    res = "stall"
                else:
                    self.irq_set_done = False
        else:                                                                # SET
            data, d = a & 31, (a >> 5) & 7
            if d == 0:
                self._pin_write(ctx, c.set_base, c.set_count, data)
            elif d == 1:
                self.x = data
            elif d == 2:
                self.y = data
            elif d == 4:
                self._pin_write(ctx, c.set_base, c.set_count, data, True)
            else:
                raise EmulationError("SET destination %s is reserved" % format(d, "03b"))
        if first:
            self._side_set(word, ctx.writes)
            self.side_done = True
        return res, jump, is_out

    def _step(self, view, ctx):
        """One state machine clock cycle."""
        blk = self.block
        if self.cur_forced is not None:                                    # a latched instruction: a forced one that stalled, or the executee of OUT / MOV EXEC
            word, source = self.cur_forced, self.cur_source
            self.cur_forced = None
        elif self.delay_left > 0:
            self.delay_left -= 1
            self.delay_cycles += 1
            self.last = ("d", 0, self.pc)
            self._background_pull()
            return
        elif self.exec_word is not None:
            word, source = self.exec_word, "exec"
            self.exec_word = None
        else:
            word, source = blk.mem[self.pc & 31], "mem"
            if word is None:
                raise EmulationError("state machine %d: pc %d executes empty instruction memory" % (self.index, self.pc))
        self._run(word, source, view, ctx)

    def _step_forced(self, view, ctx):
        """The cycle in which a word written to SMx_INSTR executes: it replaces the instruction the state machine would run (if any), whether or not the state machine is enabled
        and whatever its clock divider says."""
        word, self.cur_forced = self.cur_forced, None
        self.force_fresh = False
        if self.saved_flags is None:                                       # the interrupted instruction keeps its progress (side-set done, IN done, IRQ set)
            self.saved_flags = (self.side_done, self.in_shifted, self.irq_set_done)
        self.side_done = self.in_shifted = self.irq_set_done = False
        self._run(word, "host", view, ctx)

    def _run(self, word, source, view, ctx):
        c = self.cfg
        pc_before = self.pc
        res, jump, is_out = self._exec(word, view, ctx)
        self.last = ("s" if res == "stall" else ("f" if source != "mem" else "e"), word, pc_before)
        if res == "stall":
            self.stall_cycles += 1
            if source != "mem":
                self.cur_forced, self.cur_source = word, source            # latched until it completes (EXEC_STALLED)
        else:
            self.executed += 1
            self.side_done = False
            if source == "host":
                self.in_shifted = self.irq_set_done = False
                if self.saved_flags is not None:
                    self.side_done, self.in_shifted, self.irq_set_done = self.saved_flags
                    self.saved_flags = None
            else:
                # delays of OUT EXEC / MOV EXEC are ignored: the executee's own applies (RP2040 3.4.5.2 p.326); those of a forced instruction are ignored too (3.5.7 p.344)
                self.delay_left = 0 if self.exec_word is not None else self._delay_of(word)
            if jump is not None:
                self.pc = jump & 31
            elif source == "mem":
                self.pc = c.wrap_bottom if self.pc == c.wrap_top else (self.pc + 1) & 31
        if not is_out:
            self._background_pull()

    def _clock_enable(self):
        """The fractional clock divider: True in the system clock cycles in which the state machine advances. Following RP2040 3.5.5 p.339-340 (RP2350 11.5.5 p.821-822): the divider
        counts a division period of n system clock cycles and emits an enable pulse at its end; it keeps a running total of the fractional part (first-order delta-sigma) of each
        period, and every time this total wraps through 1 the next period is n + 1 cycles. The average rate is 1 / (n + f/256). The datasheet does not say where the first period
        starts after the state machine is enabled; here a period starts on the first enabled cycle (with a divisor of 1 the enable is asserted on every cycle, Figure 45) [verify]."""
        d = max(int(round(self.cfg.clkdiv * 256)), 256)
        n, f = d >> 8, d & 255
        if self.cd_left <= 0:
            self.cd_left = n
        self.cd_left -= 1
        if self.cd_left > 0:
            return False
        self.cd_total += f
        wrapped = self.cd_total >= 256
        self.cd_total &= 255
        self.cd_left = n + 1 if wrapped else n
        return True

    def snapshot(self):
        """The state after the last cycle, as recorded in the trace of `run_case` with ``record=True``.

        Returns
        -------
        dict
            The keys ``sm``, ``kind`` (``"e"`` executed, ``"s"`` stalled, ``"f"`` forced, ``"d"`` delay, ``"-"`` none yet), ``word``, ``pc_at``, ``pc``, ``x``, ``y``,
            ``isr``, ``isr_count``, ``osr``, ``osr_count``, ``tx`` and ``rx`` (the FIFO contents as lists).
        """
        return {"sm": self.index, "kind": self.last[0], "word": self.last[1], "pc_at": self.last[2], "pc": self.pc, "x": self.x, "y": self.y, "isr": self.isr,
                "isr_count": self.isr_count, "osr": self.osr, "osr_count": self.osr_count, "tx": list(self.tx.q), "rx": list(self.rx.q)}


class _Ctx:
    """The effects of one system clock cycle that are applied together at its end."""

    def __init__(self):
        self.writes = []
        self.irq_set, self.irq_clear = set(), set()
        self.irq_set_other, self.irq_clear_other = [], []


class Loaded:
    """A program placed in the instruction memory: where it is, its wrap range and side-set (program-relative), as returned by `PioBlock.load`.

    Pass it to `StateMachine.configure` as ``program``.

    Attributes
    ----------
    offset : int
        The load address.
    length : int
        The number of instructions.
    wrap_target, wrap : int
        The wrap range, program-relative.
    side_bits : int
        The side-set field width including the enable bit of an optional side-set.
    side_opt, side_pindirs : bool
        Optional side-set; side-set drives pin directions.
    """

    def __init__(self, offset, length, wrap_target, wrap, side_bits, side_opt, side_pindirs):
        self.offset, self.length, self.wrap_target, self.wrap = offset, length, wrap_target, wrap
        self.side_bits, self.side_opt, self.side_pindirs = side_bits, side_opt, side_pindirs

    def __int__(self):
        return self.offset


class PioBlock:
    """One PIO block: four state machines around a shared instruction memory, the IRQ flags and the GPIO side. Pin numbers of the block are those of its 32-pin window; `gpio_base`
    (0 or 16, RP2350B) maps them to GPIO numbers: GPIO = pin + gpio_base.

    Parameters
    ----------
    version : int
        The PIO version: 0 (RP2040) or 1 (RP2350, RP2354).
    gpio_base : int
        0, or 16 on PIO version 1 (the 48-GPIO parts).
    sync_cycles : int
        Cycles of the input synchroniser: 2 by default, a "standard 2-flipflop synchroniser" on each GPIO input that is engaged unless the INPUT_SYNC_BYPASS bit of the GPIO is
        set (RP2040 3.5.6.3 p.342 and Table 376 p.374, RP2350 11.5.6.3 p.824); 0 bypasses it for every GPIO.
    loopback : str
        How a state machine sees its own block's outputs. ``"direct"`` (the default, the convention of bitweave's simulator and of pioweave's Amaranth simulation): an instruction
        sees the pin levels written by the previous cycle's instruction. ``"datasheet"``: "a state machine observing its own outputs is subject to ... a two-cycle delay when
        bypassing synchronisers, a four-cycle delay when synchronisers are engaged" (RP2040 3.5.6.1 p.342); the extra cycles are `sync_cycles` + 1. The RP2350 datasheet does not
        repeat the statement ("improved GPIO input/output delay and skew", 11.1.1 p.793), so for version 1 the numbers are [verify].

    Attributes
    ----------
    version, gpio_base, sync_cycles, loopback :
        The arguments as given.
    sync_bypass : int
        INPUT_SYNC_BYPASS: one bit per pin of the window; a pin whose bit is 1 bypasses the synchroniser (0, all synchronised, at reset).
    mem : list
        The 32 words of the instruction memory (None for an empty word).
    irq : int
        The eight IRQ flags as a bit mask.
    dirs : int
        The pin directions of the window, one bit per pin (1 is an output).
    ext : int
        What the outside world drives, one bit per pin; pins the block drives as outputs are not affected.
    sm : list of StateMachine
        The four state machines.
    neighbours : dict
        ``{-1: previous block, 1: next block}`` for IRQ PREV / NEXT (PIO version 1); None where there is none.
    cycle : int
        The number of system clock cycles run.
    trace : list of dict
        The recorded cycles when `recording` is true.
    recording : bool
        Record a trace entry per cycle.

    Raises
    ------
    ValueError
        If `version` or `gpio_base` is not valid.

    Examples
    --------
    >>> from pioweave.builder import Program, PINS
    >>> from pioweave.emulator import PioBlock
    >>> p = Program("square")
    >>> with p.wrapped():
    ...     p.set(PINS, 1, delay=1)
    ...     p.set(PINS, 0, delay=1)
    >>> block = PioBlock(version=0)
    >>> sm = block.sm[0]
    >>> sm = sm.configure(program=block.load(p), set_base=0, set_count=1).enable()
    >>> block.dirs = 1
    >>> levels = []
    >>> for _ in range(8):
    ...     block.step()
    ...     levels.append(block.pads() & 1)
    >>> levels
    [1, 1, 0, 0, 1, 1, 0, 0]
    """

    def __init__(self, version=0, gpio_base=0, sync_cycles=2, loopback="direct"):
        if loopback not in ("direct", "datasheet"):
            raise ValueError("loopback is 'direct' or 'datasheet'")
        if version not in (0, 1):
            raise ValueError("version is 0 (RP2040) or 1 (RP2350, RP2354)")
        if gpio_base not in (0, 16) or (gpio_base and version == 0):
            raise ValueError("gpio_base is 0, or 16 on PIO version 1 (the 48-GPIO parts)")
        if isinstance(sync_cycles, bool) or not isinstance(sync_cycles, int) or not 0 <= sync_cycles <= 64:
            raise ValueError("sync_cycles is a whole number from 0 to 64, not %r" % (sync_cycles,))
        self.version, self.gpio_base = version, gpio_base
        self.sync_cycles = sync_cycles                    # 2 = the input synchroniser of the hardware (RP2040 3.5.6.3 p.342); 0 = bypass
        self.loopback = loopback
        self.sync_bypass = 0                              # INPUT_SYNC_BYPASS, one bit per pin
        self._own_history = []
        self.mem = [None] * 32
        self.irq = 0
        self.out_level = 0
        self.dirs = 0
        self.ext = 0                                      # what the outside world drives, bit per pin (pins the block drives as outputs are not affected)
        self._ext_history = [0] * (self.sync_cycles + 1)
        self.sm = [StateMachine(self, i) for i in range(4)]
        self.neighbours = {-1: None, 1: None}             # the PREV / NEXT block, for IRQ PREV / NEXT (PIO version 1)
        self.cycle = 0
        self.trace = []
        self.recording = False

    def neighbour(self, which):
        """The neighbouring block for IRQ PREV / NEXT.

        Parameters
        ----------
        which : int
            -1 for the previous block, 1 for the next one.

        Returns
        -------
        PioBlock or None
            The block set in `neighbours`, or None.
        """
        return self.neighbours.get(which)

    # ------------------------------------------------------------------ loading
    def load(self, program, offset=None, side=None):
        """Put a program into the instruction memory and return a `Loaded`. `program`: a pioweave.builder.Program (words, wrap and side-set are taken from it) or a list of 16-bit
        words (then give `side=(bits, optional, pindirs)` if it uses side-set; `bits` counts the enable bit of an optional side-set, as SIDESET_COUNT does). JMP targets are program-relative as pioasm writes them and are relocated by the load offset.
        `offset`: None = the program's .origin if it has one, else the first place with room.

        Parameters
        ----------
        program : pioweave.builder.Program or list of int
            The program, or its 16-bit words.
        offset : int or None
            The load address.
        side : tuple or None
            ``(bits, optional, pindirs)`` for a word list that uses side-set.

        Returns
        -------
        Loaded
            Where the program is and its wrap range and side-set.

        Raises
        ------
        ValueError
            If the program has no instruction or more than 32, a word is not a 16-bit integer, or `offset` is negative or not an integer.
        EmulationError
            If there is no room for the program.

        Examples
        --------
        >>> from pioweave.builder import Program, PINS
        >>> from pioweave.emulator import PioBlock
        >>> p = Program("square")
        >>> with p.wrapped():
        ...     p.set(PINS, 1, delay=1)
        ...     p.set(PINS, 0, delay=1)
        >>> block = PioBlock()
        >>> loaded = block.load(p)
        >>> (loaded.offset, loaded.length, loaded.wrap_target, loaded.wrap)
        (0, 2, 0, 1)
        >>> block.mem[:3]
        [57601, 57600, None]
        >>> block.load([0xE101]).offset
        2
        """
        if hasattr(program, "words"):
            words = program.words()
            wrap_target, wrap = program.wrap_range
            origin = getattr(program, "origin", None)
            side = (program.side_count + (1 if program.side_opt and program.side_count else 0), program.side_opt, program.side_pindirs)
        else:
            words, origin = list(program), None
            side = side or (0, False, False)
            wrap_target, wrap = 0, len(words) - 1
        n = len(words)
        if n == 0 or n > 32:
            raise ValueError("a program has 1 to 32 instructions")
        for w in words:
            if isinstance(w, bool) or not isinstance(w, int) or not 0 <= w <= 0x1FFFF:       # 16 bits, and the builder's flag in bit 16
                raise ValueError("an instruction is a 16-bit word (0 to 0xFFFF), not %r" % (w,))
        if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset):
            raise ValueError("the load offset is a whole number from 0, not %r" % (offset,))
        if offset is None:
            offset = origin
        if offset is None:
            offset = next((o for o in range(0, 33 - n) if all(self.mem[o + i] is None for i in range(n))), None)
            if offset is None:
                raise EmulationError("no room for %d instructions in the instruction memory" % n)
        if offset + n > 32:
            raise EmulationError("the program does not fit at offset %d" % offset)
        for i, w in enumerate(words):
            w &= 0xFFFF                                        # the builder's words() carry a flag in bit 16 for a WAIT on GPIO 32-47; the instruction is the low 16 bits
            if w >> 13 == 0:                                   # JMP: the target is relative to the program, add the load offset
                w = (w & ~31) | (((w & 31) + offset) & 31)
            elif w >> 13 == 1 and (w >> 5) & 3 == 0:           # WAIT GPIO carries the low five bits of the real GPIO number; with GPIO base 16 the SDK flips bit 4 when it loads
                w ^= self.gpio_base
            self.mem[offset + i] = w & 0xFFFF
        return Loaded(offset, n, wrap_target, wrap, side[0], bool(side[1]), bool(side[2]))

    def unload(self, loaded):
        """Free the instruction memory of a program.

        Parameters
        ----------
        loaded : Loaded
            What `load` returned.
        """
        for i in range(loaded.length):
            self.mem[loaded.offset + i] = None

    # ------------------------------------------------------------------ pins
    def set_input(self, pin, level):
        """Drive an input pin from outside (a pin of the window).

        Parameters
        ----------
        pin : int
            The pin number in the window (0 to 31).
        level : bool or int
            The level.

        Raises
        ------
        ValueError
            If `pin` is not 0 to 31.
        """
        if isinstance(pin, bool) or not isinstance(pin, int) or not 0 <= pin <= 31:
            raise ValueError("the pin is a number from 0 to 31 of the window, not %r" % (pin,))
        self.ext = (self.ext & ~(1 << pin)) | (int(bool(level)) << pin)

    def set_inputs(self, value):
        """Drive all input pins from outside.

        Parameters
        ----------
        value : int
            One bit per pin of the window.
        """
        self.ext = value & MASK32

    def pads(self):
        """What is on the pads (the window): own outputs where the pin is an output, the outside world elsewhere.

        Returns
        -------
        int
            One bit per pin of the window.
        """
        return (self.out_level & self.dirs) | (self.ext & ~self.dirs & MASK32)

    # ------------------------------------------------------------------ running
    def step(self):
        """One system clock cycle."""
        hist = self._ext_history
        hist.append(self.ext)
        ext_seen = (hist[-1 - self.sync_cycles] & ~self.sync_bypass) | (self.ext & self.sync_bypass)
        del hist[0:len(hist) - (self.sync_cycles + 1)]
        own = self._own_history
        own.append((self.out_level, self.dirs))
        level, dirs = own[max(len(own) - 1 - (self.sync_cycles + 1 if self.loopback == "datasheet" else 0), 0)]
        del own[0:max(len(own) - (self.sync_cycles + 2), 0)]
        view = (level & dirs) | (ext_seen & ~dirs & MASK32)
        ctx = _Ctx()
        for sm in self.sm:
            run = sm.enabled and sm._clock_enable()
            if sm.enabled:
                for is_dirs, (base, count, value) in sm.sticky.items():     # OUT_STICKY: the most recent OUT / SET is asserted again
                    sm._write(ctx.writes, base, count, value, is_dirs)
            if sm.force_fresh and sm.cur_forced is not None:
                sm._step_forced(view, ctx)
            elif run:
                sm._step(view, ctx)
        # apply the effects in state machine order, so that the highest-numbered state machine wins on a pin (levels and directions separately) when several write it in the same
        # cycle (RP2040 3.5.6.1 p.341-342, RP2350 11.5.6.1 p.823-824); within one state machine the side-set was staged after the OUT / SET and so wins (RP2040 3.5.1 p.332)
        for dirs, pin, bit in ctx.writes:
            if dirs:
                self.dirs = (self.dirs & ~(1 << pin)) | (bit << pin)
            else:
                self.out_level = (self.out_level & ~(1 << pin)) | (bit << pin)
        self.irq &= ~sum(1 << f for f in ctx.irq_clear)
        self.irq |= sum(1 << f for f in ctx.irq_set)       # a flag that is set and cleared in the same cycle ends up set (RP2350 11.2.8 p.798)
        for nb, f in ctx.irq_clear_other:
            nb.irq &= ~(1 << f)
        for nb, f in ctx.irq_set_other:
            nb.irq |= 1 << f
        self.cycle += 1
        if self.recording:
            self.trace.append({"cycle": self.cycle - 1, "pads": self.pads(), "dirs": self.dirs, "irq": self.irq, "sms": [sm.snapshot() for sm in self.sm]})

    def irq_clear(self, mask):
        """Clear IRQ flags from the host side (write 1 to clear the IRQ register, RP2040 3.7 List of Registers p.369).

        Parameters
        ----------
        mask : int
            One bit per flag, 0 to 7.
        """
        self.irq &= ~mask & 0xFF

    def irq_force(self, mask):
        """Raise IRQ flags from the host side (IRQ_FORCE: "writing here affects PIO internal state", RP2040 p.369).

        Parameters
        ----------
        mask : int
            One bit per flag, 0 to 7.
        """
        self.irq |= mask & 0xFF

    def run(self, cycles):
        """Run the block for a number of system clock cycles (calls `step`).

        Parameters
        ----------
        cycles : int
            The number of cycles.

        Returns
        -------
        PioBlock
            `self`.

        Raises
        ------
        EmulationError
            If a state machine does something the hardware leaves undefined.

        Examples
        --------
        >>> from pioweave.builder import Program, PINS
        >>> from pioweave.emulator import PioBlock
        >>> p = Program("square")
        >>> with p.wrapped():
        ...     p.set(PINS, 1, delay=1)
        ...     p.set(PINS, 0, delay=1)
        >>> block = PioBlock()
        >>> _ = block.sm[0].configure(program=block.load(p), set_base=0, set_count=1).enable()
        >>> block.dirs = 1
        >>> block.run(2).pads()
        1
        >>> block.run(2).pads()
        0
        >>> block.cycle
        4
        """
        for _ in range(cycles):
            self.step()
        return self
