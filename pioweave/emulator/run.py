# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../../LICENSE.txt)
"""Run one state machine of a PIO block on a test case, with the conventions of bitweave's PIO simulator and of the Amaranth reference simulation in pioweave:

  - input waveforms are strings of 0 and 1, one character per cycle, the last character repeats; they drive the pins the block does not drive itself;
  - `tx` words are in the TX FIFO at cycle 0, `tx_feed` entries `(cycle, word)` are written by the host before the cycle's instruction runs;
  - the host reads the RX FIFO after every cycle (`drain=True`, the default) and logs `(cycle, word)`, or leaves the words in it;
  - `pads[t]` is what is on the pads after cycle t: the block's own output where it drives the pin, the waveform elsewhere.
"""
from ..builder.program import CHIP_VERSION
from .core import EmulationError, PioBlock


class Result:
    """What `run_case` returns."""

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
        """The pad level of `pin` as a string of 0 and 1, one character per cycle."""
        return "".join(str((p >> pin) & 1) for p in self.pads[:limit])


def _stim_at(stim, t):
    v = 0
    for pin, w in (stim or {}).items():
        if w[min(t, len(w) - 1)] == "1":
            v |= 1 << pin
    return v


def run_case(program, cycles, version=0, stim=None, tx=(), tx_feed=(), drain=True, sync_cycles=0, gpio_base=0, record=False, on_error="raise", **config):
    """Load `program` (a pioweave.builder.Program, or a word list with side=(bits, opt, pindirs) in `config`) into a fresh block, configure state machine 0 with `config`
    (`set_base`, `set_count`, `out_base`, `out_count`, `sideset_base` (alias of `side_base`), `in_base`, `jmp_pin`, `in_shift`, `out_shift`, `autopush`, `autopull`, `push_thresh`,
    `pull_thresh`, `fifo_join`, `status_sel`, `status_n`, `x`, `y`, `dirs`, `start_pc`) and run it for `cycles` cycles. A program that does something the hardware leaves
    undefined raises `EmulationError`; with `on_error="stop"` the run ends there instead and the result carries `error` and `error_cycle`."""
    side = config.pop("side", None)
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
    numbers, as the Amaranth simulation does. Returns a `Result` whose pad waveforms use the window pin numbers: `compiled.pin(gpio)` converts."""
    cfg = dict(compiled.sim_config())
    window = {compiled.pin(g): w for g, w in (stim or {}).items() if 0 <= compiled.pin(g) <= 31}
    version = CHIP_VERSION[compiled.chip]
    return run_case(compiled.program, cycles, version=version, stim=window, tx=tx, tx_feed=tx_feed, drain=drain, record=record, **cfg)


def to_vcd(trace, pins, name="pio", timescale="1 ns"):
    """A VCD of the pad levels of the pins in `pins` (window pin numbers) from a recorded trace (`run_case(..., record=True)`), one time step per cycle."""
    lines = ["$timescale %s $end" % timescale, "$scope module %s $end" % name]
    ids = {pin: chr(33 + i) for i, pin in enumerate(pins)}
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
