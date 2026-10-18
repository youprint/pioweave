# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../../LICENSE.txt)
"""Run one state machine of a PIO block on a test case, with the conventions of bitweave's PIO simulator and of the Amaranth reference simulation in pioweave:

  - input waveforms are strings of 0 and 1, one character per cycle, the last character repeats; they drive the pins the block does not drive itself;
  - `tx` words are in the TX FIFO at cycle 0, `tx_feed` entries `(cycle, word)` are written by the host before the cycle's instruction runs;
  - the host reads the RX FIFO after every cycle (`drain=True`, the default) and logs `(cycle, word)`, or leaves the words in it;
  - `pads[t]` is what is on the pads after cycle t: the block's own output where it drives the pin, the waveform elsewhere.
"""
from ..builder.program import CHIP_VERSION
from .core import EmulationError, PioBlock

__all__ = ["Result", "run_case", "run_compiled", "to_vcd"]


class Result:
    """What `run_case` returns.

    Attributes
    ----------
    pads : list of int
        The 32-bit pad value after each cycle.
    dirs : list of int
        The pin directions after each cycle.
    rx : list of tuple
        ``(cycle, word)`` read by the draining host.
    rx_left : list of int
        The words still in the RX FIFO at the end.
    tx_left : int
        The number of words still in the TX FIFO at the end.
    x, y, pc, isr, osr, irq : int
        The registers at the end (`pc` is program-relative; `irq` is the flag mask of the block).
    executed, stall_cycles, delay_cycles, rx_lost, tx_dropped : int
        The counters of the state machine (see `StateMachine`).
    trace : list of dict
        One entry per cycle when the run recorded.
    error : str or None
        The message of an `EmulationError` when run with ``on_error="stop"``.
    error_cycle : int or None
        The cycle of that error.
    """

    def __init__(self):
        self.pads = []          # one 32-bit value per cycle
        self.dirs = []
        self.rx = []            # [(cycle, word)] read by the draining host
        self.rx_left = []       # words still in the RX FIFO at the end
        self.tx_left = 0
        self.x = self.y = self.pc = self.isr = self.osr = self.irq = 0
        self.executed = self.stall_cycles = self.delay_cycles = self.rx_lost = self.tx_dropped = 0
        self.trace = []
        self.error = None       # the message of an EmulationError when run with on_error="stop"
        self.error_cycle = None

    def wave(self, pin, limit=None):
        """The pad level of `pin` as a string of 0 and 1, one character per cycle.

        Parameters
        ----------
        pin : int
            The pin number in the window.
        limit : int or None
            Stop after this many cycles.

        Returns
        -------
        str
            The waveform.
        """
        return "".join(str((p >> pin) & 1) for p in self.pads[:limit])


def _stim_at(stim, t):
    v = 0
    for pin, w in (stim or {}).items():
        if w[min(t, len(w) - 1)] == "1":
            v |= 1 << pin
    return v


def run_case(program, cycles, version=None, stim=None, tx=(), tx_feed=(), drain=True, sync_cycles=0, gpio_base=0, record=False, on_error="raise", **config):
    """Load `program` (a pioweave.builder.Program, or a word list with side=(bits, opt, pindirs) in `config`) into a fresh block, configure state machine 0 with `config`
    (`set_base`, `set_count`, `out_base`, `out_count`, `sideset_base` (alias of `side_base`), `in_base`, `jmp_pin`, `in_shift`, `out_shift`, `autopush`, `autopull`, `push_thresh`,
    `pull_thresh`, `fifo_join`, `status_sel`, `status_n`, `x`, `y`, `dirs`, `start_pc`) and run it for `cycles` cycles. A program that does something the hardware leaves
    undefined raises `EmulationError`; with `on_error="stop"` the run ends there instead and the result carries `error` and `error_cycle`.

    Parameters
    ----------
    program : pioweave.builder.Program or list of int
        The program, or its 16-bit words (then pass ``side=(bits, optional, pindirs)`` if it uses side-set).
    cycles : int
        The number of cycles to run.
    version : int
        The PIO version: 0 or 1. None (the default) takes it from a `Program` (its chip) and is 0 for a list of words.
    stim : dict or None
        Input waveforms ``{pin: "0101..."}`` in window pin numbers, one character per cycle; the last character repeats.
    tx : iterable of int
        Words in the TX FIFO at cycle 0.
    tx_feed : iterable of tuple
        ``(cycle, word)`` pairs the host writes before that cycle's instruction runs.
    drain : bool
        The host reads the RX FIFO after every cycle.
    sync_cycles : int
        Cycles of the input synchroniser (see `PioBlock`).
    gpio_base : int
        0, or 16 on PIO version 1.
    record : bool
        Record a trace (see `to_vcd`).
    on_error : str
        ``"raise"`` or ``"stop"``.
    **config :
        The state machine configuration: ``x``, ``y``, ``dirs`` and ``start_pc`` (program-relative) set the initial state; the other keys are those of
        `StateMachine.configure`, plus the alias ``sideset_base`` for ``side_base``.

    Returns
    -------
    Result
        The waveforms, the words read and the final registers.

    Raises
    ------
    EmulationError
        If the program does something the hardware leaves undefined and `on_error` is ``"raise"``.
    ValueError
        If `cycles` is negative, or a waveform of `stim` is empty or has other characters than 0 and 1, or a configuration value is out of range.

    Examples
    --------
    >>> from pioweave.builder import Program, PINS
    >>> from pioweave.emulator import run_case
    >>> p = Program("square")
    >>> with p.wrapped():
    ...     p.set(PINS, 1, delay=1)
    ...     p.set(PINS, 0, delay=1)
    >>> result = run_case(p, 8, set_base=0, set_count=1, dirs=1)
    >>> result.wave(0)
    '11001100'
    >>> result.executed
    4
    """
    if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles < 0:
        raise ValueError("cycles is a whole number of at least 0, not %r" % (cycles,))
    for pin, w in (stim or {}).items():
        if not isinstance(pin, int) or not 0 <= pin <= 31 or not isinstance(w, str) or not w or w.strip("01"):
            raise ValueError("stim is {pin: waveform} with a pin from 0 to 31 and a non-empty string of 0 and 1, not %r: %r" % (pin, w))
    side = config.pop("side", None)
    if version is None:
        version = getattr(program, "version", 0)
    block = PioBlock(version=version, gpio_base=gpio_base, sync_cycles=sync_cycles)
    loaded = block.load(program, side=side)
    sm = block.sm[0]
    if "sideset_base" in config:
        config["side_base"] = config.pop("sideset_base")
    x, y, dirs, start = config.pop("x", 0), config.pop("y", 0), config.pop("dirs", 0), config.pop("start_pc", 0)
    sm.configure(program=loaded, **config)
    sm.enable()
    sm.set_pc(loaded.offset + start)
    sm.x, sm.y = x, y
    block.dirs = dirs & 0xFFFFFFFF
    for w in tx:
        sm.put(w)
    feeds = sorted(tx_feed)
    block.recording = record
    res = Result()
    for t in range(cycles):
        for c, w in feeds:
            if c == t:
                sm.put(w)
        block.ext = _stim_at(stim, t)
        try:
            block.step()
        except EmulationError as e:
            if on_error != "stop":
                raise
            res.error, res.error_cycle = str(e), t
            break
        if drain:
            while len(sm.rx):
                res.rx.append((t, sm.get()))
        res.pads.append(block.pads())
        res.dirs.append(block.dirs)
    res.rx_left = list(sm.rx.q)
    res.tx_left = len(sm.tx)
    res.x, res.y, res.pc, res.isr, res.osr, res.irq = sm.x, sm.y, sm.pc - loaded.offset, sm.isr, sm.osr, block.irq
    res.executed, res.stall_cycles, res.delay_cycles, res.rx_lost, res.tx_dropped = sm.executed, sm.stall_cycles, sm.delay_cycles, sm.rx_lost, sm.tx_dropped
    res.trace = block.trace
    return res


def run_compiled(compiled, cycles, stim=None, tx=(), tx_feed=(), drain=True, record=False):
    """Run a program compiled from an Amaranth design (`pioweave.compile_design`) with the configuration the compiler derived (`compiled.sim_config()`). `stim` uses real GPIO
    numbers, as the Amaranth simulation does. Returns a `Result` whose pad waveforms use the window pin numbers: `compiled.pin(gpio)` converts.

    Parameters
    ----------
    compiled : pioweave.compile.Compiled
        What `pioweave.compile.compile_design` returned.
    cycles : int
        The number of cycles to run.
    stim : dict or None
        Input waveforms ``{gpio: "0101..."}`` in real GPIO numbers.
    tx, tx_feed, drain, record :
        As for `run_case`.

    Returns
    -------
    Result
        The result of the run.

    Examples
    --------
    >>> from pioweave.compile import compile_design
    >>> from pioweave.designs import SquareWave
    >>> from pioweave.emulator import run_compiled
    >>> from pioweave.platform import RP2040PioPlatform
    >>> compiled = compile_design(SquareWave(), RP2040PioPlatform(), "square")
    >>> run_compiled(compiled, 8).wave(0)
    '11001100'
    """
    cfg = dict(compiled.sim_config())
    window = {compiled.pin(g): w for g, w in (stim or {}).items() if 0 <= compiled.pin(g) <= 31}
    version = CHIP_VERSION[compiled.chip]
    return run_case(compiled.program, cycles, version=version, stim=window, tx=tx, tx_feed=tx_feed, drain=drain, record=record, **cfg)


def to_vcd(trace, pins, name="pio", timescale="1 ns"):
    """A VCD of the pad levels of the pins in `pins` (window pin numbers) from a recorded trace (`run_case(..., record=True)`), one time step per cycle.

    Parameters
    ----------
    trace : list of dict
        ``Result.trace`` of a run with ``record=True``.
    pins : iterable of int
        The window pins to dump.
    name : str
        The module name in the file.
    timescale : str
        The VCD timescale.

    Returns
    -------
    str
        The VCD text.

    Examples
    --------
    >>> from pioweave.builder import Program, PINS
    >>> from pioweave.emulator import run_case, to_vcd
    >>> p = Program("square")
    >>> with p.wrapped():
    ...     p.set(PINS, 1, delay=1)
    ...     p.set(PINS, 0, delay=1)
    >>> result = run_case(p, 4, set_base=0, set_count=1, dirs=1, record=True)
    >>> print(to_vcd(result.trace, [0]), end="")
    $timescale 1 ns $end
    $scope module pio $end
    $var wire 1 ! pin0 $end
    $upscope $end
    $enddefinitions $end
    #0
    1!
    #2
    0!
    #4
    """
    lines = ["$timescale %s $end" % timescale, "$scope module %s $end" % name]
    ids = {pin: chr(33 + i + (i >= 3)) for i, pin in enumerate(pins)}          # printable ASCII from "!", skipping "$" (it starts the keywords)
    for pin, ident in ids.items():
        lines.append("$var wire 1 %s pin%d $end" % (ident, pin))
    lines += ["$upscope $end", "$enddefinitions $end"]
    last = {}
    for row in trace:
        changes = []
        for pin, ident in ids.items():
            v = (row["pads"] >> pin) & 1
            if last.get(pin) != v:
                changes.append("%d%s" % (v, ident))
                last[pin] = v
        if changes or row["cycle"] == 0:
            lines.append("#%d" % row["cycle"])
            lines += changes
    lines.append("#%d" % (len(trace)))
    return "\n".join(lines) + "\n"
