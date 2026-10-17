# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Write PIO programs for the Raspberry Pi RP2040, RP2350 and RP2354 in Python (design note docs/design-notes/pio-dsl.rst).

A `Program` is built with one method per PIO instruction, in the style of an Amaranth description: statements in program order, names as
constants, errors raised when the program is built, not when the chip runs it. It produces::

  program.pio_text()   pioasm source (assembled by pioasm, which bitweave runs: `bitweave pio NAME` with a `generator`)
  program.words()      the 16-bit instruction words, encoded here from the datasheet tables (RP2040 datasheet chapter 3, RP2350
                       datasheet chapter 11), independently of pioasm, so the two can be compared (tests/test_pioprog.py)

Facts used: the instruction encodings, the directive and instruction syntax of pioasm as printed in the datasheets. No third-party code.
Host software only: nothing here runs on a chip or in an FPGA.

    >>> from pioweave.builder import Program, PINS
    >>> p = Program("square", chip="rp2040")
    >>> with p.wrapped():
    ...     p.set(PINS, 1, delay=1)
    ...     p.set(PINS, 0, delay=1)
    >>> print(p.pio_text(), end="")
    .program square
    .pio_version 0
    .wrap_target
        set pins, 1 [1]
        set pins, 0 [1]
    .wrap
"""
import math
import re
from contextlib import contextmanager

__all__ = [
    "ALWAYS", "CHIP_VERSION", "CLEAR", "EXEC", "INVERT", "ISR", "MEMORY_WORDS", "NONE", "NOT_OSRE", "NOT_X", "NOT_Y", "NULL", "OSR", "PC", "PIN", "PINDIRS", "PINS",
    "REVERSE", "SET", "STATUS", "WAIT", "X", "X_DEC", "X_NE_Y", "Y", "Y_DEC", "PioError", "Program", "write_pio",
]

# the RP2354 is an RP2350 with 2 MB of flash in the package; the A (QFN-60, 30 GPIOs) and B (QFN-80, 48 GPIOs) variants differ only in the pinout (RP2350 datasheet, Table 1)
CHIP_VERSION = {"rp2040": 0, "rp2350": 1, "rp2350a": 1, "rp2350b": 1, "rp2354": 1, "rp2354a": 1, "rp2354b": 1}
MEMORY_WORDS = 32                                         # shared instruction memory of one PIO block


# (regular expression searched in the message, hint appended to it): what to change for the most common mistakes. The message itself is never changed.
_HINTS = [
    (r"more than \d+ instructions", "split the program, or let loops and delays do the repeating (a delay [n] or a jmp x-- loop costs no extra word); the 32 words are shared by the four state machines of a block"),
    (r"mandatory side-set: give side=", "give side= on every instruction, or create the Program with side_opt=True so that it may be left out"),
    (r"side= given but the program has side_set=0", "create the Program with side_set=N (the number of side-set bits)"),
    (r"^delay \(|leaves no room", "the delay and the side-set share 5 bits: use fewer side-set bits or a smaller delay (a nop can carry the rest of a long delay)"),
    (r"jmp to undefined label", "define the label with p.label(name) just before the instruction it should point at"),
    (r"label .* is defined twice", "give every label a unique name"),
    (r"label .* points past the last instruction|wrap_target points past", "add an instruction after the label"),
    (r"needs PIO version 1", "use chip='rp2350' (or rp2350a, rp2350b, rp2354) for this form; the RP2040 has PIO version 0"),
    (r"must come before the first instruction", "move this directive above the first instruction of the program"),
    (r"has no instructions", "add at least one instruction before calling pio_text(), words() or write_pio()"),
    (r"wait gpio uses GPIOs below 16", "keep the GPIOs of one program inside GPIO 0..31 or inside GPIO 16..47 (the PIO sees a window of 32 pins)"),
    (r"jmp target address \d+ is beyond", "a numeric target is an instruction index inside the program; use a label instead"),
    (r"^chip .* use rp2040", "pass chip='rp2040' or chip='rp2350'"),
]


class PioError(ValueError):
    """The program is not valid for the chosen chip.

    Raised by the `Program` methods when an argument is out of range or not allowed for the chip, and by `Program.pio_text`, `Program.words` and
    `Program.wrap_range` when the finished program is inconsistent (an undefined label, a jump beyond the end, no instructions). It is a `ValueError`.

    Examples
    --------
    >>> from pioweave.builder import Program, PINS
    >>> Program("p").set(PINS, 32)
    Traceback (most recent call last):
      ...
    pioweave.builder.program.PioError: set value must be an integer from 0 to 31, got 32
    """

    def __init__(self, message=""):
        message = str(message)
        for pattern, hint in _HINTS:
            if re.search(pattern, message):
                message += "\n  hint: " + hint
                break
        super().__init__(message)


class _Name:
    """A named operand (register, condition, ...); compares and prints by name."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


# registers and destinations
PINS, X, Y, NULL, ISR, OSR, PC, EXEC, PINDIRS, STATUS = (_Name(n) for n in ("pins", "x", "y", "null", "isr", "osr", "pc", "exec", "pindirs", "status"))

# JMP conditions: (text, code)
ALWAYS = _Name("always")
NOT_X, X_DEC, NOT_Y, Y_DEC, X_NE_Y, PIN, NOT_OSRE = (_Name(n) for n in ("not_x", "x_dec", "not_y", "y_dec", "x_ne_y", "pin", "not_osre"))
_COND = {ALWAYS: ("", 0), NOT_X: ("!x", 1), X_DEC: ("x--", 2), NOT_Y: ("!y", 3), Y_DEC: ("y--", 4), X_NE_Y: ("x!=y", 5), PIN: ("pin", 6), NOT_OSRE: ("!osre", 7)}

# MOV operations
NONE, INVERT, REVERSE = (_Name(n) for n in ("none", "invert", "reverse"))
_MOV_OP = {NONE: ("", 0), INVERT: ("!", 1), REVERSE: ("::", 2)}

# IRQ modes: set the flag, set it and wait until it is cleared, clear it
SET, WAIT, CLEAR = (_Name(n) for n in ("set", "wait", "clear"))

_IN_SRC = {PINS: 0, X: 1, Y: 2, NULL: 3, ISR: 6, OSR: 7}
_OUT_DST = {PINS: 0, X: 1, Y: 2, NULL: 3, PINDIRS: 4, PC: 5, ISR: 6, EXEC: 7}
_MOV_DST = {PINS: 0, X: 1, Y: 2, PINDIRS: 3, EXEC: 4, PC: 5, ISR: 6, OSR: 7}
_MOV_SRC = {PINS: 0, X: 1, Y: 2, NULL: 3, STATUS: 5, ISR: 6, OSR: 7}
_SET_DST = {PINS: 0, X: 1, Y: 2, PINDIRS: 4}


class _Instr:
    def __init__(self, kind, side, delay, **f):
        self.kind, self.side, self.delay, self.f = kind, side, delay, f
        self.labels = []          # labels placed before this instruction
        self.wrap_target = False  # execution wraps to this instruction
        self.wrap_top = False     # the program wraps after this instruction


def _int(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise PioError("%s must be an integer from %d to %d, got %r" % (what, lo, hi, v))
    return v


def _is_ident(name):
    """True for a pioasm symbol: ASCII letters, digits and underscore, not starting with a digit (``str.isalnum`` accepts Unicode letters and digits, pioasm does not)."""
    return isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is not None


def _pick(table, v, what):
    try:
        known = v in table
    except TypeError:                   # not hashable, for example a list
        known = False
    if not known:
        raise PioError("%s %r is not allowed here; use one of: %s" % (what, v, ", ".join(repr(k) for k in table)))
    return table[v]


class Program:
    """One PIO program, written one method call per instruction.

    The chip decides which instructions and directives are legal (PIO version 0 on the RP2040, version 1 on the RP2350 and RP2354). A method that is given
    an argument the chip or the instruction does not allow raises `PioError` at once; problems that only show when the program is complete (an undefined
    label, a jump beyond the end) are raised by `pio_text`, `words` and `wrap_range`. Every instruction method takes `side` (the side-set value) and
    `delay` (the delay cycles); both share five bits with the side-set (RP2040 datasheet chapter 3, RP2350 datasheet chapter 11).

    Parameters
    ----------
    name : str
        The program name (pioasm `.program`): letters, digits and underscores, not starting with a digit.
    chip : str
        One of ``rp2040``, ``rp2350``, ``rp2350a``, ``rp2350b``, ``rp2354``, ``rp2354a``, ``rp2354b`` (see `CHIP_VERSION`).
    side_set : int
        Number of side-set bits, 0 to 5 (the enable bit of `side_opt` counts against the five bits shared with the delay).
    side_opt : bool
        Side-set is optional per instruction (adds an enable bit); needs ``side_set >= 1``.
    side_pindirs : bool
        Side-set drives pin directions instead of levels; needs ``side_set >= 1``.
    origin : int or None
        Fixed load address (pioasm `.origin`), 0 to 31, or None.
    emit_version : bool
        Write a ``.pio_version`` line in `pio_text`.

    Attributes
    ----------
    name : str
        The program name.
    chip : str
        The chip name as given.
    version : int
        The PIO version of the chip: 0 or 1.
    emit_version : bool
        Whether `pio_text` writes ``.pio_version``.
    side_count, side_opt, side_pindirs, origin :
        The side-set and origin settings as given.
    clock_div : int, float or None
        Set by `clock_divider`.
    fifo_config : str or None
        Set by `fifo`.
    status_cfg : str or None
        The ``.mov_status`` line set by `mov_status`.
    in_cfg, out_cfg : str or None
        The ``.in`` / ``.out`` lines set by `config_in` and `config_out`.
    set_count : int or None
        Set by `config_set`.
    defines : list of tuple
        ``(symbol, value, public)`` entries added by `define`.

    Raises
    ------
    PioError
        If the name, the chip or the side-set settings are not valid.

    Examples
    --------
    A program with an optional side-set bit; ``side`` and ``delay`` share the five bits:

    >>> from pioweave.builder import Program, X, X_DEC
    >>> p = Program("blink", side_set=1, side_opt=True)
    >>> p.label("top")
    'top'
    >>> p.set(X, 5, side=1, delay=3)
    >>> p.jmp("top", when=X_DEC, side=0)
    >>> print(p.pio_text(), end="")
    .program blink
    .pio_version 0
    .side_set 1 opt
    top:
        set x, 5 side 1 [3]
        jmp x-- top side 0
    >>> [hex(w) for w in p.words()]
    ['0xfb25', '0x1040']
    """

    def __init__(self, name, chip="rp2040", side_set=0, side_opt=False, side_pindirs=False, origin=None, emit_version=True):
        if not _is_ident(name):
            raise PioError("program name %r must be ASCII letters, digits and underscore and not start with a digit" % (name,))
        if chip not in CHIP_VERSION:
            raise PioError("chip %r: use rp2040, rp2350, rp2350a, rp2350b, rp2354, rp2354a or rp2354b" % (chip,))
        self.name, self.chip, self.version = name, chip, CHIP_VERSION[chip]
        self.emit_version = emit_version
        self.side_count, self.side_opt, self.side_pindirs = _int(side_set, 0, 5, "side_set"), bool(side_opt), bool(side_pindirs)
        if self.side_count + (1 if self.side_opt and self.side_count else 0) > 5:
            raise PioError("side_set %d with an enable bit leaves no room (5 bits shared with the delay)" % self.side_count)
        if (self.side_opt or self.side_pindirs) and self.side_count == 0:
            raise PioError("side_opt / side_pindirs need side_set >= 1")
        self.origin = None if origin is None else _int(origin, 0, 31, "origin")
        self.clock_div = None
        self.fifo_config = None
        self.status_cfg = None
        self.in_cfg = self.out_cfg = None
        self.set_count = None
        self.defines = []
        self._instrs = []
        self._pending_labels = []
        self._pending_wrap_target = False
        self._wrap_target_set = self._wrap_set = False
        self._label_names = set()

    # ---- directives that change the default state machine configuration (pioasm; they only affect the generated C header)
    def _before_code(self, what):
        if self._instrs:
            raise PioError("%s must come before the first instruction (pioasm rule)" % what)

    def clock_divider(self, divider):
        """Set the default clock divider (pioasm `.clock_div`; it only changes the generated C header).

        Parameters
        ----------
        divider : int or float
            A finite number from 1 to 65535 (the range pioasm accepts).

        Raises
        ------
        PioError
            If an instruction was already added, or `divider` is not a finite number from 1 to 65535.
        """
        self._before_code("clock_divider")
        if not isinstance(divider, (int, float)) or isinstance(divider, bool) or (isinstance(divider, float) and not math.isfinite(divider)) or not 1 <= divider <= 65535:
            raise PioError("clock divider must be a number from 1 to 65535 (pioasm refuses anything else), not %r" % (divider,))
        self.clock_div = divider

    def fifo(self, config):
        """Set the FIFO configuration (pioasm `.fifo`).

        Parameters
        ----------
        config : str
            ``txrx``, ``tx`` or ``rx``; on PIO version 1 (RP2350, RP2354) also ``txput``, ``txget`` or ``putget``.

        Raises
        ------
        PioError
            If an instruction was already added, or `config` is not allowed on this chip.
        """
        self._before_code("fifo")
        names = ("txrx", "tx", "rx") + (("txput", "txget", "putget") if self.version >= 1 else ())
        if config not in names:
            raise PioError("fifo %r on %s: use %s (txput, txget and putget need PIO version 1, RP2350 / RP2354)" % (config, self.chip, ", ".join(names)))
        self.fifo_config = config

    def mov_status(self, kind, n, which=None):
        """Set the source of ``mov ..., status`` (pioasm `.mov_status`).

        Parameters
        ----------
        kind : str
            ``'rxfifo'`` or ``'txfifo'`` (status is true while the FIFO level is below `n`), or ``'irq'`` (status is true while flag `n` is set; RP2350 / RP2354 only).
        n : int
            The FIFO level, 0 to 15, or the IRQ flag, 0 to 7.
        which : str or None
            For ``'irq'`` only: None, ``'prev'`` or ``'next'``.

        Raises
        ------
        PioError
            If an instruction was already added, or an argument is not allowed on this chip.
        """
        self._before_code("mov_status")
        if kind in ("rxfifo", "txfifo"):
            self.status_cfg = ".mov_status %s < %d" % (kind, _int(n, 0, 15, "mov_status level"))
        elif kind == "irq":
            if self.version < 1:
                raise PioError("mov_status irq needs PIO version 1 (RP2350 / RP2354)")
            if which not in (None, "prev", "next"):
                raise PioError("mov_status irq which must be None, 'prev' or 'next'")
            self.status_cfg = ".mov_status irq %sset %d" % (which + " " if which else "", _int(n, 0, 7, "mov_status irq flag"))
        else:
            raise PioError("mov_status kind must be 'rxfifo', 'txfifo' or 'irq'")

    def _shift_cfg(self, what, count, direction, auto, threshold):
        self._before_code(".%s" % what)
        if self.version < 1 and count != 32 and what in ("in", "out"):
            raise PioError(".%s count must be 32 on PIO version 0 (pioasm rule)" % what)
        _int(count, 1 if what != "set" else 0, 32 if what != "set" else 5, ".%s count" % what)
        if direction not in (None, "left", "right"):
            raise PioError("direction must be None, 'left' or 'right'")
        if threshold is not None:
            _int(threshold, 1, 32, "threshold")
            if not auto:
                raise PioError("a threshold needs auto = True")
        return ".%s %d%s%s%s" % (what, count, " " + direction if direction else "", " auto" if auto else "", " %d" % threshold if threshold is not None else "")

    def config_in(self, count, direction=None, auto=False, threshold=None):
        """Set the default input shift configuration (pioasm `.in`).

        Parameters
        ----------
        count : int
            Number of input pins, 1 to 32; must be 32 on PIO version 0.
        direction : str or None
            ``'left'``, ``'right'`` or None.
        auto : bool
            Enable autopush.
        threshold : int or None
            Autopush threshold, 1 to 32; needs ``auto=True``.

        Raises
        ------
        PioError
            If an instruction was already added, or an argument is not allowed on this chip.
        """
        self.in_cfg = self._shift_cfg("in", count, direction, auto, threshold)

    def config_out(self, count, direction=None, auto=False, threshold=None):
        """Set the default output shift configuration (pioasm `.out`).

        Parameters
        ----------
        count : int
            Number of output pins, 1 to 32; must be 32 on PIO version 0.
        direction : str or None
            ``'left'``, ``'right'`` or None.
        auto : bool
            Enable autopull.
        threshold : int or None
            Autopull threshold, 1 to 32; needs ``auto=True``.

        Raises
        ------
        PioError
            If an instruction was already added, or an argument is not allowed on this chip.
        """
        self.out_cfg = self._shift_cfg("out", count, direction, auto, threshold)

    def config_set(self, count):
        """Set the number of pins driven by ``set pins`` (pioasm `.set`).

        Parameters
        ----------
        count : int
            0 to 5.

        Raises
        ------
        PioError
            If an instruction was already added, or `count` is out of range.
        """
        self._before_code(".set")
        _int(count, 0, 5, ".set count")
        self.set_count = count

    def define(self, symbol, value, public=False):
        """Add a symbol (pioasm `.define`).

        Parameters
        ----------
        symbol : str
            Letters, digits and underscores, not starting with a digit.
        value : int
            A 32-bit value.
        public : bool
            Write ``PUBLIC``, which exports the symbol to the generated C header.

        Raises
        ------
        PioError
            If the symbol or the value is not valid.
        """
        if not _is_ident(symbol):
            raise PioError("symbol %r must be ASCII letters, digits and underscore and not start with a digit" % (symbol,))
        self.defines.append((symbol, _int(value, -(1 << 31), (1 << 32) - 1, "define value"), bool(public)))

    # ---- structure
    def label(self, name):
        """Name the next instruction; usable as a JMP target before or after this point.

        Parameters
        ----------
        name : str
            Letters, digits and underscores, not starting with a digit; unique in the program.

        Returns
        -------
        str
            `name`, to be passed to `jmp`.

        Raises
        ------
        PioError
            If the name is not valid or already defined.
        """
        if not _is_ident(name):
            raise PioError("label %r must be ASCII letters, digits and underscore and not start with a digit" % (name,))
        if name in self._label_names:
            raise PioError("label %r is defined twice" % name)
        self._label_names.add(name)
        self._pending_labels.append(name)
        return name

    def wrap_target(self):
        """Execution continues here after the wrap point (default: the first instruction).

        Raises
        ------
        PioError
            If it was already used.
        """
        if self._wrap_target_set:
            raise PioError("wrap_target may be used only once")
        self._wrap_target_set = True
        self._pending_wrap_target = True

    def wrap(self):
        """The instruction just written is the last of the loop: after it execution wraps (default: the last instruction).

        Raises
        ------
        PioError
            If it was already used, or no instruction was written yet.
        """
        if self._wrap_set:
            raise PioError("wrap may be used only once")
        if not self._instrs:
            raise PioError("wrap needs an instruction before it")
        self._wrap_set = True
        self._instrs[-1].wrap_top = True

    @contextmanager
    def wrapped(self):
        """``with p.wrapped():`` makes the instructions inside the wrap loop (`wrap_target` before them, `wrap` after them).

        Examples
        --------
        >>> from pioweave.builder import Program, PINS
        >>> p = Program("w")
        >>> p.nop()
        >>> with p.wrapped():
        ...     p.set(PINS, 1)
        >>> p.wrap_range
        (1, 1)
        """
        self.wrap_target()
        yield
        self.wrap()

    # ---- instructions (every one takes side= and delay=)
    def _add(self, kind, side, delay, **f):
        ds_bits = 5 - self.side_count - (1 if self.side_opt else 0)
        _int(delay, 0, (1 << ds_bits) - 1, "delay (%d bit%s left after the side-set)" % (ds_bits, "" if ds_bits == 1 else "s"))
        if side is None:
            if self.side_count and not self.side_opt:
                raise PioError("this program has a mandatory side-set: give side= (0..%d) on every instruction" % ((1 << self.side_count) - 1))
        else:
            if not self.side_count:
                raise PioError("side= given but the program has side_set=0")
            _int(side, 0, (1 << self.side_count) - 1, "side")
        ins = _Instr(kind, side, delay, **f)
        ins.labels, self._pending_labels = self._pending_labels, []
        ins.wrap_target, self._pending_wrap_target = self._pending_wrap_target, False
        self._instrs.append(ins)
        if len(self._instrs) > MEMORY_WORDS:
            raise PioError("more than %d instructions: the PIO instruction memory has %d words" % (MEMORY_WORDS, MEMORY_WORDS))
        return ins

    def nop(self, side=None, delay=0):
        """``nop`` (encoded as ``mov y, y``).

        Parameters
        ----------
        side : int or None
            Side-set value, or None.
        delay : int
            Delay cycles; the maximum depends on the side-set bits.

        Raises
        ------
        PioError
            If `side` or `delay` is out of range, or `side` is missing in a program with a mandatory side-set.
        """
        self._add("nop", side, delay)

    def jmp(self, target, when=ALWAYS, side=None, delay=0):
        """``jmp`` to a label or an address, optionally on a condition.

        Parameters
        ----------
        target : str or int
            A label name (see `label`) or an address 0 to 31.
        when : _Name
            The condition: `ALWAYS`, `NOT_X`, `X_DEC`, `NOT_Y`, `Y_DEC`, `X_NE_Y`, `PIN` or `NOT_OSRE`.
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If the condition or target is not valid, or `side` / `delay` is out of range.
        """
        _pick(_COND, when, "jmp condition")
        if not isinstance(target, (str, int)) or isinstance(target, bool):
            raise PioError("jmp target must be a label name or an address")
        if isinstance(target, int):
            _int(target, 0, 31, "jmp address")
        self._add("jmp", side, delay, target=target, cond=when)

    def _wait(self, pol, src, idx, side, delay, rel=False, mode=None):
        _int(pol, 0, 1, "wait polarity")
        self._add("wait", side, delay, pol=pol, src=src, idx=idx, rel=rel, mode=mode)

    def wait_gpio(self, polarity, gpio, side=None, delay=0):
        """``wait polarity gpio gpio``: wait on a real GPIO number.

        Parameters
        ----------
        polarity : int
            0 or 1, the level to wait for.
        gpio : int
            0 to 31, or 0 to 47 on PIO version 1 (RP2350 / RP2354; the B variants have 48 GPIOs).
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If an argument is out of range.
        """
        self._wait(polarity, "gpio", _int(gpio, 0, 47 if self.version >= 1 else 31, "GPIO number"), side, delay)

    def wait_pin(self, polarity, pin, side=None, delay=0):
        """``wait polarity pin pin``: wait on input pin `pin` of the state machine's IN mapping (IN_BASE + pin).

        Parameters
        ----------
        polarity : int
            0 or 1, the level to wait for.
        pin : int
            0 to 31.
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If an argument is out of range.
        """
        self._wait(polarity, "pin", _int(pin, 0, 31, "pin index"), side, delay)

    def wait_irq(self, polarity, flag, rel=False, other=None, side=None, delay=0):
        """``wait polarity irq flag``: wait on an IRQ flag.

        Parameters
        ----------
        polarity : int
            0 or 1, the flag level to wait for.
        flag : int
            0 to 7.
        rel : bool
            Add the state machine number to the low two bits of the flag number.
        other : str or None
            Selects the neighbouring PIO block, ``'prev'`` or ``'next'`` (RP2350 / RP2354 only; not with `rel`).
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If an argument is out of range or not allowed on this chip.
        """
        self._irq_target(flag, rel, other)
        self._wait(polarity, "irq", flag, side, delay, rel=rel, mode=other)

    def wait_jmppin(self, polarity, offset=0, side=None, delay=0):
        """``wait polarity jmppin [+ offset]`` (PIO version 1 only): wait on the JMP pin plus `offset`.

        Parameters
        ----------
        polarity : int
            0 or 1, the level to wait for.
        offset : int
            0 to 3.
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If the chip is PIO version 0, or an argument is out of range.
        """
        if self.version < 1:
            raise PioError("wait jmppin needs PIO version 1 (RP2350 / RP2354)")
        self._wait(polarity, "jmppin", _int(offset, 0, 3, "jmppin offset"), side, delay)

    def in_(self, src, bits, side=None, delay=0):
        """``in src, bits``: shift `bits` bits from `src` into the ISR.

        Parameters
        ----------
        src : _Name
            `PINS`, `X`, `Y`, `NULL`, `ISR` or `OSR`.
        bits : int
            1 to 32.
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If an argument is not allowed.
        """
        _pick(_IN_SRC, src, "in source")
        self._add("in", side, delay, src=src, bits=_int(bits, 1, 32, "in bit count"))

    def out(self, dst, bits, side=None, delay=0):
        """``out dst, bits``: shift `bits` bits out of the OSR into `dst`.

        Parameters
        ----------
        dst : _Name
            `PINS`, `X`, `Y`, `NULL`, `PINDIRS`, `PC`, `ISR` or `EXEC`.
        bits : int
            1 to 32.
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If an argument is not allowed.
        """
        _pick(_OUT_DST, dst, "out destination")
        self._add("out", side, delay, dst=dst, bits=_int(bits, 1, 32, "out bit count"))

    def push(self, iffull=False, block=True, side=None, delay=0):
        """``push [iffull] [block | noblock]``: push the ISR to the RX FIFO.

        Parameters
        ----------
        iffull : bool
            Push only when the ISR is full to its threshold.
        block : bool
            Stall when the FIFO is full (``block``) instead of dropping the word (``noblock``).
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If the program's FIFO configuration has no RX FIFO, or `side` / `delay` is out of range.
        """
        if self.fifo_config not in (None, "txrx", "rx"):
            raise PioError("push needs fifo txrx or rx, this program has fifo %s (pioasm rule)" % self.fifo_config)
        self._add("push", side, delay, iff=bool(iffull), blk=bool(block))

    def pull(self, ifempty=False, block=True, side=None, delay=0):
        """``pull [ifempty] [block | noblock]``: load the OSR from the TX FIFO.

        Parameters
        ----------
        ifempty : bool
            Pull only when the OSR is empty to its threshold.
        block : bool
            Stall when the FIFO is empty (``block``) instead of loading X (``noblock``).
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If `side` or `delay` is out of range.
        """
        self._add("pull", side, delay, ife=bool(ifempty), blk=bool(block))

    def mov(self, dst, src, op=NONE, side=None, delay=0):
        """``mov dst, [op] src``.

        Parameters
        ----------
        dst : _Name
            `PINS`, `X`, `Y`, `PINDIRS` (PIO version 1 only), `EXEC`, `PC`, `ISR` or `OSR`.
        src : _Name
            `PINS`, `X`, `Y`, `NULL`, `STATUS`, `ISR` or `OSR`.
        op : _Name
            `NONE`, `INVERT` or `REVERSE`.
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If an argument is not allowed on this chip.
        """
        _pick(_MOV_DST, dst, "mov destination")
        _pick(_MOV_SRC, src, "mov source")
        _pick(_MOV_OP, op, "mov operation")
        if dst is PINDIRS and self.version < 1:
            raise PioError("mov pindirs needs PIO version 1 (RP2350 / RP2354)")
        self._add("mov", side, delay, dst=dst, src=src, op=op)

    def mov_put(self, index, side=None, delay=0):
        """``mov rxfifo[index], isr`` (RP2350 / RP2354).

        Parameters
        ----------
        index : int or _Name
            0 to 3, or `Y`.
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If the chip is PIO version 0, the program's FIFO configuration is not ``txput`` or ``putget``, or an argument is out of range.
        """
        self._rx_index(index, True)
        self._add("movrx", side, delay, put=True, index=index)

    def mov_get(self, index, side=None, delay=0):
        """``mov osr, rxfifo[index]`` (RP2350 / RP2354).

        Parameters
        ----------
        index : int or _Name
            0 to 3, or `Y`.
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If the chip is PIO version 0, the program's FIFO configuration is not ``txget`` or ``putget``, or an argument is out of range.
        """
        self._rx_index(index, False)
        self._add("movrx", side, delay, put=False, index=index)

    def irq(self, flag, mode=SET, rel=False, other=None, side=None, delay=0):
        """``irq set | wait | clear flag``.

        Parameters
        ----------
        flag : int
            0 to 7.
        mode : _Name
            `SET`, `WAIT` (set and wait until it is cleared) or `CLEAR`.
        rel : bool
            Add the state machine number to the low two bits of the flag number.
        other : str or None
            ``'prev'`` or ``'next'``: the neighbouring PIO block (RP2350 / RP2354 only; not with `rel`).
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If an argument is out of range or not allowed on this chip.
        """
        if mode not in (SET, WAIT, CLEAR):
            raise PioError("irq mode must be SET, WAIT or CLEAR")
        self._irq_target(flag, rel, other)
        self._add("irq", side, delay, flag=flag, mode=mode, rel=rel, other=other)

    def set(self, dst, value, side=None, delay=0):
        """``set dst, value``.

        Parameters
        ----------
        dst : _Name
            `PINS`, `X`, `Y` or `PINDIRS`.
        value : int
            0 to 31.
        side, delay :
            As for `nop`.

        Raises
        ------
        PioError
            If an argument is not allowed.
        """
        _pick(_SET_DST, dst, "set destination")
        self._add("set", side, delay, dst=dst, value=_int(value, 0, 31, "set value"))

    def word(self, value):
        """A raw 16-bit instruction word (pioasm ``.word``).

        Parameters
        ----------
        value : int
            0 to 0xFFFF.

        Raises
        ------
        PioError
            If `value` is out of range, or the program has a mandatory side-set (pioasm cannot assemble that).
        """
        if self.side_count and not self.side_opt:
            raise PioError(".word cannot be assembled by pioasm in a program with a mandatory side-set (it asks for a side value and then rejects it); use side_opt")
        self._add("word", None, 0, value=_int(value, 0, 0xFFFF, "word"))

    # ---- helpers
    def _rx_index(self, index, put):
        if self.version < 1:
            raise PioError("mov to / from the RX FIFO registers needs PIO version 1 (RP2350 / RP2354)")
        ok = ("txput", "putget") if put else ("txget", "putget")
        if self.fifo_config not in ok:
            raise PioError("mov %s needs fifo %s or %s, this program has fifo %s (pioasm rule)" % ("rxfifo[], isr" if put else "osr, rxfifo[]", ok[0], ok[1], self.fifo_config or "txrx"))
        if index is not Y:
            _int(index, 0, 3, "rxfifo index")

    def _irq_target(self, flag, rel, other):
        _int(flag, 0, 7, "IRQ flag")
        if other is not None:
            if self.version < 1:
                raise PioError("irq prev / next needs PIO version 1 (RP2350 / RP2354)")
            if other not in ("prev", "next"):
                raise PioError("other must be 'prev' or 'next'")
            if rel:
                raise PioError("rel is not supported with prev / next")

    # ---- output
    def _resolve(self):
        if self._pending_labels:
            raise PioError("label %s points past the last instruction" % self._pending_labels[0])
        if self._pending_wrap_target:
            raise PioError("wrap_target points past the last instruction")
        if not self._instrs:
            raise PioError("program %s has no instructions" % self.name)
        if self.in_cfg and " auto" in self.in_cfg and self.fifo_config in ("txput", "txget", "putget"):
            raise PioError("autopush (.in ... auto) is incompatible with fifo %s (pioasm rule)" % self.fifo_config)
        addr = {}
        base = self.origin or 0
        for i, ins in enumerate(self._instrs):
            for n in ins.labels:
                addr[n] = i      # program-relative, as pioasm writes it; the SDK adds the load offset
        for ins in self._instrs:
            if ins.kind == "jmp" and isinstance(ins.f["target"], str):
                if ins.f["target"] not in addr:
                    raise PioError("jmp to undefined label %r" % ins.f["target"])
            elif ins.kind == "jmp" and ins.f["target"] >= len(self._instrs):
                raise PioError("jmp target address %d is beyond the end of the program (%d instructions; pioasm rule)" % (ins.f["target"], len(self._instrs)))
        ranges = 0
        for ins in self._instrs:
            if ins.kind == "wait" and ins.f["src"] == "gpio":
                ranges |= 1 << (ins.f["idx"] >> 4)
        if ranges & 1 and ranges & 4:
            raise PioError("wait gpio uses GPIOs below 16 and above 31 in one program: a PIO sees 32 GPIOs at a time (GPIO base 0 or 16) (pioasm rule)")
        if base + len(self._instrs) > MEMORY_WORDS:
            raise PioError("origin %d plus %d instructions does not fit the %d-word memory" % (base, len(self._instrs), MEMORY_WORDS))
        return addr

    def _operand_text(self, ins):
        f = ins.f
        k = ins.kind
        if k == "nop":
            return "nop"
        if k == "jmp":
            c = _COND[f["cond"]][0]
            return "jmp %s%s" % (c + " " if c else "", f["target"])
        if k == "wait":
            if f["src"] == "irq":
                return "wait %d irq %s%d%s" % (f["pol"], f["mode"] + " " if f["mode"] else "", f["idx"], " rel" if f["rel"] else "")
            if f["src"] == "jmppin":
                return "wait %d jmppin%s" % (f["pol"], " + %d" % f["idx"] if f["idx"] else "")
            return "wait %d %s %d" % (f["pol"], f["src"], f["idx"])
        if k == "in":
            return "in %r, %d" % (f["src"], f["bits"])
        if k == "out":
            return "out %r, %d" % (f["dst"], f["bits"])
        if k == "push":
            return "push%s %s" % (" iffull" if f["iff"] else "", "block" if f["blk"] else "noblock")
        if k == "pull":
            return "pull%s %s" % (" ifempty" if f["ife"] else "", "block" if f["blk"] else "noblock")
        if k == "mov":
            return "mov %r, %s%r" % (f["dst"], _MOV_OP[f["op"]][0], f["src"])
        if k == "movrx":
            idx = "y" if f["index"] is Y else str(f["index"])
            return "mov rxfifo[%s], isr" % idx if f["put"] else "mov osr, rxfifo[%s]" % idx
        if k == "irq":
            return "irq %s%s %d%s" % (f["other"] + " " if f["other"] else "", f["mode"], f["flag"], " rel" if f["rel"] else "")
        if k == "set":
            return "set %r, %d" % (f["dst"], f["value"])
        return ".word 0x%04X" % f["value"]

    def pio_text(self):
        """The program as pioasm source.

        Returns
        -------
        str
            The text, one line per directive, label or instruction, ending in a newline.

        Raises
        ------
        PioError
            If the program is inconsistent: no instructions, a label or wrap target past the last instruction, a jump to an undefined label or beyond the end,
            a ``wait gpio`` that needs two GPIO windows, or an origin that does not fit the 32-word memory.

        Examples
        --------
        >>> from pioweave.builder import Program, PINS
        >>> p = Program("square")
        >>> with p.wrapped():
        ...     p.set(PINS, 1, delay=1)
        ...     p.set(PINS, 0, delay=1)
        >>> print(p.pio_text(), end="")
        .program square
        .pio_version 0
        .wrap_target
            set pins, 1 [1]
            set pins, 0 [1]
        .wrap
        """
        self._resolve()
        out = [".program " + self.name]
        if self.emit_version:
            out.append(".pio_version %d" % self.version)
        for sym, val, public in self.defines:
            out.append(".define %s%s %d" % ("PUBLIC " if public else "", sym, val))
        if self.origin is not None:
            out.append(".origin %d" % self.origin)
        if self.clock_div is not None:
            out.append(".clock_div %s" % self.clock_div)
        if self.fifo_config:
            out.append(".fifo " + self.fifo_config)
        if self.status_cfg:
            out.append(self.status_cfg)
        for cfg in (self.in_cfg, self.out_cfg):
            if cfg:
                out.append(cfg)
        if self.set_count is not None:
            out.append(".set %d" % self.set_count)
        if self.side_count:
            out.append(".side_set %d%s%s" % (self.side_count, " opt" if self.side_opt else "", " pindirs" if self.side_pindirs else ""))
        for ins in self._instrs:
            if ins.wrap_target:
                out.append(".wrap_target")
            for n in ins.labels:
                out.append(n + ":")
            line = "    " + self._operand_text(ins)
            if ins.kind != "word":
                if ins.side is not None:
                    line += " side %d" % ins.side
                if ins.delay:
                    line += " [%d]" % ins.delay
            out.append(line)
            if ins.wrap_top:
                out.append(".wrap")
        return "\n".join(out) + "\n"

    # ---- own encoder (datasheet tables), independent of pioasm
    def _ds(self, ins):
        """The 5-bit delay / side-set field."""
        bits = self.side_count
        delay_bits = 5 - bits - (1 if self.side_opt else 0)
        v = ins.delay
        if self.side_count:
            if ins.side is not None:
                v |= ins.side << delay_bits
                if self.side_opt:
                    v |= 1 << 4
        return v

    def words(self):
        """The assembled 16-bit words as pioasm writes them: JMP targets are relative to the start of the program (the SDK adds the load offset).

        The encoder is written from the datasheet tables and does not call pioasm. A ``wait gpio`` on GPIO 32 to 47 sets bit 16 of its word, the flag that pioasm
        uses for it.

        Returns
        -------
        list of int
            One word per instruction.

        Raises
        ------
        PioError
            As for `pio_text`.

        Examples
        --------
        >>> from pioweave.builder import Program, PINS
        >>> p = Program("square")
        >>> with p.wrapped():
        ...     p.set(PINS, 1, delay=1)
        ...     p.set(PINS, 0, delay=1)
        >>> [hex(w) for w in p.words()]
        ['0xe101', '0xe100']
        """
        addr = self._resolve()
        out = []
        for ins in self._instrs:
            f, k = ins.f, ins.kind
            if k == "word":
                out.append(f["value"])
                continue
            ds = self._ds(ins) << 8
            if k == "nop":
                w = (5 << 13) | ds | (_MOV_DST[Y] << 5) | _MOV_SRC[Y]
            elif k == "jmp":
                t = f["target"] if isinstance(f["target"], int) else addr[f["target"]]
                w = (0 << 13) | ds | (_COND[f["cond"]][1] << 5) | t
            elif k == "wait":
                src = {"gpio": 0, "pin": 1, "irq": 2, "jmppin": 3}[f["src"]]
                idx = f["idx"]
                big = 0x10000 if f["src"] == "gpio" and idx >= 32 else 0     # pioasm flags a `wait gpio` on GPIO 32-47 with bit 16 of the word
                if f["src"] == "gpio":
                    idx &= 31
                if f["src"] == "irq":
                    idx |= {None: 0, "prev": 0x08, "next": 0x18}[f["mode"]] | (0x10 if f["rel"] else 0)
                w = big | (1 << 13) | ds | (f["pol"] << 7) | (src << 5) | idx
            elif k == "in":
                w = (2 << 13) | ds | (_IN_SRC[f["src"]] << 5) | (f["bits"] & 31)
            elif k == "out":
                w = (3 << 13) | ds | (_OUT_DST[f["dst"]] << 5) | (f["bits"] & 31)
            elif k == "push":
                w = (4 << 13) | ds | (int(f["iff"]) << 6) | (int(f["blk"]) << 5)
            elif k == "pull":
                w = (4 << 13) | ds | 0x80 | (int(f["ife"]) << 6) | (int(f["blk"]) << 5)
            elif k == "mov":
                w = (5 << 13) | ds | (_MOV_DST[f["dst"]] << 5) | (_MOV_OP[f["op"]][1] << 3) | _MOV_SRC[f["src"]]
            elif k == "movrx":
                immediate = f["index"] is not Y
                w = (4 << 13) | ds | (0 if f["put"] else 0x80) | 0x10 | (8 if immediate else 0) | (f["index"] & 3 if immediate else 0)
            elif k == "irq":
                idx = f["flag"] | {None: 0, "prev": 0x08, "next": 0x18}[f["other"]] | (0x10 if f["rel"] else 0)
                w = (6 << 13) | ds | ((1 if f["mode"] is CLEAR else 0) << 6) | ((1 if f["mode"] is WAIT else 0) << 5) | idx
            else:
                w = (7 << 13) | ds | (_SET_DST[f["dst"]] << 5) | f["value"]
            out.append(w)
        return out

    @property
    def wrap_range(self):
        """``(wrap_target, wrap_top)`` as instruction indexes, with the defaults (first and last instruction).

        Raises
        ------
        PioError
            As for `pio_text`.
        """
        self._resolve()
        t = next((i for i, x in enumerate(self._instrs) if x.wrap_target), 0)
        w = next((i for i, x in enumerate(self._instrs) if x.wrap_top), len(self._instrs) - 1)
        return t, w


def write_pio(programs, path):
    """Write one or more programs into a .pio file (a global ``.pio_version`` is not written; each program carries its own).

    Parameters
    ----------
    programs : iterable of Program
        The programs, written in order.
    path : str
        The file to write.

    Raises
    ------
    PioError
        If a program is inconsistent (see `Program.pio_text`).
    """
    with open(path, "w") as fh:
        fh.write("\n".join(p.pio_text() for p in programs))
