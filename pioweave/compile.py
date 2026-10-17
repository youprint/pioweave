# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""Compile an Amaranth FSM to a PIO program (design note docs/design-notes/amaranth-pio.rst).

A PIO state machine is an FSM whose state is the program counter and that executes one instruction per clock cycle. A design written as one
Amaranth FSM in which every state does what one PIO instruction does is therefore cycle-equivalent to the compiled program, and the Amaranth
simulation of the design is the reference (tests/test_compile.py compares it with the bitweave PIO simulator, pin by pin and cycle by
cycle). The compiler reads the Amaranth 0.5.10 IR (Fragment.statements); anything it cannot map without changing the cycle behaviour is refused
with the source line, never approximated.

Accepted (all in the `sync` domain, one `m.FSM()`, no submodules)::

  state effects      pin outputs from platform.request() assigned constants      -> `set pins` (or side-set, see below)
                     X or Y from platform.scratch() assigned 0..31               -> `set x/y`
                     X or Y assigned the other register or its complement        -> `mov`
                     X or Y assigned itself minus 1 together with a test         -> `jmp x--` / `jmp y--`
  transitions        `m.next` = the next state in definition order (the last state wraps to the first, or to the state its transition names)
                     `m.next` = another state, with no other effect than side-set  -> `jmp`
  one test per state `m.If(x != 0)`, `x == 0`, `y != 0`, `y == 0`, `x != y`, `x == y`, an input pin or its complement, with the jump on one branch
                     and the next state on the other                              -> `jmp`
                     a state that stays until an input pin has a level           -> `wait`
  delay              states without any effect that follow an instruction are folded into its delay (`[n]`) when nothing jumps to them
  side-set           pin outputs assigned in a state that also does something else (or a jump / wait) use the side-set of the program

Refused: more than one operation per state (it would need two cycles), compound tests, tests on anything but X, Y and input pins, free-running
logic outside the FSM, combinational logic, several FSMs, submodules, wide constants, registers with a non-zero reset value.
"""
import os
import re

from amaranth.hdl import Const, Signal
from amaranth.lib import io

from .builder import (ALWAYS, CLEAR, INVERT, ISR, NONE, NOT_OSRE, NOT_X, NOT_Y, NULL, OSR, PINDIRS, PINS, PIN, REVERSE, SET, STATUS, WAIT, X, X_DEC, X_NE_Y, Y, Y_DEC, PioError, Program)

from . import data as D
from ._compat import Fragment, ast, fsm_state_names
from .platform import DirPin

__all__ = ["Compiled", "PioCompileError", "compile_design"]

MAX_SIDE_BITS = 4      # with the enable bit of an optional side-set (5 bits shared with the delay)


class PioCompileError(Exception):
    """The design is outside the subset that maps to PIO without changing its cycle behaviour.

    The message names the source line of the offending statement where Amaranth recorded one.
    """


_AMARANTH_DIR = os.path.dirname(os.path.dirname(os.path.abspath(ast.__file__)))
_DATA_PY = os.path.abspath(D.__file__)          # statements the shifter helpers create carry a location inside pioweave/data.py: the design's own line is used instead


def _user_loc(loc, fallback):
    """A source location in the user's design: statements created by m.next carry a location inside Amaranth itself, so those fall back to the state's."""
    if isinstance(loc, tuple) and len(loc) == 2 and not os.path.abspath(str(loc[0])).startswith(_AMARANTH_DIR) and os.path.abspath(str(loc[0])) != _DATA_PY:
        return loc
    return fallback


def _where(loc):
    if isinstance(loc, tuple) and len(loc) == 2:
        return "%s:%s: " % (os.path.basename(str(loc[0])), loc[1])
    return ""


# (regular expression searched in the message, hint appended to it): what to change for the most common mistakes. The message itself is never changed.
_HINTS = [
    (r"expected exactly one m\.FSM\(\)", "write the whole design as one `with m.FSM():` block; one FSM state is one PIO instruction"),
    (r"logic outside the FSM", "move the assignments into the states of the FSM; the PIO has no logic of its own outside the instructions"),
    (r"combinational logic", "assign with m.d.sync inside an FSM state (the value takes effect with the instruction); the PIO has no combinational paths"),
    (r"submodules are not supported", "use one Module: build the pins with platform.buffer() and the registers with platform.scratch(), not with submodules"),
    (r"clock domain .* is not supported", "use m.d.sync only; the whole design runs in the PIO state machine clock"),
    (r"m\.next names a state that is never defined", "check the spelling: every m.next must name a state created with m.State(\"NAME\")"),
    (r"statement .* is not supported", "use m.If / m.Else, m.d.sync assignments and m.next inside FSM states; see docs/designing-for-pio.rst for what compiles"),
    (r"is not a pin from platform\.request", "get pins from platform.buffer(m, ...) or platform.request(...) and the two registers from platform.scratch(\"x\") and platform.scratch(\"y\")"),
    (r"the test uses signal .* not an input pin", "a test can only look at an input pin or at X or Y; copy the value into x or y in an earlier state"),
    (r"compound tests", "split the test: check the first condition in one state and the second in the next state"),
    (r"this test cannot be done by one PIO instruction", "compare X or Y with 0, X with Y, or test one input pin; anything else needs several states"),
    (r"more than one test in a state", "put each test in a state of its own and chain the states with m.next"),
    (r"not consecutive GPIOs", "request the pins as one resource with consecutive GPIO numbers (for example gpio 4 to 7)"),
    (r"do not fit one 32-GPIO window", "keep all the pins of one design inside GPIO 0..31 or inside GPIO 16..47 (RP2350B only)"),
    (r"must start in the state that is defined first", "define the state the FSM should start in first; the reset state of the FSM must be the first one"),
    (r"output pin .* resets to \d+ but", "give the pin the init value 0, or assign the wanted level in the first state"),
    (r"needs two PIO instructions", "split the state in two and chain them with m.next; every PIO instruction takes one cycle, so the design needs one more cycle there"),
]


def _fail(msg, loc=None):
    for pattern, hint in _HINTS:
        if re.search(pattern, msg):
            msg += "\n  hint: " + hint
            break
    raise PioCompileError(_where(loc) + msg)


# ----------------------------------------------------------------------------- symbolic execution of one state
class Leaf:
    def __init__(self, effects, nxt, loc):
        self.effects, self.nxt, self.loc = effects, nxt, loc      # effects: {id(signal): (signal, rhs)}; nxt: state id or None (stay)


class Br:
    def __init__(self, cond, then, els, loc):
        self.cond, self.then, self.els, self.loc = cond, then, els, loc


class Compiled:
    """The result of `compile_design`: the PIO program and what the project writers and the emulator need to know about the design.

    Attributes
    ----------
    name : str
        The program name.
    chip : str
        The chip name of the platform.
    program : pioweave.builder.Program
        The compiled program.
    states : list of tuple
        ``(state name, instruction index or None)``; None when the state was folded into the delay of the instruction before it.
    set_pin, side_pin, out_pin, in_pin : pioweave.platform.PinInfo or None
        The pin groups driven by ``set pins``, driven by side-set, driven by ``out pins`` and read by ``in pins``.
    jmp_gpio : int or None
        The GPIO tested by ``jmp pin``.
    input_gpios, output_gpios : list of int
        The GPIOs the design reads and drives.
    out_shift, in_shift : str or None
        ``"right"`` or ``"left"`` when the program uses ``out`` or ``in`` (or autopull / autopush is on).
    autopull, autopush : bool
        The automatic transfers of the shifter.
    pull_thresh, push_thresh : int
        Their thresholds (32 when not configured).
    fifo_join : str
        ``"none"``, ``"tx"`` or ``"rx"``.
    status_sel, status_n : str or None, int
        The source of ``mov ..., status`` when the program uses it (``"tx"``, ``"rx"`` or ``"irq"``) and its level or flag number.
    in_count : int or None
        The number of input pins the program masks the IN mapping to (PIO version 1; set when it reads the pins with ``mov``).
    uses_irq, irq_rel : bool
        The program uses IRQ flags; one of the instructions has the ``rel`` form.
    uses_tx, uses_rx : bool
        The program has a ``pull`` (TX FIFO) or a ``push`` (RX FIFO).
    wrap_target_state : int
        The index of the state the program wraps to (the wrap target).
    dir_pins : list of pioweave.platform.PinInfo
        The pin groups with a direction register (``platform.directions``); their directions are a model state (design note 2f).
    dynamic_gpios : list of int
        The GPIOs of those groups. Their pad is the design's output where the pin is an output and the outside world elsewhere, so the simulations report the pad levels and
        the directions of these pins cycle by cycle (``dirs`` in `pioweave.project.amaranth_run`).
    side_pindirs : bool
        The side-set of the program writes pin directions (``.side_set N pindirs``) instead of pin levels.
    gpio_base : int
        The GPIO that PIO pin 0 maps to: 0, or 16 on the 48-GPIO parts (RP2350B, RP2354B).
    """

    def __init__(self):
        self.name = None
        self.chip = None
        self.program = None
        self.states = []          # [(state name, instruction index or None when folded into a delay)]
        self.set_pin = None       # PinInfo driven by `set pins`
        self.side_pin = None      # PinInfo driven by side-set
        self.jmp_gpio = None
        self.input_gpios = []
        self.output_gpios = []
        self.out_pin = None       # PinInfo driven by `out pins`
        self.in_pin = None        # PinInfo read by `in pins`
        self.out_shift = None     # "right" / "left" when the program uses OUT / IN
        self.in_shift = None
        self.autopull = self.autopush = False
        self.pull_thresh = self.push_thresh = 32
        self.fifo_join = "none"   # "tx" / "rx" when the shifter joins the FIFOs
        self.status_sel = None    # "tx" / "rx" when the program uses `mov ..., status`
        self.status_n = 0
        self.uses_irq = False     # the program uses an IRQ flag (irq, wait irq, or mov status from the flags)
        self.irq_rel = False      # ... with the rel form, which makes the state machine number matter (the model and the generated code use state machine 0)
        self.in_count = None      # the width the IN pins are masked to (PIO version 1), set when the program reads the pins with `mov`
        self.uses_tx = False
        self.uses_rx = False
        self.wrap_target_state = 0
        self.dir_pins = []        # pin groups with a direction register (platform.directions)
        self.side_pindirs = False
        self.gpio_base = 0        # GPIO that PIO pin 0 maps to: 0, or 16 on the 48-GPIO parts (RP2350B, RP2354B)

    @property
    def tx_depth(self):
        """The depth of the TX FIFO: 4, 8 when the FIFOs are joined for TX, 0 when they are joined for RX."""
        return {"none": 4, "tx": 8, "rx": 0}[self.fifo_join]

    @property
    def dynamic_gpios(self):
        return sorted(g for p in self.dir_pins for g in p.gpios)

    def dirs_init(self):
        """The initial pin directions the host configures, as a mask over the PIO's own pin numbers (fixed outputs 1, fixed inputs 0, a group with a direction register: its init).

        Returns
        -------
        int
            The mask (bit ``gpio - gpio_base``).
        """
        dynamic = set(self.dynamic_gpios)
        mask = 0
        for g in self.output_gpios:
            if g not in dynamic:
                mask |= 1 << self.pin(g)
        for p in self.dir_pins:
            for k, g in enumerate(p.gpios):
                if (p.dirs_init >> k) & 1:
                    mask |= 1 << self.pin(g)
        return mask

    def pin(self, gpio):
        """The PIO's own number of a real GPIO (relative to the GPIO base).

        Parameters
        ----------
        gpio : int
            A GPIO number.

        Returns
        -------
        int
            ``gpio - gpio_base``.
        """
        return gpio - self.gpio_base

    def sim_config(self):
        """Keys of a bitweave ``[[pio.NAME.sim]]`` case that configure the state machine like the generated C / MicroPython project. Pin numbers are the
        PIO's own (relative to `gpio_base`), as bitweave's simulator takes them.

        Returns
        -------
        dict
            The configuration, usable as keyword arguments of `pioweave.emulator.run_case` together with ``version=CHIP_VERSION[compiled.chip]``
            (`pioweave.builder.CHIP_VERSION`: ``run_case`` defaults to PIO version 0, which refuses a ``gpio_base`` of 16 and the RP2350-only keys);
            `pioweave.emulator.run_compiled` does that for you.

        Examples
        --------
        >>> from pioweave import PLATFORMS, compile_design, designs
        >>> from pioweave.builder import CHIP_VERSION
        >>> from pioweave.emulator import run_case
        >>> cls, chip, stim, cycles, opts = designs.entry("high_pins")
        >>> c = compile_design(cls(), PLATFORMS[chip](), "high_pins")
        >>> c.sim_config()["gpio_base"]
        16
        >>> r = run_case(c.program, 4, version=CHIP_VERSION[c.chip], **c.sim_config())
        """
        c = {}
        if self.set_pin:
            c["set_base"], c["set_count"] = self.pin(self.set_pin.gpios[0]), len(self.set_pin.gpios)
        if self.side_pin:
            c["sideset_base"] = self.pin(self.side_pin.gpios[0])
        if self.jmp_gpio is not None:
            c["jmp_pin"] = self.pin(self.jmp_gpio)
        c["dirs"] = self.dirs_init()
        if self.out_pin:
            c["out_base"], c["out_count"] = self.pin(self.out_pin.gpios[0]), len(self.out_pin.gpios)
        if self.in_pin:
            c["in_base"] = self.pin(self.in_pin.gpios[0])
        if self.out_shift:
            c["out_shift"] = self.out_shift
        if self.in_shift:
            c["in_shift"] = self.in_shift
        if self.autopull:
            c["autopull"] = True
        if self.autopush:
            c["autopush"] = True
        if self.fifo_join != "none":
            c["fifo_join"] = self.fifo_join
        if self.status_sel:
            c["status_sel"], c["status_n"] = self.status_sel, self.status_n
        if self.in_count is not None:
            c["in_count"] = self.in_count
        if self.pull_thresh != 32:
            c["pull_thresh"] = self.pull_thresh
        if self.push_thresh != 32:
            c["push_thresh"] = self.push_thresh
        if self.gpio_base:
            c["gpio_base"] = self.gpio_base
        return c


def _state_signal_and_cases(frag, sh=None):
    sync = list(frag.statements.get("sync", []))
    fsms = [s for s in sync if isinstance(s, ast.Switch) and isinstance(s.test, Signal) and fsm_state_names(s.test)]
    others = [s for s in sync if s not in fsms and not (sh is not None and _is_background_refill(s, sh))]
    if len(fsms) != 1:
        _fail("expected exactly one m.FSM() in the design, found %d" % len(fsms))
    if others:
        _fail("logic outside the FSM in the sync domain is not supported (it would need extra PIO instructions)", getattr(others[0], "src_loc", None))
    return fsms[0]


def _is_background_refill(stmt, sh):
    """The one statement outside the FSM that Shifter adds for autopull: ``if bg: osr, osr_n, tx_pop <- refill`` (the background refill of design note 2b)."""
    if sh.bg is None or not isinstance(stmt, ast.Switch):
        return False
    parts = stmt.test.parts if isinstance(stmt.test, ast.Concat) else [stmt.test]
    if len(parts) != 1 or parts[0] is not sh.bg:
        return False
    allowed = {id(sh.osr), id(sh.osr_n), id(sh.fifo.tx_pop)}
    return all(isinstance(a, ast.Assign) and id(a.lhs) in allowed for case in stmt.cases for a in case[1])


def _check_fragment(frag):
    for sf in frag.subfragments:
        if not any(isinstance(o, (io.Buffer, D.FifoModel, D.ShiftFlags, D.IrqModel)) for o in getattr(sf[0], "origins", None) or []):
            _fail("submodules are not supported (only amaranth.lib.io.Buffer for pins and the FIFO model, the flags and the IRQ model of platform.shifter()): write the design as one module with one FSM")
    for dom, stmts in frag.statements.items():
        if dom == "sync":
            continue
        if dom != "comb":
            _fail("clock domain %r is not supported: the PIO clock is the only domain (sync)" % dom)
        for s in stmts:     # m.FSM creates comb flags for m.ongoing(); anything else is real combinational logic
            ok = isinstance(s, ast.Assign) and isinstance(s.rhs, ast.Operator) and s.rhs.operator == "==" and any(
                isinstance(o, Signal) and fsm_state_names(o) for o in s.rhs.operands)
            if not ok:
                _fail("combinational logic (m.d.comb) is not supported", getattr(s, "src_loc", None))


# ----------------------------------------------------------------------------- conditions
class Ctx:
    def __init__(self, platform, state_sig):
        self.platform, self.state_sig = platform, state_sig
        self.pos = {}        # FSM state number (Amaranth numbers states by first mention) -> position in definition order = PIO address
        self.reg = {id(s): n for n, s in platform._scratch.items()}
        self.sh = getattr(platform, "_shifter", None)
        self.wait_pins = {}          # id(PinInfo) -> PinInfo of the input groups tested by `wait pin`
        self.side_pin = None
        self.side_pindirs = False    # the side-set writes pin directions (set by _dir_roles)

    def flag(self, sig):
        """``(operation codes, style)`` when `sig` is a flag of the data path (the TX-ready / RX-space flag of the FIFO model or a flag of `ShiftFlags`), else None.
        Style ``"body"``: the data statements are in the branch that completes; ``"both"``: they are in both branches."""
        if self.sh is not None:
            return self.sh.flag_info.get(id(sig))
        return None

    def regname(self, sig):
        return self.reg.get(id(sig))


def _flip(c):
    kind = c[0]
    if kind in ("pin", "flag", "osre", "pinbit"):
        return (kind, c[1], not c[2]) + tuple(c[3:])
    return ("cmp", "!=" if c[1] == "==" else "==", c[2], c[3])


def _norm_cond(part, truth, ctx, loc):
    """Normalise a 1-bit Amaranth condition to ('pin', signal, level) or ('cmp', op, a, b) (a, b: 'x', 'y' or int)."""
    if isinstance(part, Signal):
        if ctx.flag(part):
            return ("flag", part, truth)
        if ctx.sh is not None and ctx.sh.cond_flags.get(id(part)) == "osre":
            return ("osre", part, truth)
        if ctx.platform.pin_info(part) and len(part) == 1:
            return ("pin", part, truth)
        if ctx.regname(part) is not None:
            return ("cmp", "!=" if truth else "==", ctx.regname(part), 0)      # if(x): bool(x)
        _fail("the test uses signal %r, which is not an input pin or a scratch register" % part.name, loc)
    if isinstance(part, ast.Slice) and isinstance(part.value, Signal) and part.stop - part.start == 1:
        info = ctx.platform.pin_info(part.value)
        if info is not None and part.value is info.i:
            if len(part.value) == 1:
                return ("pin", part.value, truth)
            return ("pinbit", part.value, truth, part.start)       # one bit of an input group: wait pin
        _fail("the test uses a bit of signal %r, which is not an input pin" % part.value.name, loc)
    if isinstance(part, ast.Operator):
        op, a = part.operator, part.operands
        if op == "~" and len(a) == 1 and isinstance(a[0], (Signal, ast.Slice)) and len(a[0]) == 1:
            return _norm_cond(a[0], not truth, ctx, loc)
        if op in ("b", "r|") and len(a) == 1 and isinstance(a[0], (Signal, ast.Slice)):
            return _norm_cond(a[0], truth, ctx, loc)
        if op in ("==", "!=") and len(a) == 2:
            l, r = a
            if isinstance(l, Const):
                l, r = r, l
            if isinstance(l, ast.Slice) and isinstance(r, Const) and r.value in (0, 1) and l.stop - l.start == 1:
                return _norm_cond(l, (r.value == 1) == ((op == "==") == truth), ctx, loc)
            if isinstance(l, Signal) and isinstance(r, Const):
                eq = (op == "==") == truth
                if ctx.platform.pin_info(l) and len(l) == 1 and r.value in (0, 1):
                    return ("pin", l, bool(r.value) == eq)
                if ctx.regname(l) is not None:
                    if r.value != 0:
                        _fail("PIO can only compare X or Y with 0 (jmp !x / jmp x--), not with %d" % r.value, loc)
                    return ("cmp", "==" if eq else "!=", ctx.regname(l), 0)
            if isinstance(l, Signal) and isinstance(r, Signal) and ctx.regname(l) and ctx.regname(r) and ctx.regname(l) != ctx.regname(r):
                eq = (op == "==") == truth
                return ("cmp", "==" if eq else "!=", "x", "y")
    _fail("this test cannot be done by one PIO instruction (X or Y against 0, X against Y, an input pin)", loc)


def _case_conds(sw, ctx, loc):
    parts = sw.test.parts if isinstance(sw.test, ast.Concat) else [sw.test]
    if any(len(p) != 1 for p in parts):
        _fail("m.Switch / m.Case on multi-bit values is not supported; use m.If with a simple test", loc)
    out = []
    for case in sw.cases:
        pats = case[0]
        if pats is None:
            out.append((None, case[1]))
            continue
        pat = pats[0]
        lits = []
        k = len(parts)
        for j in range(k):
            ch = pat[k - 1 - j]
            if ch in "01":
                lits.append(_norm_cond(parts[j], ch == "1", ctx, loc))
        if len(lits) != 1:
            _fail("compound tests (and / or) need several PIO instructions", loc)
        out.append((lits[0], case[1]))
    return out


def _exec(stmts, ctx, nxt, effects, loc):
    if not stmts:
        return Leaf(dict(effects), nxt, loc)
    s, rest = stmts[0], list(stmts[1:])
    loc = _user_loc(getattr(s, "src_loc", None), loc)
    if isinstance(s, ast.Assign):
        if s.lhs is ctx.state_sig:
            if not isinstance(s.rhs, Const):
                _fail("m.next must name a state", loc)
            if s.rhs.value not in ctx.pos:
                _fail("m.next names a state that is never defined with m.State", loc)
            return _exec(rest, ctx, ctx.pos[s.rhs.value], effects, loc)
        if not isinstance(s.lhs, Signal):
            _fail("assignments to parts of a signal are not supported; assign the whole signal", loc)
        eff = dict(effects)
        eff[id(s.lhs)] = (s.lhs, s.rhs)
        return _exec(rest, ctx, nxt, eff, loc)
    if isinstance(s, ast.Switch):
        chain = _case_conds(s, ctx, loc)
        default = None
        branches = []
        for cond, body in chain:
            if cond is None:
                default = body
            else:
                branches.append((cond, body))
        tree = _exec(list(default) + rest, ctx, nxt, effects, loc) if default is not None else _exec(rest, ctx, nxt, effects, loc)
        for cond, body in reversed(branches):
            tree = Br(cond, _exec(list(body) + rest, ctx, nxt, effects, loc), tree, loc)
        return tree
    _fail("statement %s is not supported" % type(s).__name__, loc)


# ----------------------------------------------------------------------------- effects
def _const(rhs, loc):
    if isinstance(rhs, Const):
        return rhs.value
    return None


def _expected_owned(sh, code, mov=None, flagged=False):
    """The shifter signals the helper statements of operation `code` assign (the compiler accepts nothing else on them). `mov`: (dst, src, op) names of a `mov`."""
    f = sh.fifo
    if code in (D.PULL_BLOCK, D.PULL_NOBLOCK):
        return [sh.op, sh.osr, sh.osr_n, f.tx_pop] + ([sh.arg] if flagged else [])
    if code in (D.PUSH_BLOCK, D.PUSH_NOBLOCK):
        return [sh.op, sh.isr, sh.isr_n, f.rx_data, f.rx_push] + ([sh.arg] if flagged else [])
    if code in D.OUT_CODES:
        return [sh.op, sh.arg, sh.osr, sh.osr_n] + ([sh.isr, sh.isr_n] if code == D.OUT_ISR else []) + ([f.tx_pop] if sh.autopull else [])
    if code in (D.IRQ_SET, D.IRQ_CLEAR, D.IRQ_WAIT, D.WAIT_IRQ):
        flag, pol = mov                     # for the IRQ operations `mov` carries (flag, polarity)
        if code == D.IRQ_SET:
            return [sh.op, sh.arg, sh.irq_model.set_tog[flag]]
        if code == D.IRQ_CLEAR:
            return [sh.op, sh.arg, sh.irq_model.clr_tog[flag]]
        if code == D.IRQ_WAIT:
            return [sh.op, sh.arg, sh.irq_model.set_tog[flag], sh.irq_pend]
        return [sh.op, sh.arg] + ([sh.irq_model.clr_tog[flag]] if pol else [])
    if code == D.MOV:
        dst = mov[0]
        if dst == "isr":
            return [sh.op, sh.arg, sh.isr, sh.isr_n]
        if dst == "osr":
            return [sh.op, sh.arg, sh.osr, sh.osr_n] + ([f.tx_pop] if sh.autopull else [])
        return [sh.op, sh.arg]
    return [sh.op, sh.arg, sh.isr, sh.isr_n] + ([sh.in_pend, f.rx_data, f.rx_push] if sh.autopush else [])


def _data_op(effects, ctx, loc):
    """Split the data-path operation (pull / push / out / in) off the effects of a leaf. Returns (data, remaining effects); data is None when the state
    does not touch the shifters. data: {code, name, n, pin} (pin: the PinInfo `out pins` drives)."""
    sh = ctx.sh
    if sh is None:
        return None, effects
    owned = [sh.op, sh.arg, sh.osr, sh.osr_n, sh.isr, sh.isr_n, sh.in_pend, sh.irq_pend, sh.fifo.tx_pop, sh.fifo.rx_push, sh.fifo.rx_data]
    if sh.irq_model is not None:
        owned += sh.irq_model.set_tog + sh.irq_model.clr_tog
    ids = {id(x) for x in owned}
    got = {k for k in effects if k in ids}
    if not got:
        return None, effects
    if id(sh.op) not in got:
        _fail("the OSR, ISR or FIFO registers are assigned without an operation: use the sh.pull / sh.push / sh.out / sh.in_ helpers", loc)
    marker = effects[id(sh.op)][1]
    if not isinstance(marker, Const) or marker.value not in D.NAMES:
        _fail("the operation marker of the shifter is not set by a helper", loc)
    code = marker.value
    mov = None
    irq = None
    if code in (D.IRQ_SET, D.IRQ_CLEAR, D.IRQ_WAIT, D.WAIT_IRQ):
        arg = effects.get(id(sh.arg), (None, None))[1]
        if not isinstance(arg, Const) or sh.irq_model is None:
            _fail("the operand code of %s is not set by the helper" % D.NAMES[code], loc)
        irq = {"flag": arg.value & 7, "rel": bool(arg.value & 8), "pol": (arg.value >> 4) & 1}
        mov = (irq["flag"], irq["pol"])
    if code == D.MOV:
        arg = effects.get(id(sh.arg), (None, None))[1]
        if not isinstance(arg, Const):
            _fail("the operand code of mov is not set by the helper sh.mov", loc)
        rev = lambda table, v: next((k for k, c in table.items() if c == v), None)      # noqa: E731
        mov = (rev(D.MOV_DST, arg.value & 7), rev(D.MOV_SRC, (arg.value >> 3) & 7), rev(D.MOV_OP, (arg.value >> 6) & 3))
        if None in mov:
            _fail("the operand code of mov is not set by the helper sh.mov", loc)
    flagged = code in (D.PULL_BLOCK, D.PULL_NOBLOCK, D.PUSH_BLOCK, D.PUSH_NOBLOCK) and id(sh.arg) in got
    if got != {id(x) for x in _expected_owned(sh, code, mov, flagged)}:
        _fail("the statements on the shifter registers do not match `%s`: use the helper methods, one operation per state" % D.NAMES[code], loc)
    n = None
    if code in D.OUT_CODES or code in D.IN_CODES:
        arg = effects[id(sh.arg)][1]
        if not isinstance(arg, Const):
            _fail("the bit count of %s must be a constant" % D.NAMES[code], loc)
        n = arg.value
    rest = {k: v for k, v in effects.items() if k not in ids}
    if flagged and not (isinstance(effects[id(sh.arg)][1], Const) and effects[id(sh.arg)][1].value == 1):
        _fail("the operand code of %s is not set by the helper" % D.NAMES[code], loc)
    base_name = D.NAMES[code].replace("pull ", "pull ifempty ").replace("push ", "push iffull ") if flagged else D.NAMES[code]
    data = {"irq": irq, "flag": flagged, "code": code, "name": base_name if mov is None or irq is not None else "mov %s, %s%s" % (mov[0], {"none": "", "invert": "!", "reverse": "::"}[mov[2]], mov[1]), "n": n, "pin": None, "mov": mov}
    reg_dst = {D.OUT_X: "x", D.OUT_Y: "y"}.get(code) or (mov[0] if mov is not None and mov[0] in ("x", "y") else None)
    if reg_dst is not None:
        reg = ctx.platform._scratch[reg_dst]
        if rest.pop(id(reg), None) is None:
            _fail("%s: the destination register is not assigned" % data["name"], loc)
    elif code == D.OUT_PINS or (mov is not None and mov[0] == "pins"):
        found = [(k, info) for k, (sig, rhs) in rest.items() for info in [ctx.platform.pin_info(sig)] if info is not None and sig is info.o and not isinstance(rhs, Const)]
        if len(found) != 1:
            _fail("%s: the destination pin is not assigned the shifted bits" % data["name"], loc)
        rest.pop(found[0][0])
        data["pin"] = found[0][1]
    elif code == D.OUT_PINDIRS or (mov is not None and mov[0] == "pindirs"):
        found = [(k, ctx.platform.dirs_info(sig)) for k, (sig, rhs) in rest.items() if ctx.platform.dirs_info(sig) is not None and not isinstance(rhs, Const)]
        if len(found) != 1:
            _fail("%s: the destination direction register is not assigned the shifted bits" % data["name"], loc)
        rest.pop(found[0][0])
        data["pin"] = found[0][1].info
    return data, rest


def _split_effects(effects, ctx, loc):
    """Return (pin_assignments {pin info: value}, register effect or None, data-path operation or None). Register effects: ('set', 'x', v) ('mov', 'x', 'y', invert) ('dec', 'x')."""
    data, effects = _data_op(effects, ctx, loc)
    pins, regs = {}, []
    for sig, rhs in effects.values():
        dp = ctx.platform.dirs_info(sig)
        if dp is not None:
            v = _const(rhs, loc)
            if v is None:
                _fail("the direction register of %s_%s can only be assigned a constant, or the bits of sh.out / sh.mov" % (dp.name, dp.number), loc)
            pins[id(dp)] = (dp, v & ((1 << len(sig)) - 1))
            continue
        info = ctx.platform.pin_info(sig)
        if info is not None and sig is info.o:
            v = _const(rhs, loc)
            if v is None:
                _fail("output pin %s_%s can only be assigned a constant, or the bits of sh.out(m, pin)" % (info.name, info.number), loc)
            pins[id(info)] = (info, v & ((1 << len(sig)) - 1))
            continue
        if info is not None:
            _fail("signal %r is an input pin and cannot be assigned" % sig.name, loc)
        name = ctx.regname(sig)
        if name is None:
            _fail("signal %r is not a pin from platform.request() or a scratch register from platform.scratch(); it would need PIO storage that does not exist" % sig.name, loc)
        v = _const(rhs, loc)
        if v is not None:
            if not 0 <= v <= 31:
                _fail("%s can only be set to 0..31 by `set` (got %d)" % (name, v), loc)
            regs.append(("set", name, v))
            continue
        if isinstance(rhs, Signal):
            if rhs is sig:
                continue
            if ctx.regname(rhs) is None:
                _fail("%s can only be assigned a constant, the other scratch register or its complement" % name, loc)
            regs.append(("mov", name, ctx.regname(rhs), False))
            continue
        if isinstance(rhs, ast.Operator):
            if rhs.operator == "~" and isinstance(rhs.operands[0], Signal) and ctx.regname(rhs.operands[0]):
                regs.append(("mov", name, ctx.regname(rhs.operands[0]), True))
                continue
            if (rhs.operator == "-" and len(rhs.operands) == 2 and rhs.operands[0] is sig and isinstance(rhs.operands[1], Const)
                    and rhs.operands[1].value == 1 and len(sig) == 32):
                regs.append(("dec", name))
                continue
        _fail("the value assigned to %s is not something one PIO instruction computes (a constant 0..31, the other register, its complement, or itself minus 1)" % name, loc)
    if len(regs) > 1:
        _fail("two register operations in one state need two PIO instructions, which would change the cycle behaviour: put them in different states", loc)
    return pins, (regs[0] if regs else None), data


# ----------------------------------------------------------------------------- the compiler
def compile_design(top, platform, name="top"):
    """Compile an Amaranth design to a PIO program.

    The design must be written in the restricted style described in the module documentation: one FSM, one state per PIO instruction. The compiler elaborates
    the design with `platform`.

    Parameters
    ----------
    top : amaranth.hdl.Elaboratable
        The design.
    platform : pioweave.platform.RP2040PioPlatform
        A fresh instance of one of the PIO platforms.
    name : str
        The program name.

    Returns
    -------
    Compiled
        The program and the pin and FIFO information.

    Raises
    ------
    PioCompileError
        If the design is outside the accepted subset; the message names the source line.

    Examples
    --------
    >>> from pioweave.compile import compile_design
    >>> from pioweave.designs import SquareWave
    >>> from pioweave.platform import RP2040PioPlatform
    >>> compiled = compile_design(SquareWave(), RP2040PioPlatform(), "square")
    >>> print(compiled.program.pio_text(), end="")
    .program square
    .pio_version 0
        set pins, 1 [1]
        set pins, 0 [1]
    >>> [hex(w) for w in compiled.program.words()]
    ['0xe101', '0xe100']
    >>> compiled.states
    [('HIGH', 0), ('HIGH_HOLD', None), ('LOW', 1), ('LOW_HOLD', None)]
    """
    frag = Fragment.get(top, platform)
    _check_fragment(frag)
    platform.bind_buffers(frag)
    sw = _state_signal_and_cases(frag, getattr(platform, "_shifter", None))
    state_sig = sw.test
    ctx = Ctx(platform, state_sig)
    if ctx.sh is not None and ctx.sh.unentered():
        _fail("this helper call can stall (autopull for `out`, autopush for `in`, `wait`-type operations) and must be written `with sh.<operation>(m): m.next = ...` so that the "
              "state stays while it stalls; as a plain call the Amaranth design and the PIO program would differ", ctx.sh.unentered()[0])
    # program order = the order the states are defined in the source (the cases of the Switch); Amaranth's state numbers follow first mention
    order = [int(case[0][0] or "0", 2) for case in sw.cases]
    bodies = {int(case[0][0] or "0", 2): case[1] for case in sw.cases}
    case_loc = {int(case[0][0] or "0", 2): case[2] if len(case) > 2 else None for case in sw.cases}
    n = len(order)
    if order[0] != 0 or state_sig.init != 0:
        _fail("the FSM must start in the state that is defined first")
    ctx.pos = {sid: k for k, sid in enumerate(order)}
    state_names = fsm_state_names(state_sig)
    names = [state_names[sid].rsplit("/", 1)[0] for sid in order]
    trees = [_exec(bodies[sid], ctx, None, {}, _user_loc(case_loc[sid], None)) for sid in order]
    for sig in list(platform._scratch.values()):
        if sig.init != 0:
            _fail("scratch register %s resets to %d but the PIO registers reset to 0: assign the value in the first state" % (sig.name, sig.init))
    for info in platform._pio_pins:
        if info.o is not None and info.o.init != 0:
            _fail("output pin %s_%s resets to %d but the PIO output level resets to 0" % (info.name, info.number, info.o.init))
    return _build(platform, name, n, names, trees, ctx)


def _succ(i, n, wrap):
    return i + 1 if i + 1 < n else wrap


class Op:
    def __init__(self, kind, **kw):
        self.kind = kind
        self.__dict__.update(kw)
        self.side = kw.get("side")
        self.delay = 0
        self.label = False


def _contiguous(info, what):
    g = info.gpios
    if any(g[k + 1] != g[k] + 1 for k in range(len(g) - 1)):
        _fail("%s pins %s_%s are not consecutive GPIOs %s: PIO addresses a pin group by base and count" % (what, info.name, info.number, g))
    return g[0]


def _leaves(t):
    return _leaves(t.then) + _leaves(t.els) if isinstance(t, Br) else [t]


def _roles(platform, trees, ctx):
    """Decide which output pin drives `set pins` and which drives the side-set.
    A pin assigned alone in a state is a `set` pin (at most one). In a state with two pins, one of them being the side-set pin (forced with
    platform.pio_role), the other is the `set` pin. A pin assigned in a state that also does something else (a register operation, a test, a jump or
    wait) is a side-set pin (at most one). A pin used both ways is in both groups."""
    outs = [p for p in platform._pio_pins if p.o is not None]
    states = []                     # (pins list, has register effect, is a test)
    for t in trees:
        for lf in _leaves(t):
            pins, reg, data = _split_effects(lf.effects, ctx, lf.loc)
            has_dirs = any(isinstance(info, DirPin) for info, _ in pins.values())          # a pin direction assignment is the operation (or the side-set) of the state
            states.append(([info for info, _ in pins.values() if not isinstance(info, DirPin)], reg is not None or data is not None or has_dirs, isinstance(t, Br)))
    setp = [p for p in outs if p.role == "set"]
    side = [p for p in outs if p.role == "side"]
    for p in outs:
        if p.role is None and any(pins == [p] and not reg and not br for pins, reg, br in states):
            setp.append(p)
    for _ in range(2):
        for pins, reg, br in states:
            if reg or br or len(pins) != 2:
                continue
            known_side = [p for p in pins if p in side]
            other = [p for p in pins if p not in side]
            if len(known_side) == 1 and len(other) == 1 and other[0].role is None and other[0] not in setp:
                setp.append(other[0])
    for pins, reg, br in states:
        for p in pins:
            if p in side or p.role == "set":
                continue
            if br or reg or (len(pins) > 1 and p not in setp):
                side.append(p)
            elif len(pins) > 1 and p in setp and sum(1 for q in pins if q in setp) > 1:
                _fail("states drive more than one `set` pin together (%s): merge them into one multi-bit resource" % ", ".join("%s_%s" % (q.name, q.number) for q in pins))
    for p in setp:
        for pins, reg, br in states:
            if p in pins and (br or reg) and p not in side and p.role != "set":
                side.append(p)       # a set pin used where it cannot be the operation drives the side-set as well
    if len(setp) > 1:
        _fail("more than one output signal is driven by `set pins` (%s): merge them into one multi-bit resource or call platform.pio_role(pin, 'side') for all but one" % ", ".join("%s_%s" % (p.name, p.number) for p in setp))
    if len(side) > 1:
        _fail("more than one output signal is driven by side-set (%s): use one multi-bit resource, or force the roles with platform.pio_role(pin, 'set' | 'side')" % ", ".join("%s_%s" % (p.name, p.number) for p in side))
    return (setp[0] if setp else None), (side[0] if side else None)


def _build(platform, name, n, names, trees, ctx):
    set_pin, side_pin = _roles(platform, trees, ctx)
    set_pin, side_pin, side_pindirs = _dir_roles(trees, ctx, set_pin, side_pin)
    ctx.side_pin = side_pin
    ctx.side_pindirs = side_pindirs
    dataops = [d for t in trees for lf in _leaves(t) for d in [_split_effects(lf.effects, ctx, lf.loc)[2]] if d]
    codes = {d["code"] for d in dataops}
    outs_used = {id(d["pin"]): d["pin"] for d in dataops if d["pin"] is not None}
    if len(outs_used) > 1:
        _fail("`out pins` drives more than one pin group (%s); PIO has one OUT group per state machine" % ", ".join("%s_%s" % (p.name, p.number) for p in outs_used.values()))
    out_pin = next(iter(outs_used.values()), None)
    if side_pin is not None and len(side_pin.gpios) > MAX_SIDE_BITS:
        _fail("side-set pin group %s_%s has %d bits; with the enable bit at most %d fit" % (side_pin.name, side_pin.number, len(side_pin.gpios), MAX_SIDE_BITS))
    if set_pin is not None and len(set_pin.gpios) > 5:
        _fail("set pin group %s_%s has %d bits; `set pins` writes at most 5" % (set_pin.name, set_pin.number, len(set_pin.gpios)))
    # the wrap target: the last state's transition when it is a plain instruction
    wrap_t = 0
    last = trees[-1]
    if isinstance(last, Leaf) and last.nxt is not None and last.nxt != n - 1:
        pins, reg, data = _split_effects(last.effects, ctx, last.loc)
        has_main = reg is not None or data is not None or any(info is set_pin or (isinstance(info, DirPin) and info.info is set_pin) for info, _ in pins.values())
        if has_main:
            wrap_t = last.nxt
    ops = [None] * n
    jmp_gpios = set()
    targets = {0}
    for i in range(n):
        ops[i] = _classify(i, n, trees[i], wrap_t, names, set_pin, side_pin, ctx, jmp_gpios)
    in_pin = None
    srcs = {id(ctx.platform.pin_info(sg)): ctx.platform.pin_info(sg) for sg in (ctx.sh.in_sources if ctx.sh is not None else [])}
    srcs.update(ctx.wait_pins)          # filled while the states were classified
    if len(srcs) > 1:
        _fail("`in pins`, `mov ..., pins` or `wait pin` reads %d different pin groups; PIO has one IN group per state machine" % len(srcs))
    if srcs:
        in_pin = next(iter(srcs.values()))
    referenced = set()
    for i in range(n):
        for t in ops[i].targets:
            targets.add(t)
            referenced.add(t)
    if wrap_t != 0:
        targets.add(wrap_t)
    if len(jmp_gpios) > 1:
        _fail("jmp pin tests use different input pins; PIO has one JMP pin per state machine")
    # the PIO sees 32 GPIOs: GPIOBASE 0 (GPIO 0-31) or, on the 48-GPIO parts, 16 (GPIO 16-47)
    used = set(jmp_gpios)
    for p in (set_pin, side_pin, out_pin, in_pin):
        if p is not None:
            used.update(p.gpios)
    for p in platform._pio_pins:
        if p.dirs is not None:
            used.update(p.gpios)
    used.update(op.gpio for op in ops if op.kind == "wait")
    gpio_base = next((b for b in platform.gpio_bases if all(b <= g <= b + 31 for g in used)), None)
    if gpio_base is None:
        _fail("the pins the program uses (GPIO %s) do not fit one 32-GPIO window of the PIO (GPIO base %s)" % (", ".join(str(g) for g in sorted(used)),
                                                                                           " or ".join(str(b) for b in platform.gpio_bases)))
    # delay folding
    side_bits = len(side_pin.gpios) if side_pin is not None else 0
    delay_max = (1 << (5 - side_bits - (1 if side_bits else 0))) - 1
    folded = [False] * n
    i = 0
    while i < n:
        op = ops[i]
        if op.foldable:
            j = i + 1
            while j < n and ops[j].pure_nop and j not in targets and op.delay < delay_max:
                op.delay += 1
                folded[j] = True
                j += 1
            i = j
        else:
            i += 1
    # emit
    try:
        prog = Program(_ident(name), chip=platform.chip, side_set=side_bits, side_opt=side_bits > 0, side_pindirs=side_pindirs, emit_version=True)
        labels = {}
        used = set()
        for i in range(n):
            if i in referenced and not folded[i]:
                lab = "s_" + _ident(names[i])
                while lab in used:
                    lab += "_"
                used.add(lab)
                labels[i] = lab
        index = {}
        count = 0
        for i in range(n):
            if folded[i]:
                continue
            op = ops[i]
            if i == wrap_t and wrap_t != 0:
                prog.wrap_target()
            if i in labels:
                prog.label(labels[i])
            _emit(prog, op, labels, names)
            index[i] = count
            count += 1
    except PioError as e:
        raise PioCompileError(str(e))
    c = Compiled()
    c.name, c.chip, c.program = name, platform.chip, prog
    c.states = [(names[i], None if folded[i] else index[i]) for i in range(n)]
    c.set_pin, c.side_pin = set_pin, side_pin
    c.side_pindirs = side_pindirs
    c.dir_pins = [p for p in platform._pio_pins if p.dirs is not None]
    c.jmp_gpio = next(iter(jmp_gpios)) if jmp_gpios else None
    c.wrap_target_state = wrap_t
    c.gpio_base = gpio_base
    outs = []
    for p in (set_pin, side_pin):
        if p is not None:
            _contiguous(p, "output")
            outs.extend(p.gpios)
    if out_pin is not None:
        _contiguous(out_pin, "output")
        outs.extend(out_pin.gpios)
    if in_pin is not None:
        _contiguous(in_pin, "input")
    c.out_pin, c.in_pin = out_pin, in_pin
    sh = ctx.sh
    out_codes, in_codes = set(D.OUT_CODES), set(D.IN_CODES)
    movs = [d["mov"] for d in dataops if d["code"] == D.MOV]
    irqs = [d["irq"] for d in dataops if d["irq"]]
    c.irq_rel = any(i["rel"] for i in irqs)
    if sh is not None:
        if any(src == "status" for _, src, _ in movs):
            c.status_sel, c.status_n = sh.status, sh.status_n
        c.uses_irq = bool(irqs) or c.status_sel == "irq"
        if any(src == "pins" for _, src, _ in movs) and in_pin is not None:
            c.in_count = len(in_pin.gpios)
        c.autopull, c.autopush, c.pull_thresh, c.push_thresh = sh.autopull, sh.autopush, sh.pull_thresh, sh.push_thresh
        c.fifo_join = sh.fifo_join
        if codes & out_codes or sh.autopull:
            c.out_shift = "right" if sh.out_right else "left"
        if codes & in_codes or sh.autopush:
            c.in_shift = "right" if sh.in_right else "left"
    c.uses_tx = (bool(codes & {D.PULL_BLOCK, D.PULL_NOBLOCK}) or bool(sh is not None and sh.autopull and codes & out_codes)) and c.fifo_join != "rx"
    c.uses_rx = (bool(codes & {D.PUSH_BLOCK, D.PUSH_NOBLOCK}) or bool(sh is not None and sh.autopush and codes & in_codes)) and c.fifo_join != "tx"
    outs.extend(c.dynamic_gpios)
    c.output_gpios = sorted(set(outs))
    c.input_gpios = sorted({g for p in platform._pio_pins if p.i is not None for g in p.gpios})
    return c


def _ident(s):
    s = re.sub(r"[^0-9A-Za-z_]", "_", s)
    return s if s and not s[0].isdigit() else "_" + s


def _is_side(info, side_pin):
    """True for the side-set pin group itself, or (side-set pindirs) for its direction register."""
    return info is side_pin or (isinstance(info, DirPin) and info.info is side_pin)


def _side_of(pins, side_pin, loc):
    """Side-set value among the pin assignments (those that are not the `set` operation)."""
    for info, v in pins.values():
        if _is_side(info, side_pin):
            return v
    return None


def _dir_roles(trees, ctx, set_pin, side_pin):
    """Place the assignments to direction registers (design note 2f): one alone in a state is `set pindirs` and needs the SET group to be its pin group; one in a state with
    another operation or a test is the side-set, and then the side-set of the whole program writes pin directions (`.side_set N pindirs`).
    Returns (set_pin, side_pin, side_pindirs)."""
    set_groups, side_groups = {}, {}
    for t in trees:
        for lf in _leaves(t):
            pins, reg, data = _split_effects(lf.effects, ctx, lf.loc)
            for info, _ in pins.values():
                if isinstance(info, DirPin):
                    (side_groups if reg is not None or data is not None or isinstance(t, Br) else set_groups)[id(info.info)] = info.info
    names = lambda groups: ", ".join("%s_%s" % (g.name, g.number) for g in groups.values())      # noqa: E731
    if len(set_groups) > 1:
        _fail("`set pindirs` is used on more than one pin group (%s); PIO has one SET group per state machine" % names(set_groups))
    if len(side_groups) > 1:
        _fail("the side-set writes the directions of more than one pin group (%s); PIO has one side-set group per state machine" % names(side_groups))
    if set_groups:
        g = next(iter(set_groups.values()))
        if set_pin is not None and set_pin is not g:
            _fail("`set pins` drives %s_%s but `set pindirs` changes %s_%s; PIO has one SET group per state machine" % (set_pin.name, set_pin.number, g.name, g.number))
        set_pin = g
    side_pindirs = bool(side_groups)
    if side_groups:
        g = next(iter(side_groups.values()))
        if side_pin is not None:
            _fail("the side-set of one program writes either pin levels (%s_%s) or pin directions (%s_%s), not both" % (side_pin.name, side_pin.number, g.name, g.number))
        side_pin = g
    return set_pin, side_pin, side_pindirs


def _classify(i, n, tree, wrap_t, names, set_pin, side_pin, ctx, jmp_gpios):
    succ = _succ(i, n, wrap_t)
    if isinstance(tree, Leaf):
        pins, reg, data = _split_effects(tree.effects, ctx, tree.loc)
        dir_assign = [(info, v) for info, v in pins.values() if isinstance(info, DirPin)]
        pins = {k: x for k, x in pins.items() if not isinstance(x[0], DirPin)}
        nxt = i if tree.nxt is None else tree.nxt
        set_assign = [(info, v) for info, v in pins.values() if info is set_pin and info is not side_pin]
        both = [(info, v) for info, v in pins.values() if info is set_pin and info is side_pin]
        side = None
        main_pin = None
        for info, v in pins.values():
            if info is side_pin and info is not set_pin:
                side = v
        # a signal in both groups: the `set` operation when nothing else is going on, else side-set
        for info, v in both:
            if reg is None and data is None and not set_assign and nxt == succ:
                main_pin = (info, v)
            else:
                side = v
        if set_assign:
            main_pin = set_assign[0]
        for info, v in pins.values():
            if info is not set_pin and info is not side_pin:
                _fail("output pin %s_%s has no PIO role (internal)" % (info.name, info.number), tree.loc)
        if len([1 for x in (main_pin is not None, reg is not None and reg[0] != "dec") if x]) > 1 or (main_pin and reg and reg[0] == "dec"):
            _fail("a state that sets pins and also changes a register needs two PIO instructions; put them in different states (side-set can carry a pin assignment)", tree.loc)
        if len(set_assign) > 1:
            _fail("internal: two set operations", tree.loc)
        main_dir = None
        if len(dir_assign) > 1:
            _fail("a state that changes the directions of two pin groups needs two PIO instructions; put them in different states", tree.loc)
        if dir_assign:
            dp, dv = dir_assign[0]
            if dp.info is set_pin and main_pin is None and reg is None and data is None:
                main_dir = dv                                      # set pindirs
            elif dp.info is side_pin and ctx.side_pindirs and side is None:
                side = dv                                          # the side-set writes pin directions
            else:
                _fail("a state that changes the directions of %s_%s and also sets a pin or does another operation needs two PIO instructions; put them in different states" % (dp.name, dp.number), tree.loc)
        if data is not None:
            if main_pin is not None or reg is not None:
                _fail("a state that does `%s` and also sets a pin or changes a register needs two PIO instructions; put them in different states (side-set can carry a pin assignment)" % data["name"], tree.loc)
            if nxt != succ:
                _fail("a state that does `%s` and then jumps to another state needs two PIO instructions; put the jump in its own state" % data["name"], tree.loc)
            return _data_instruction(data, side, ctx, tree.loc)
        if nxt == succ:
            if reg is not None and reg[0] == "dec":
                return Op("jmp", cond=("x--" if reg[1] == "x" else "y--"), target=succ, side=side, targets=[succ], foldable=False, pure_nop=False)
            if main_dir is not None:
                return Op("set_pindirs", value=main_dir, side=side, targets=[], foldable=True, pure_nop=False)
            if main_pin is not None:
                return Op("set_pins", value=main_pin[1], side=side, targets=[], foldable=True, pure_nop=False)
            if reg is not None and reg[0] == "set":
                return Op("set_reg", reg=reg[1], value=reg[2], side=side, targets=[], foldable=True, pure_nop=False)
            if reg is not None and reg[0] == "mov":
                return Op("mov", dst=reg[1], src=reg[2], inv=reg[3], side=side, targets=[], foldable=True, pure_nop=False)
            return Op("nop", side=side, targets=[], foldable=True, pure_nop=(side is None))
        if main_pin is not None or reg is not None or main_dir is not None:
            _fail("a state that changes %s and then jumps to another state needs two PIO instructions; put the jump in its own state" % (
                "a pin" if main_pin else "a register" if reg is not None else "pin directions"), tree.loc)
        return Op("jmp", cond="always", target=nxt, side=side, targets=[nxt], foldable=False, pure_nop=False)
    return _classify_branch(i, n, tree, succ, names, set_pin, side_pin, ctx, jmp_gpios)


def _classify_branch(i, n, br, succ, names, set_pin, side_pin, ctx, jmp_gpios):
    if isinstance(br.then, Br) or isinstance(br.els, Br):
        _fail("more than one test in a state: PIO executes one test per instruction (Elif chains need one state per test)", br.loc)
    parts = []
    for leaf in (br.then, br.els):
        pins, reg, data = _split_effects(leaf.effects, ctx, leaf.loc)
        parts.append((leaf, pins, reg, data))
    (tl, tpins, treg, tdata), (el, epins, ereg, edata) = parts
    tn = i if tl.nxt is None else tl.nxt
    en = i if el.nxt is None else el.nxt
    cond = br.cond
    if cond[0] == "flag":
        return _classify_flag(i, succ, br, parts, tn, en, ctx)
    if tdata is not None or edata is not None:
        _fail("an operation on the FIFOs or shifters (%s) cannot be in a state with a test" % (tdata or edata)["name"], br.loc)
    # side-set values must agree in both branches (a single instruction applies one value)
    tside = _side_of(tpins, side_pin, br.loc)
    eside = _side_of(epins, side_pin, br.loc)
    for info, _ in list(tpins.values()) + list(epins.values()):
        if not _is_side(info, side_pin):
            _fail("pins that are set inside a test need a PIO instruction of their own; only the side-set pin can change in a state with a test", br.loc)
    if tside != eside:
        _fail("the side-set value differs between the two branches of the test; a PIO instruction applies one value", br.loc)
    side = tside
    # wait pin: one bit of an input group (relative to the IN base)
    if cond[0] == "pinbit":
        if (tn == i) == (en == i):
            _fail("a test on one bit of an input group can only be a wait (the state stays until the bit has a level): PIO has no jump on a pin relative to the IN base", br.loc)
        stay_leaf_next, go_next = (tn, en) if tn == i else (en, tn)
        if go_next != succ:
            _fail("a state that waits for a pin must continue with the next state", br.loc)
        if treg or ereg:
            _fail("a waiting state cannot change registers", br.loc)
        info = ctx.platform.pin_info(cond[1])
        ctx.wait_pins[id(info)] = info
        level = (not cond[2]) if tn == i else cond[2]
        return Op("wait_pin", level=int(level), idx=cond[3], side=side, targets=[], foldable=True, pure_nop=False)
    # wait: stays until the test is true / false
    if cond[0] == "pin" and (tn == i) != (en == i):
        stay_leaf_next, go_next = (tn, en) if tn == i else (en, tn)
        if go_next != succ:
            _fail("a state that waits for a pin must continue with the next state", br.loc)
        if treg or ereg:
            _fail("a waiting state cannot change registers", br.loc)
        info = ctx.platform.pin_info(cond[1])
        if len(info.gpios) != 1:
            _fail("wait on a multi-bit input is not supported", br.loc)
        # cond true selects the then branch; if that branch stays, the state continues when the condition is false
        level = (not cond[2]) if tn == i else cond[2]
        return Op("wait", level=int(level), gpio=info.gpios[0], side=side, targets=[], foldable=True, pure_nop=False)
    # jump on a test
    if tn != succ and en == succ:
        jump_when, target = cond, tn
    elif en != succ and tn == succ:
        jump_when, target = _flip(cond), en
    else:
        _fail("a state with a test must continue with the next state on one branch and jump on the other (found branches to %s and %s)" % (names[tn], names[en]), br.loc)
    if treg != ereg:
        _fail("register changes differ between the two branches of the test; PIO's jmp x-- decrements on both", br.loc)
    kind = jump_when[0]
    dec = treg is not None and treg[0] == "dec"
    if kind == "osre":
        if not jump_when[2]:
            _fail("PIO can jump while the OSR is not empty (jmp !osre) but not while it is empty: write the test as `sh.osr_not_empty` selecting the jump", br.loc)
        if treg is not None:
            _fail("a jmp !osre state cannot change a register", br.loc)
        return Op("jmp", cond="!osre", target=target, side=side, targets=[target], foldable=False, pure_nop=False)
    if treg is not None and not dec:
        _fail("a state with a test can only decrement the tested register (jmp x--)", br.loc)
    if kind == "pin":
        if not jump_when[2]:
            # try the other layout is already handled by the flip above; jumping on a low pin is not available
            _fail("PIO can jump on a pin that is high but not on one that is low (jmp pin); invert the test or wait for the level", br.loc)
        info = ctx.platform.pin_info(jump_when[1])
        if len(info.gpios) != 1:
            _fail("jmp pin on a multi-bit input is not supported", br.loc)
        jmp_gpios.add(info.gpios[0])
        if dec:
            _fail("a jmp pin state cannot decrement a register", br.loc)
        return Op("jmp", cond="pin", target=target, side=side, targets=[target], foldable=False, pure_nop=False)
    _, op, a, b = jump_when
    if b == 0 and op == "==":
        if dec:
            _fail("jmp !%s does not decrement; remove the decrement" % a, br.loc)
        return Op("jmp", cond="!" + a, target=target, side=side, targets=[target], foldable=False, pure_nop=False)
    if b == 0 and op == "!=":
        if not dec or treg[1] != a:
            _fail("PIO's jump-if-nonzero is `jmp %s--`, which always decrements %s: write %s.eq(%s - 1) in the state" % (a, a, a, a), br.loc)
        return Op("jmp", cond=a + "--", target=target, side=side, targets=[target], foldable=False, pure_nop=False)
    if (a, b, op) == ("x", "y", "!="):
        if dec:
            _fail("jmp x!=y does not decrement", br.loc)
        return Op("jmp", cond="x!=y", target=target, side=side, targets=[target], foldable=False, pure_nop=False)
    _fail("this comparison has no PIO jump (X == Y can be written as X != Y with the branches swapped)", br.loc)


def _can_stall(code, sh):
    """True when the operation can stall under the configuration of the shifter (so its state must test a flag)."""
    if code in (D.PULL_BLOCK, D.PUSH_BLOCK, D.IRQ_WAIT, D.WAIT_IRQ):
        return True
    if code in D.OUT_CODES:
        return sh.autopull
    if code in D.IN_CODES:
        return sh.autopush
    return False


_OUT_DST = {D.OUT_PINS: "pins", D.OUT_X: "x", D.OUT_Y: "y", D.OUT_NULL: "null", D.OUT_ISR: "isr", D.OUT_PINDIRS: "pindirs"}
_IN_SRC = {D.IN_PINS: "pins", D.IN_X: "x", D.IN_Y: "y", D.IN_NULL: "null", D.IN_ISR: "isr", D.IN_OSR: "osr"}


def _data_instruction(data, side, ctx, loc, stalls=False):
    """The PIO instruction of a data-path operation. An operation that can stall is recognised from the flag its state tests (see _classify_flag, `stalls` True);
    in a state without that test it is a design error (a plain call of a helper that needs `with`)."""
    code = data["code"]
    if _can_stall(code, ctx.sh) and not stalls:
        hint = {D.PULL_BLOCK: "pull", D.PUSH_BLOCK: "push", D.IRQ_WAIT: "irq(m, flag, 'wait')", D.WAIT_IRQ: "wait_irq"}.get(code)
        _fail("`%s` stalls while it cannot complete: write `with sh.%s(m): m.next = ...` so the state stays until then" % (data["name"], hint or (
            "out" if code in D.OUT_CODES else "in_")), loc)
    if code in (D.PULL_NOBLOCK, D.PULL_BLOCK):
        return Op("pull", block=code == D.PULL_BLOCK, cond=data["flag"], side=side, targets=[], foldable=True, pure_nop=False)
    if code in (D.PUSH_NOBLOCK, D.PUSH_BLOCK):
        return Op("push", block=code == D.PUSH_BLOCK, cond=data["flag"], side=side, targets=[], foldable=True, pure_nop=False)
    if code in D.OUT_CODES:
        return Op("out", dst=_OUT_DST[code], n=data["n"], side=side, targets=[], foldable=True, pure_nop=False)
    if code in (D.IRQ_SET, D.IRQ_CLEAR, D.IRQ_WAIT):
        return Op("irq", mode={D.IRQ_SET: "set", D.IRQ_CLEAR: "clear", D.IRQ_WAIT: "wait"}[code], flag=data["irq"]["flag"], rel=data["irq"]["rel"], side=side, targets=[],
                  foldable=True, pure_nop=False)
    if code == D.WAIT_IRQ:
        return Op("wait_irq", pol=data["irq"]["pol"], flag=data["irq"]["flag"], rel=data["irq"]["rel"], side=side, targets=[], foldable=True, pure_nop=False)
    if code == D.MOV:
        dst, src, mop = data["mov"]
        return Op("movg", dst=dst, src=src, mop=mop, side=side, targets=[], foldable=True, pure_nop=False)
    return Op("in", src=_IN_SRC[code], n=data["n"], side=side, targets=[], foldable=True, pure_nop=False)


def _classify_flag(i, succ, br, parts, tn, en, ctx):
    """A state that stalls on a flag of the data path: a blocking pull / push (the FIFO flag), an OUT with autopull or an IN with autopush (a flag of ShiftFlags).
    The state stays while the flag is false and the operation runs in the cycle the flag is true."""
    (tl, tpins, treg, tdata), (el, epins, ereg, edata) = parts
    codes, style = ctx.flag(br.cond[1])
    if (tn == i) == (en == i):
        _fail("a state that tests a flag of the data path must stay in the state on one branch and continue on the other (use the `with sh.<operation>(m):` form of the helper)", br.loc)
    go_then = tn != i
    go_next, go_pins, go_reg, go_data = (tn, tpins, treg, tdata) if go_then else (en, epins, ereg, edata)
    stay_pins, stay_reg, stay_data = (epins, ereg, edata) if go_then else (tpins, treg, tdata)
    if br.cond[2] != go_then:
        _fail("the flag must be true on the branch that completes the instruction (the state waits while it is false)", br.loc)
    names = " / ".join(D.NAMES[c] for c in codes)
    if go_data is None or go_data["code"] not in codes:
        _fail("this flag belongs to `%s`: use `with sh.<operation>(m):`" % names, br.loc)
    if style == "body" and stay_data is not None:
        _fail("this flag belongs to `%s`: use `with sh.<operation>(m):`" % names, br.loc)
    if style == "both" and stay_data != go_data:
        _fail("the statements of `%s` must be the same whether the state waits or completes: use the helper, with nothing else in the `with` body but m.next" % go_data["name"], br.loc)
    if go_reg or stay_reg:
        _fail("a state that waits cannot change registers", br.loc)
    if go_next != succ:
        _fail("a blocking pull / push must continue with the next state (so must any instruction that can stall, such as an out with autopull; a jump needs a state of its own)", br.loc)
    for info, _ in list(go_pins.values()) + list(stay_pins.values()):
        if not _is_side(info, ctx.side_pin):
            _fail("pins that are set in a state that waits must be the side-set pin", br.loc)
    if _side_of(go_pins, ctx.side_pin, br.loc) != _side_of(stay_pins, ctx.side_pin, br.loc):
        _fail("the side-set value differs between waiting and completing; assign it before the `with` so it applies in both", br.loc)
    side = _side_of(go_pins, ctx.side_pin, br.loc)
    return _data_instruction(go_data, side, ctx, br.loc, stalls=True)


_COND = {"always": ALWAYS, "!x": NOT_X, "x--": X_DEC, "!y": NOT_Y, "y--": Y_DEC, "x!=y": X_NE_Y, "pin": PIN, "!osre": NOT_OSRE}
_REG = {"x": X, "y": Y}


def _emit(prog, op, labels, names):
    kw = {"side": op.side, "delay": op.delay}
    if op.kind == "nop":
        prog.nop(**kw)
    elif op.kind == "set_pins":
        prog.set(PINS, op.value, **kw)
    elif op.kind == "set_pindirs":
        prog.set(PINDIRS, op.value, **kw)
    elif op.kind == "set_reg":
        prog.set(_REG[op.reg], op.value, **kw)
    elif op.kind == "mov":
        prog.mov(_REG[op.dst], _REG[op.src], op=INVERT if op.inv else NONE, **kw)
    elif op.kind == "jmp":
        prog.jmp(labels[op.target], when=_COND[op.cond], **kw)
    elif op.kind == "wait":
        prog.wait_gpio(op.level, op.gpio, **kw)
    elif op.kind == "pull":
        prog.pull(block=op.block, ifempty=op.cond, **kw)
    elif op.kind == "push":
        prog.push(block=op.block, iffull=op.cond, **kw)
    elif op.kind == "out":
        prog.out({"pins": PINS, "x": X, "y": Y, "null": NULL, "isr": ISR, "pindirs": PINDIRS}[op.dst], op.n, **kw)
    elif op.kind == "in":
        prog.in_({"pins": PINS, "x": X, "y": Y, "null": NULL, "isr": ISR, "osr": OSR}[op.src], op.n, **kw)
    elif op.kind == "wait_pin":
        prog.wait_pin(op.level, op.idx, **kw)
    elif op.kind == "irq":
        prog.irq(op.flag, mode={"set": SET, "clear": CLEAR, "wait": WAIT}[op.mode], rel=op.rel, **kw)
    elif op.kind == "wait_irq":
        prog.wait_irq(op.pol, op.flag, rel=op.rel, **kw)
    elif op.kind == "movg":
        prog.mov({"pins": PINS, "x": X, "y": Y, "pindirs": PINDIRS, "isr": ISR, "osr": OSR}[op.dst], {"pins": PINS, "x": X, "y": Y, "null": NULL, "status": STATUS, "isr": ISR, "osr": OSR}[op.src],
                 op={"none": NONE, "invert": INVERT, "reverse": REVERSE}[op.mop], **kw)
    else:
        raise PioCompileError("internal: unknown operation %s" % op.kind)
