# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../../LICENSE.txt)
"""A generator-based API for unit tests and for looking at a program run: `emulate` yields a ``(before, after)`` pair of frozen `State` snapshots for every system clock cycle
or every executed instruction of one state machine, and a few conveniences are built on it: `count_cycles`, `pin_history`, `waveform_text` and `plot_waveform`.

    from pioweave.emulator import emulate

    for before, after in emulate(program, stop_when=lambda s: s.x == 0xFFFFFFFF):
        print(before.x, "->", after.x)

The state machine is run by the same `PioBlock` as everything else in `pioweave.emulator`; the configuration keywords are those of `run_case`.
"""
from dataclasses import dataclass, field, replace

from ..builder.program import CHIP_VERSION
from .core import EmulationError, PioBlock
from .run import _stim_at

__all__ = ["State", "emulate", "count_cycles", "pin_history", "waveform_text", "plot_waveform"]

_KINDS = {"-": "none", "e": "executed", "s": "stalled", "f": "forced", "d": "delay"}


@dataclass(frozen=True)
class State:
    """An immutable snapshot of one state machine and of the pins of its block.

    Parameters
    ----------
    clock : int
        System clock cycles run so far.
    pc : int
        The program counter, relative to the start of the program.
    x, y : int
        The scratch registers.
    isr, osr : int
        The shift registers.
    isr_count, osr_count : int
        The shift counters: bits shifted into the ISR and out of the OSR. The OSR counter is 32 (empty) at the start.
    tx, rx : tuple of int
        The words in the TX and RX FIFOs, oldest first.
    pins : int
        What is on the pads of the block's 32-pin window, one bit per pin.
    dirs : int
        The pin directions, one bit per pin (1 is an output).
    irq : int
        The IRQ flags of the block, bit n is flag n.
    opcode : int or None
        The instruction word the state machine handled in the cycle that led to this state; None in the initial state.
    kind : str
        What that cycle was: ``"none"`` (the initial state), ``"executed"``, ``"stalled"``, ``"forced"`` (an instruction from OUT EXEC, MOV EXEC or the host), ``"delay"``
        (a delay cycle) or ``"idle"`` (the state machine did not run: the clock divider or a disabled state machine).
    delay_left : int
        Delay cycles still to run before the next instruction.

    Examples
    --------
    >>> State().osr_count
    32
    >>> State(x=3).replace(x=4).x
    4
    """
    clock: int = 0
    pc: int = 0
    x: int = 0
    y: int = 0
    isr: int = 0
    osr: int = 0
    isr_count: int = 0
    osr_count: int = 32
    tx: tuple = field(default_factory=tuple)
    rx: tuple = field(default_factory=tuple)
    pins: int = 0
    dirs: int = 0
    irq: int = 0
    opcode: object = None
    kind: str = "none"
    delay_left: int = 0

    def replace(self, **changes):
        """A copy with some fields changed.

        Parameters
        ----------
        **changes :
            The fields to change.

        Returns
        -------
        State
            The new snapshot.
        """
        return replace(self, **changes)

    def pin(self, n):
        """The level of pin `n`.

        Parameters
        ----------
        n : int
            A pin of the window.

        Returns
        -------
        int
            0 or 1.
        """
        return (self.pins >> n) & 1


def _snapshot(block, sm, loaded, idle=False):
    last_kind, last_word, _ = sm.last
    kind = "idle" if idle else _KINDS[last_kind]
    return State(clock=block.cycle, pc=sm.pc - loaded.offset, x=sm.x, y=sm.y, isr=sm.isr, osr=sm.osr, isr_count=sm.isr_count, osr_count=sm.osr_count, tx=tuple(sm.tx.q),
                 rx=tuple(sm.rx.q), pins=block.pads(), dirs=block.dirs, irq=block.irq, opcode=None if idle or last_kind == "-" else last_word, kind=kind,
                 delay_left=sm.delay_left)


def emulate(program, *, stop_when=None, cycles=None, per="instruction", initial_state=None, input_source=None, stim=None, tx=(), tx_feed=(), version=None, gpio_base=0,
            sync_cycles=0, max_cycles=1_000_000, **config):
    """Run one state machine and yield ``(before, after)`` snapshots, to write unit tests, count cycles or look at the pins.

    Nothing runs until the generator is iterated. Before every step `stop_when` is called with the `State` the step would start from; the generator ends when it returns true, when
    `cycles` system clock cycles have run, or when `max_cycles` is reached (so that a program stuck on a WAIT cannot hang a test).

    Parameters
    ----------
    program : pioweave.builder.Program or list of int
        The program, or its 16-bit words (then pass ``side=(bits, optional, pindirs)`` if it uses side-set, as for `run_case`).
    stop_when : callable or None
        ``stop_when(state)`` returning true to stop. At least one of `stop_when` and `cycles` is required.
    cycles : int or None
        Stop after this many system clock cycles (checked after each step).
    per : str
        ``"instruction"`` (the default): one pair per executed instruction; the step starts when the previous one completed and ends when this one has completed and its delay
        cycles have run, so stall and delay cycles are inside the step and ``after.clock - before.clock`` is the cost of the instruction in system clock cycles. ``"cycle"``: one pair
        per system clock cycle, whatever the state machine did (executed, stalled, delay, idle).
    initial_state : State or None
        The registers, FIFOs, program counter, shift counters, pin directions, pin levels and IRQ flags to start from, and the clock count.
    input_source : callable or None
        ``input_source(state)`` called before every system clock cycle with the current `State`, returning the levels the outside world drives, one bit per pin (pins the
        block drives itself as outputs are not affected).
    stim : dict or None
        Input waveforms ``{pin: "0101..."}`` as for `run_case` (one character per cycle, the last repeats); added to `input_source`.
    tx : iterable of int
        Words in the TX FIFO at the start.
    tx_feed : iterable of tuple
        ``(cycle, word)`` pairs the host writes before that cycle's instruction runs.
    version : int or None
        The PIO version: 0 or 1; None takes it from the chip of a `Program`, 0 for a word list.
    gpio_base : int
        0, or 16 on PIO version 1.
    sync_cycles : int
        Cycles of the input synchroniser (0 bypasses it, as in `run_case`).
    max_cycles : int
        Safety limit on the system clock cycles.
    **config :
        The state machine configuration of `run_case`: ``set_base``, ``set_count``, ``out_base``, ``out_count``, ``sideset_base``, ``in_base``, ``jmp_pin``, ``in_shift``,
        ``out_shift``, ``autopush``, ``autopull``, ``push_thresh``, ``pull_thresh``, ``fifo_join``, ``status_sel``, ``status_n``, ``clkdiv``, ``x``, ``y``, ``dirs``, ``start_pc``,
        ``side`` and so on.

    Yields
    ------
    tuple of State
        ``(before, after)``.

    Raises
    ------
    ValueError
        If neither `stop_when` nor `cycles` is given, or `per` is not valid.
    EmulationError
        If the program does something the hardware leaves undefined.

    Examples
    --------
    Count X down from 3 and watch the register change; ``jmp x--`` decrements even when it does not jump:

    >>> from pioweave.builder import Program, X, X_DEC
    >>> from pioweave.emulator import emulate
    >>> p = Program("count")
    >>> p.set(X, 3)
    >>> _ = p.label("loop")
    >>> p.jmp("loop", when=X_DEC)
    >>> for before, after in emulate(p, stop_when=lambda s: s.x == 0xFFFFFFFF):
    ...     print(after.clock, before.x, "->", after.x)
    1 0 -> 3
    2 3 -> 2
    3 2 -> 1
    4 1 -> 0
    5 0 -> 4294967295

    One pair per system clock cycle shows the delay cycles too:

    >>> from pioweave.builder import PINS
    >>> p = Program("blink")
    >>> with p.wrapped():
    ...     p.set(PINS, 1, delay=1)
    ...     p.set(PINS, 0, delay=1)
    >>> [(a.clock, a.kind, a.pin(0)) for _, a in emulate(p, cycles=4, per="cycle", set_base=0, set_count=1, dirs=1)]
    [(1, 'executed', 1), (2, 'delay', 1), (3, 'executed', 0), (4, 'delay', 0)]
    """
    if per not in ("instruction", "cycle"):
        raise ValueError("per is 'instruction' or 'cycle'")
    if stop_when is None and cycles is None:
        raise ValueError("give stop_when or cycles, or the run never ends")
    if version is None:
        version = CHIP_VERSION[program.chip] if hasattr(program, "chip") else 0
    side = config.pop("side", None)
    if "sideset_base" in config:
        config["side_base"] = config.pop("sideset_base")
    x, y, dirs, start = config.pop("x", 0), config.pop("y", 0), config.pop("dirs", 0), config.pop("start_pc", 0)
    block = PioBlock(version=version, gpio_base=gpio_base, sync_cycles=sync_cycles)
    loaded = block.load(program, side=side)
    sm = block.sm[0]
    sm.configure(program=loaded, **config)
    sm.enable()
    sm.set_pc(loaded.offset + start)
    sm.x, sm.y = x, y
    block.dirs = dirs & 0xFFFFFFFF
    for w in tx:
        sm.put(w)
    if initial_state is not None:
        s = initial_state
        sm.set_pc(loaded.offset + s.pc)
        sm.x, sm.y, sm.isr, sm.osr, sm.isr_count, sm.osr_count = s.x, s.y, s.isr, s.osr, s.isr_count, s.osr_count
        sm.tx.q.extend(w for w in s.tx)
        sm.rx.q.extend(w for w in s.rx)
        block.dirs, block.irq, block.cycle = s.dirs, s.irq, s.clock
        block.out_level = s.pins & s.dirs
        block.ext = s.pins & ~s.dirs & 0xFFFFFFFF
        sm.delay_left = s.delay_left
    feeds = sorted(tx_feed)
    limit = block.cycle + max_cycles
    stop_at = None if cycles is None else block.cycle + cycles

    def one_cycle(state):
        """Run one system clock cycle; returns (the instruction completed, the state machine did anything)."""
        t = block.cycle
        for c, w in feeds:
            if c == t:
                sm.put(w)
        if stim or input_source is not None:
            ext = _stim_at(stim, t)
            if input_source is not None:
                ext |= input_source(state) & 0xFFFFFFFF
            block.ext = ext
        done_before, work_before = sm.executed, sm.executed + sm.stall_cycles + sm.delay_cycles
        block.step()
        return sm.executed != done_before, sm.executed + sm.stall_cycles + sm.delay_cycles != work_before

    current = _snapshot(block, sm, loaded, idle=True)
    current = current.replace(kind="none", opcode=None)
    while block.cycle < limit:
        if (stop_when is not None and stop_when(current)) or (stop_at is not None and block.cycle >= stop_at):
            return
        before = current
        if per == "cycle":
            _, worked = one_cycle(current)
            current = _snapshot(block, sm, loaded, idle=not worked)
        else:
            done = False
            while block.cycle < limit and not (stop_at is not None and block.cycle >= stop_at):
                completed, worked = one_cycle(current)
                current = _snapshot(block, sm, loaded, idle=not worked)
                done = done or completed
                if done and sm.delay_left == 0:
                    break
        yield before, current


def count_cycles(program, stop_when, *, max_cycles=1_000_000, **config):
    """The number of system clock cycles the state machine takes from the start until `stop_when` is true.

    Parameters
    ----------
    program : pioweave.builder.Program or list of int
        The program.
    stop_when : callable
        ``stop_when(state)`` returning true; called before every instruction with the `State` the instruction starts from.
    max_cycles : int
        Give up after this many cycles.
    **config :
        As for `emulate`.

    Returns
    -------
    int
        The system clock cycles run when `stop_when` first held (a state machine that runs at a clock divider of n takes n system clock cycles per cycle).

    Raises
    ------
    EmulationError
        If `stop_when` did not hold within `max_cycles`.

    Examples
    --------
    One ``set`` with 3 delay cycles takes 4 cycles; the loop below counts the cycles of the first 3 passes:

    >>> from pioweave.builder import Program, X, X_DEC
    >>> from pioweave.emulator import count_cycles
    >>> p = Program("delays")
    >>> p.set(X, 2, delay=3)
    >>> _ = p.label("loop")
    >>> p.jmp("loop", when=X_DEC, delay=1)
    >>> count_cycles(p, lambda s: s.x == 0xFFFFFFFF)
    10
    """
    state = None
    for _, state in emulate(program, stop_when=stop_when, max_cycles=max_cycles, **config):
        pass
    if state is None:
        return 0
    if not stop_when(state):
        raise EmulationError("the stop condition did not hold within %d cycles" % max_cycles)
    return state.clock


def pin_history(program, cycles, pins=(0,), **config):
    """The level of some pins after every system clock cycle.

    Parameters
    ----------
    program : pioweave.builder.Program or list of int
        The program.
    cycles : int
        The number of system clock cycles to run.
    pins : iterable of int
        The pins of the window to record.
    **config :
        As for `emulate` (the configuration, ``stim`` or ``input_source`` for inputs, ``dirs`` for the initial pin directions).

    Returns
    -------
    dict
        ``{pin: [level after cycle 0, level after cycle 1, ...]}`` with levels 0 and 1.

    Examples
    --------
    >>> from pioweave.builder import PINS, Program
    >>> from pioweave.emulator import pin_history
    >>> p = Program("blink")
    >>> with p.wrapped():
    ...     p.set(PINS, 1, delay=1)
    ...     p.set(PINS, 0, delay=1)
    >>> pin_history(p, 8, pins=[0], set_base=0, set_count=1, dirs=1)
    {0: [1, 1, 0, 0, 1, 1, 0, 0]}
    """
    pins = list(pins)
    hist = {p: [] for p in pins}
    for _, after in emulate(program, cycles=cycles, per="cycle", **config):
        for p in pins:
            hist[p].append((after.pins >> p) & 1)
    return hist


def waveform_text(history, names=None, scale=1, high="#", low="_"):
    """A plain-text waveform of pin histories, one line per pin.

    Parameters
    ----------
    history : dict or Result
        What `pin_history` returned, or a `Result` of `run_case` (then give the pins through `names` keys, or all pins that ever change are shown).
    names : dict or None
        ``{pin: "label"}``; the pins to show, in this order. None shows all pins of a dict, and the pins that change in a `Result`.
    scale : int
        Characters per cycle.
    high, low : str
        The characters for level 1 and level 0.

    Returns
    -------
    str
        The text, without a trailing newline.

    Examples
    --------
    >>> from pioweave.emulator import waveform_text
    >>> print(waveform_text({0: [1, 1, 0, 0, 1, 1, 0, 0], 3: [0] * 8}, names={0: "clk", 3: "cs"}))
    clk |##__##__|
    cs  |________|
    """
    if hasattr(history, "pads"):
        pads = history.pads
        shown = list(names) if names else [p for p in range(32) if len({(v >> p) & 1 for v in pads}) > 1]
        history = {p: [(v >> p) & 1 for v in pads] for p in shown}
    if names is None:
        names = {p: "pin%d" % p for p in history}
    width = max((len(str(n)) for n in names.values()), default=0)
    lines = []
    for p, label in names.items():
        levels = history[p]
        lines.append("%-*s |%s|" % (width, label, "".join((high if v else low) * scale for v in levels)))
    return "\n".join(lines)


def plot_waveform(history, names=None, ax=None):
    """Plot pin histories as step waveforms with matplotlib, which is imported here, not at import time.

    Parameters
    ----------
    history : dict
        What `pin_history` returned: ``{pin: [levels]}``.
    names : dict or None
        ``{pin: "label"}``.
    ax : matplotlib.axes.Axes or None
        Where to draw; a new figure when None.

    Returns
    -------
    matplotlib.axes.Axes
        The axes drawn on.

    Raises
    ------
    ImportError
        If matplotlib is not installed.
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError as e:
        raise ImportError("plot_waveform needs matplotlib (pip install matplotlib)") from e
    if ax is None:
        _, ax = plt.subplots()
    names = names or {p: "pin%d" % p for p in history}
    ticks = []
    for row, (p, label) in enumerate(names.items()):
        base = 1.5 * (len(names) - 1 - row)
        levels = list(history[p])
        ax.step(range(len(levels) + 1), [base + v for v in levels] + [base + levels[-1]] if levels else [], where="post")
        ticks.append((base + .5, label))
    ax.set_yticks([t for t, _ in ticks])
    ax.set_yticklabels([n for _, n in ticks])
    ax.set_xlabel("system clock cycle")
    return ax
