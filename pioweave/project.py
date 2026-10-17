# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Project writers for the PIO platform: `platform.build(design, name, build_dir, project=KIND, factory=..., ...)`.

KIND (the user picks what kind of project is needed):
  pio           <name>.pio, bitweave.toml with a simulation case, README.md
  pico-c        pio + a Pico SDK C project: CMakeLists.txt, main.c (loads the program, configures the state machine, starts it)
  micropython   pio + <name>_pio.py (written by pioasm -o python) and main.py (rp2.StateMachine glue)

The simulation case in bitweave.toml carries the expected pin waveforms taken from the AMARANTH simulation of the same design, so `bitweave pio
<name>` checks the compiled PIO program against the design it came from. That needs `factory`, a callable returning a fresh (design, platform) pair
(a design requests its pins from the platform when it is elaborated, so the compile and the simulation each need their own pair); without it the
case is written without expectations. `cycles` is the simulation length, `stim` the input waveforms {gpio: "0101..."}.

Nothing generated here has run on a chip. The C project was built with the Pico SDK through `bitweave firmware` in the tests; the MicroPython files
use the rp2 module as documented and were not run on a device [verify].
"""
import dataclasses
import os
import re
import shutil
import subprocess
import sys
import tempfile

from amaranth.hdl import ClockDomain, Module
from amaranth.sim import Simulator

from ._compat import Fragment, clock_period
from ._validate import DEFAULT_MAX_CYCLES, check_cycles, check_name, check_stim, check_tx_feed, check_words

__all__ = ["SimOptions", "PioProjectError", "check_targets", "find_pioasm", "amaranth_run", "amaranth_waves", "write_project"]


def check_targets(plan, build_dir):
    """Refuse to write a project through a symbolic link: Amaranth's ``plan.execute_local`` opens each file for writing, which replaces an existing file and follows a link to
    wherever it points (audit S-05). Existing regular files are overwritten, as before (a rebuild into the same directory is what the bitweave generator does).

    Parameters
    ----------
    plan : amaranth.build.run.BuildPlan
        The plan whose files are about to be written.
    build_dir : str
        The directory they are written to.

    Raises
    ------
    PioProjectError
        If a file of the plan, or the directory, is a symbolic link.
    """
    for target in [build_dir] + [os.path.join(build_dir, name) for name in plan.files]:
        if os.path.islink(target):
            raise PioProjectError("%s is a symbolic link: the project is not written through links\n  hint: remove the link, or build into another directory" % target)


PIOASM_TIMEOUT = 60
"""Seconds pioasm may take (it assembles a program of at most 32 instructions)."""


@dataclasses.dataclass
class SimOptions:
    """What the reference simulation of a design needs, passed to `platform.build(..., sim=SimOptions(...))`.

    `factory`: a callable returning a fresh `(design, platform)` pair (a design requests its pins from the platform when it is elaborated, so every run needs new
    ones); without it the project has no expectations. `cycles`: length of the simulation. `stim`: input waveforms {gpio: "0101..."}. `tx`: words in the TX FIFO at
    cycle 0. `tx_feed`: [(cycle, word)] written by the host so that the instruction of that cycle sees them. `drain`: the host empties the RX FIFO every cycle.
    `irq_set` / `irq_clear`: [(cycle, flag)] IRQ flags the host raises or lowers at the start of that cycle (before the instruction; clear first, then set).
    The values are checked when the object is made (`PioProjectError`): `cycles` from 1 to `max_cycles`, waveforms of 0 and 1 for GPIO 0 to 47, 32-bit words, flags 0 to 7.

    Parameters
    ----------
    factory : callable or None
        Returns a fresh ``(design, platform)`` pair.
    cycles : int
        Length of the simulation.
    stim : dict
        Input waveforms ``{gpio: "0101..."}``, one character per cycle; the last character repeats.
    tx : tuple of int
        Words in the TX FIFO at cycle 0.
    tx_feed : tuple of tuple
        ``(cycle, word)`` pairs written by the host.
    drain : bool
        The host empties the RX FIFO every cycle.
    irq_set, irq_clear : tuple of tuple
        ``(cycle, flag)`` pairs: the host raises or lowers an IRQ flag at the start of that cycle.
    max_cycles : int
        The ceiling of `cycles` (default 1,000,000): a run takes time and memory in proportion to the number of cycles. Raise it only on purpose.

    Raises
    ------
    PioProjectError
        If a value is out of range or has the wrong type.
    """
    factory: object = None
    cycles: int = 64
    stim: dict = dataclasses.field(default_factory=dict)
    tx: tuple = ()
    tx_feed: tuple = ()
    drain: bool = True
    irq_set: tuple = ()
    irq_clear: tuple = ()
    max_cycles: int = DEFAULT_MAX_CYCLES

    def __post_init__(self):
        # The values are written into bitweave.toml and main.c: a newline in a waveform would add a table to the TOML file (audit S-03), a word over 32 bits is not valid C (S-08).
        # `cycles` has a ceiling (`max_cycles`, 1,000,000 by default; --max-cycles on the command line) because the runs are linear in it (audit S-10).
        try:
            check_cycles(self.cycles, PioProjectError, self.max_cycles)
            check_stim(self.stim, PioProjectError)
            check_words(self.tx, PioProjectError, "tx")
            check_tx_feed(self.tx_feed, PioProjectError)
            for what in ("irq_set", "irq_clear"):
                for item in getattr(self, what):
                    if len(item) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in item) or item[0] < 0 or not 0 <= item[1] <= 7:
                        raise PioProjectError("%s entries are (cycle, flag) with a cycle of at least 0 and a flag from 0 to 7, not %r" % (what, item))
        except TypeError as e:
            raise PioProjectError("invalid simulation option: %s" % e) from None


class PioProjectError(Exception):
    """The project cannot be written (an unknown project kind, an invalid name or simulation option, a pioasm that cannot run)."""


def find_pioasm():
    """Find pioasm: the ``PIOASM`` environment variable, the ``PATH``, or next to the running Python (bitweave puts it in the bin directory of its tools environment).

    Returns
    -------
    str or None
        The absolute path of an executable file, or None when pioasm was not found.
    """
    for c in (os.environ.get("PIOASM"), shutil.which("pioasm"), os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "pioasm")):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):         # a directory or a file that cannot run is not pioasm (audit S-17)
            return os.path.abspath(c)
    return None


# ----------------------------------------------------------------------------- reference simulation (Amaranth)
def amaranth_run(factory, cycles, stim=None, tx=(), tx_feed=(), drain=True, irq_set=(), irq_clear=()):
    """Simulate the design built by `factory` with the Amaranth simulator.

    Alignment with the PIO model: the FSM state in cycle t does what the instruction executed in cycle t does; outputs are registers, so the value
    sampled after the t-th clock edge is the pad level of cycle t. Inputs are set before the edge of cycle t and are what the instruction of
    cycle t reads.

    Parameters
    ----------
    factory : callable
        Returns a fresh ``(design, platform)`` pair.
    cycles : int
        The number of clock cycles.
    stim : dict or None
        Input waveforms ``{gpio: "0101..."}``, one character per cycle; the last character repeats.
    tx : iterable of int
        Words in the TX FIFO at cycle 0.
    tx_feed : iterable of tuple
        ``(cycle, word)`` pairs written by the host so that the instruction of that cycle sees them.
    drain : bool
        The host empties the RX FIFO every cycle (the default, as bitweave's simulator does).
    irq_set, irq_clear : iterable of tuple
        ``(cycle, flag)`` pairs: the host raises or lowers an IRQ flag at the start of that cycle (the instruction of that cycle sees it; clear first, then set).

    Returns
    -------
    dict
        ``{"waves": {gpio: waveform}`` for every output GPIO, ``"rx": [(cycle, word)]`` pushed to the RX FIFO, ``"rx_left"``: words still in it, ``"tx_left"``:
        words still in the TX FIFO, ``"irq"``: the eight IRQ flags as a mask after each cycle (empty when the design uses none), ``"dirs"``: ``{gpio: waveform}`` of the pin
        directions (``1`` = output) of the pins of groups with a direction register (``platform.directions``; empty when the design has none)``}``. For those pins the
        waveform in ``"waves"`` is the pad: the design's output where the pin is an output, the stimulus where it is an input.

    Examples
    --------
    >>> from pioweave.designs import SquareWave
    >>> from pioweave.platform import RP2040PioPlatform
    >>> from pioweave.project import amaranth_run
    >>> amaranth_run(lambda: (SquareWave(), RP2040PioPlatform()), 8)
    {'waves': {0: '11001100'}, 'rx': [], 'rx_left': 0, 'tx_left': 0, 'irq': [], 'dirs': {}}
    """
    design, platform = factory()
    platform.simulation = True
    feeds = sorted((c, w) for c, w in tx_feed if c > 0)
    platform.tx_init = list(tx) + [w for c, w in tx_feed if c <= 0]
    irq_set, irq_clear = list(irq_set), list(irq_clear)
    platform.irq_init = sum({1 << f for c, f in irq_set if c <= 0})         # a flag the host raises before cycle 0 (lowering a flag that is not set does nothing)
    frag = Fragment.get(design, platform)
    platform.bind_buffers(frag)
    top = Module()
    top.domains.sync = ClockDomain()
    top.submodules.d = frag
    stim = stim or {}
    ins, outs = {}, {}
    for p in platform._pio_pins:
        if p.i is not None:
            for k, g in enumerate(p.gpios):
                ins[g] = (p.port.i, k)        # an input is driven on the pad side of the simulation port
        if p.o is not None:
            for k, g in enumerate(p.gpios):
                outs[g] = (p.o, k)
    waves = {g: [] for g in outs}
    dyn = [p for p in platform._pio_pins if p.dirs is not None and p.o is not None]       # groups with a direction register: the pad follows the direction
    dyn_gpios = {g for p in dyn for g in p.gpios}
    dir_waves = {g: [] for g in dyn_gpios}

    def stim_bit(g, t):
        w = stim.get(g, "0")
        return int(w[min(t, len(w) - 1)])

    def pad_of(ctx, p, t):
        """The pad of the group `p` in cycle t, as seen with the registers as they are now: the design's output where the pin is an output, the stimulus elsewhere."""
        o, d = ctx.get(p.o), ctx.get(p.dirs)
        return [((o >> k) & 1) if (d >> k) & 1 else stim_bit(g, t) for k, g in enumerate(p.gpios)]

    fifo = platform._shifter.fifo if platform._shifter is not None else None
    rx, state = [], {}
    irqm = platform._shifter.irq_model if platform._shifter is not None else None
    irq_trace = []

    async def tb(ctx):
        for t in range(cycles):
            levels = {}
            for g, (sig, k) in ins.items():      # a multi-bit input port takes all its bits at once
                w = stim.get(g, "0")
                levels[id(sig)] = (sig, levels.get(id(sig), (None, 0))[1] | (int(w[min(t, len(w) - 1)]) << k))
            for sig, value in levels.values():
                ctx.set(sig, value)
            for p in dyn:             # the instruction of this cycle reads the pad: own output where the pin is an output
                if p.i is not None:
                    ctx.set(p.port.i, sum(b << k for k, b in enumerate(pad_of(ctx, p, t))))
            if fifo is not None:
                # a word the host writes in cycle t-1 is applied at the edge that ends it, so the instruction of cycle t sees it
                due = [w for c, w in feeds if c == t + 1]
                ctx.set(fifo.host_tx_en, 1 if due else 0)
                ctx.set(fifo.host_tx_data, due[0] if due else 0)
                if irqm is not None:           # events of cycle t + 1 are signalled now and sampled at the edge: the instruction of that cycle sees them
                    ctx.set(irqm.host_set, sum({1 << f for c, f in irq_set if c == t + 1}))
                    ctx.set(irqm.host_clr, sum({1 << f for c, f in irq_clear if c == t + 1}))
                ctx.set(fifo.host_rx_pop, 0)
                ctx.set(fifo.host_rx_drain, 1 if drain else 0)
            await ctx.tick()
            for g, (sig, k) in outs.items():
                if g not in dyn_gpios:
                    waves[g].append(str((ctx.get(sig) >> k) & 1))
            for p in dyn:
                for k, (g, b) in enumerate(zip(p.gpios, pad_of(ctx, p, t))):
                    waves[g].append(str(b))
                    dir_waves[g].append(str((ctx.get(p.dirs) >> k) & 1))
            if irqm is not None:
                irq_trace.append(ctx.get(irqm.after))
            if fifo is not None and ctx.get(fifo.host_rx_pending):
                rx.append((t, ctx.get(fifo.host_rx_data)))
        if fifo is not None:
            # a draining host has emptied the FIFO in the cycle of each push (the model drains one edge later); otherwise count what is stored or about to be
            state["rx_left"] = 0 if drain else ctx.get(fifo.host_rx_level) + ctx.get(fifo.host_rx_pending)
            state["tx_left"] = ctx.get(fifo.host_tx_level)

    sim = Simulator(top)
    sim.add_clock(clock_period())
    sim.add_testbench(tb)
    sim.run()
    return {"waves": {g: "".join(v) for g, v in waves.items()}, "rx": rx, "rx_left": state.get("rx_left", 0), "tx_left": state.get("tx_left", 0), "irq": irq_trace,
            "dirs": {g: "".join(v) for g, v in dir_waves.items()}}


def amaranth_waves(factory, cycles, stim=None):
    """The pin waveforms of `amaranth_run`.

    Parameters
    ----------
    factory : callable
        Returns a fresh ``(design, platform)`` pair.
    cycles : int
        The number of clock cycles.
    stim : dict or None
        Input waveforms ``{gpio: "0101..."}``.

    Returns
    -------
    dict
        ``{gpio: waveform}`` for every output GPIO.
    """
    return amaranth_run(factory, cycles, stim)["waves"]


# ----------------------------------------------------------------------------- files
def _toml_case(compiled, name, cycles, stim, golden, chip, tx=(), tx_feed=(), drain=True, golden_rx=None, irq_set=(), irq_clear=(), golden_irq=None, golden_dirs=None,
               case="amaranth_reference", program=None, header=True):
    """The text of a ``[pio.NAME]`` section with one simulation case; `case` names it, `program` selects one program of a file with several (`pioweave.block`), and
    ``header=False`` leaves out the ``[pio.NAME]`` lines when the section was written already. `golden_dirs` ``{gpio: waveform}`` adds the ``expect_dirs`` table (pin
    directions of a design with direction registers, design note 2f)."""
    lines = (["[pio.%s]" % name, 'source = "{root}/%s.pio"' % name, 'chip = "%s"' % chip, ""] if header else [""]) + [
        "[[pio.%s.sim]]" % name, 'name = "%s"' % case] + (['program = "%s"' % program] if program else []) + ["cycles = %d" % cycles]
    for k, v in compiled.sim_config().items():
        lines.append('%s = "%s"' % (k, v) if isinstance(v, str) else "%s = %s" % (k, "true" if v is True else "false" if v is False else "%d" % v))
    if tx:
        lines.append("tx = [%s]" % ", ".join(str(w) for w in tx))
    if not drain:
        lines.append("rx_drain = false")
    if golden_rx is not None:
        lines.append("expect_rx = [%s]" % ", ".join(str(w) for _, w in golden_rx))
    if golden_irq is not None:
        lines.append("expect_irq = %d" % golden_irq)
    for c, w in tx_feed:
        lines += ["", "[[pio.%s.sim.tx_feed]]" % name, "cycle = %d" % c, "value = %d" % w]
    for key, events in (("irq_set", irq_set), ("irq_clear", irq_clear)):
        for c, f in events:
            lines += ["", "[[pio.%s.sim.%s]]" % (name, key), "cycle = %d" % c, "flag = %d" % f]
    if stim:
        lines.append("")
        lines.append("[pio.%s.sim.pins_in]" % name)
        for g, w in sorted(stim.items()):
            lines.append('%d = "%s"' % (g, w))
    if golden:
        lines.append("")
        lines.append("[pio.%s.sim.expect]   # pad levels from the Amaranth simulation of the design, one character per clock cycle" % name)
        for g, w in sorted(golden.items()):
            lines.append('%d = "%s"' % (g, w))
    if golden_dirs:
        lines.append("")
        lines.append("[pio.%s.sim.expect_dirs]   # pin directions from the Amaranth simulation (1 = output), one character per clock cycle" % name)
        for g, w in sorted(golden_dirs.items()):
            lines.append('%d = "%s"' % (g, w))
    return "\n".join(lines) + "\n"


def _c_pindirs(compiled, info):
    """The ``pio_sm_set_consecutive_pindirs`` calls (SDK ``hardware/pio.h``) that give the pins of the group `info` their initial directions: one call for a group whose direction is
    fixed by the host (all outputs), one per run of equal bits for a group with a direction register (``platform.directions``, design note 2f)."""
    if info.dirs is None:
        return ["    pio_sm_set_consecutive_pindirs(pio, sm, %d, %d, true);" % (info.gpios[0], len(info.gpios))]
    runs, k, n = [], 0, len(info.gpios)
    while k < n:
        bit = (info.dirs_init >> k) & 1
        j = k
        while j < n and (info.dirs_init >> j) & 1 == bit:
            j += 1
        runs.append("    pio_sm_set_consecutive_pindirs(pio, sm, %d, %d, %s);       // initial direction of the pins of %s_%s" % (info.gpios[k], j - k, "true" if bit else "false", info.name, info.number))
        k = j
    return runs


def _c_sm_config(compiled, platform, name):
    """The lines of main.c that configure one state machine, from ``pio_sm_config c = ...`` to ``pio_sm_init``. They use the C variables ``pio``, ``sm`` and ``offset``,
    which the caller declares (`_c_main` for one state machine; `pioweave.block` declares them in a scope of its own for each machine of a block)."""
    lines = ["    pio_sm_config c = %s_program_get_default_config(offset);" % name]
    if compiled.set_pin:
        b, n = compiled.set_pin.gpios[0], len(compiled.set_pin.gpios)
        lines += ["    sm_config_set_set_pins(&c, %d, %d);" % (b, n), "    for (uint pin = %d; pin < %d; pin++) pio_gpio_init(pio, pin);" % (b, b + n)] + _c_pindirs(compiled, compiled.set_pin)
    if compiled.side_pin:
        b, n = compiled.side_pin.gpios[0], len(compiled.side_pin.gpios)
        lines += ["    sm_config_set_sideset_pins(&c, %d);" % b, "    for (uint pin = %d; pin < %d; pin++) pio_gpio_init(pio, pin);" % (b, b + n)] + _c_pindirs(compiled, compiled.side_pin)
    if compiled.out_pin and compiled.out_pin is not compiled.set_pin and compiled.out_pin is not compiled.side_pin:
        b, n = compiled.out_pin.gpios[0], len(compiled.out_pin.gpios)
        lines += ["    for (uint pin = %d; pin < %d; pin++) pio_gpio_init(pio, pin);" % (b, b + n)] + _c_pindirs(compiled, compiled.out_pin)
    if compiled.out_pin:
        lines.append("    sm_config_set_out_pins(&c, %d, %d);" % (compiled.out_pin.gpios[0], len(compiled.out_pin.gpios)))
    if compiled.in_pin:
        lines.append("    sm_config_set_in_pins(&c, %d);" % compiled.in_pin.gpios[0])
    if compiled.out_shift:
        lines.append("    sm_config_set_out_shift(&c, %s, %s, %d);" % ("true" if compiled.out_shift == "right" else "false", "true" if compiled.autopull else "false", compiled.pull_thresh))
    if compiled.in_shift:
        lines.append("    sm_config_set_in_shift(&c, %s, %s, %d);" % ("true" if compiled.in_shift == "right" else "false", "true" if compiled.autopush else "false", compiled.push_thresh))
    if compiled.fifo_join != "none":
        lines.append("    sm_config_set_fifo_join(&c, %s);" % {"tx": "PIO_FIFO_JOIN_TX", "rx": "PIO_FIFO_JOIN_RX"}[compiled.fifo_join])
    if compiled.in_count is not None:
        lines.append("    sm_config_set_in_pin_count(&c, %d);" % compiled.in_count)
    if compiled.status_sel:
        lines.append("    sm_config_set_mov_status(&c, %s, %d);" % ({"tx": "STATUS_TX_LESSTHAN", "rx": "STATUS_RX_LESSTHAN", "irq": "STATUS_IRQ_SET"}[compiled.status_sel], compiled.status_n))
    covered = {id(p) for p in (compiled.set_pin, compiled.side_pin, compiled.out_pin) if p is not None}
    for p in compiled.dir_pins:             # a group with a direction register that no instruction role set up above (for example one the program only reads)
        if id(p) not in covered:
            lines += ["    for (uint pin = %d; pin < %d; pin++) pio_gpio_init(pio, pin);" % (p.gpios[0], p.gpios[0] + len(p.gpios))] + _c_pindirs(compiled, p)
    for g in compiled.input_gpios:
        if g not in compiled.dynamic_gpios:
            lines += ["    pio_gpio_init(pio, %d);" % g, "    pio_sm_set_consecutive_pindirs(pio, sm, %d, 1, false);" % g]
    if compiled.jmp_gpio is not None:
        lines.append("    sm_config_set_jmp_pin(&c, %d);" % compiled.jmp_gpio)
    if platform.sm_clock_hz:
        lines.append("    sm_config_set_clkdiv(&c, (float)clock_get_hz(clk_sys) / SM_CLOCK_HZ);")
    lines += ["    pio_sm_init(pio, sm, offset, &c);"]
    return lines


def _c_main(compiled, name, platform, tx=(), tx_feed=(), irq_set=(), irq_clear=()):
    base = compiled.gpio_base
    lines = ['#include <stdio.h>', '#include "pico/stdlib.h"', '#include "hardware/clocks.h"', '#include "hardware/pio.h"', '#include "%s.pio.h"' % name, "",
             "// Generated from the Amaranth design by python/pio_project.py. Not run on a chip [verify].", ""]
    if platform.sm_clock_hz:
        lines += ["#define SM_CLOCK_HZ %.1ff" % float(platform.sm_clock_hz), ""]
    lines += ["int main(void) {", "    PIO pio = pio0;"]
    if base:
        lines.append("    pio_set_gpio_base(pio, %d);     // this program uses GPIO %d to %d; the SDK pin numbers below are real GPIO numbers" % (base, base, base + 31))
    if compiled.irq_rel:
        lines += ["    uint sm = 0;                      // the program uses `irq ... rel`: the flag number depends on the state machine number, the simulation case is state machine 0",
                  "    pio_sm_claim(pio, sm);"]
    else:
        lines += ["    uint sm = pio_claim_unused_sm(pio, true);"]
    lines += ["    uint offset = pio_add_program(pio, &%s_program);" % name]
    lines += _c_sm_config(compiled, platform, name)
    if compiled.uses_rx:
        lines.insert(lines.index("int main(void) {") + 1, "    stdio_init_all();")
    lines += ["    pio_sm_put_blocking(pio, sm, 0x%08xu);" % w for w in tx[:compiled.tx_depth]]       # the TX FIFO holds four words (eight when joined for TX, none when joined for RX); more would block before the machine runs
    lines += ["    pio_sm_set_enabled(pio, sm, true);"]
    lines += ["    pio_sm_put_blocking(pio, sm, 0x%08xu);       // written by the host after cycle %d in the simulation case" % (w, c) for c, w in tx_feed if compiled.tx_depth]
    events = sorted([(c, 0, f) for c, f in irq_clear] + [(c, 1, f) for c, f in irq_set])
    if events:
        lines.append("    // IRQ flags the host raises or lowers in the simulation case; the CPU cannot hit the cycle, so the order is kept and the time is approximate")
    for c, kind, f in events:
        if kind:
            lines.append("    pio->irq_force = 1u << %d;       // host raises flag %d (simulation: at the start of cycle %d)" % (f, f, c))
        else:
            lines.append("    pio_interrupt_clear(pio, %d);     // host lowers flag %d (simulation: at the start of cycle %d)" % (f, f, c))
    if compiled.uses_rx:
        lines += ["    while (true) {", '        printf("rx %08lx\\n", (unsigned long)pio_sm_get_blocking(pio, sm));', "    }", "}", ""]
    else:
        lines += ["    while (true) {", "        tight_loop_contents();", "    }", "}", ""]
    return "\n".join(lines)


def _cmake(name, usb_stdio=False):
    return "\n".join([
        "cmake_minimum_required(VERSION 3.13)",
        "# PICO_SDK_PATH is passed by bitweave ([firmware.%s].sdk_path) or set in the environment" % name,
        "include(${PICO_SDK_PATH}/external/pico_sdk_import.cmake)",
        "project(%s C CXX ASM)" % name,
        "set(CMAKE_C_STANDARD 11)",
        "pico_sdk_init()",
        "add_executable(%s main.c)" % name,
        "pico_generate_pio_header(%s ${CMAKE_CURRENT_LIST_DIR}/%s.pio)" % (name, name),
        "target_link_libraries(%s pico_stdlib hardware_pio hardware_clocks)" % name,
        ] + (["pico_enable_stdio_usb(%s 1)" % name, "pico_enable_stdio_uart(%s 0)" % name] if usb_stdio else []) + [
        "pico_add_extra_outputs(%s)" % name, ""])


def _init_of(info):
    """The MicroPython initial state of the pins of a group: all outputs low, or per pin the direction of the group's ``init`` (``rp2.PIO.IN_LOW`` for an input [verify])."""
    n = len(info.gpios)
    states = ["rp2.PIO.OUT_LOW" if info.dirs is None or (info.dirs_init >> k) & 1 else "rp2.PIO.IN_LOW" for k in range(n)]
    return states[0] if n == 1 else "(%s)" % ", ".join(states)


def _decorator(compiled):
    args = []
    if compiled.set_pin:
        args.append("set_init=" + _init_of(compiled.set_pin))
    if compiled.side_pin:
        args.append("sideset_init=" + _init_of(compiled.side_pin))
    if compiled.out_pin:
        args.append("out_init=" + _init_of(compiled.out_pin))
    if compiled.out_shift:
        args.append("out_shiftdir=rp2.PIO.SHIFT_%s" % compiled.out_shift.upper())
    if compiled.in_shift:
        args.append("in_shiftdir=rp2.PIO.SHIFT_%s" % compiled.in_shift.upper())
    if compiled.fifo_join != "none":
        args.append("fifo_join=rp2.PIO.JOIN_%s" % compiled.fifo_join.upper())
    if compiled.autopull:
        args.append("autopull=True")
    if compiled.autopush:
        args.append("autopush=True")
    if compiled.pull_thresh != 32:
        args.append("pull_thresh=%d" % compiled.pull_thresh)
    if compiled.push_thresh != 32:
        args.append("push_thresh=%d" % compiled.push_thresh)
    return "@rp2.asm_pio(%s)" % ", ".join(args)


def _micropython(compiled, name, platform, pioasm, tx=(), tx_feed=(), irq_set=(), irq_clear=()):
    if compiled.side_pindirs:
        raise PioProjectError("project='micropython' cannot configure a side-set that writes pin directions (rp2.asm_pio has no such option [verify]): use project='pico-c'")
    if compiled.status_sel or compiled.in_count is not None:
        raise PioProjectError("project='micropython' cannot configure `mov ..., status` or the IN pin count (rp2.asm_pio / rp2.StateMachine have no such option [verify]): use project='pico-c'")
    if pioasm is None:
        raise PioProjectError("project='micropython' needs pioasm to write the MicroPython program (set PIOASM or put pioasm on the PATH)\n  hint: build pioasm from the Pico SDK (tools/pioasm), or use project='pio' or 'pico-c', which do not need it")
    d = tempfile.mkdtemp(prefix="pio_mp_")
    try:
        src = os.path.join(d, name + ".pio")
        with open(src, "w") as fh:
            fh.write(compiled.program.pio_text())
        out = os.path.join(d, name + "_pio.py")
        try:
            r = subprocess.run([pioasm, "-v", str(platform.pio_version), "-o", "python", src, out], capture_output=True, text=True, timeout=PIOASM_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise PioProjectError("pioasm did not finish within %d seconds (%s)\n  hint: run `python -m pioweave doctor` to see which pioasm was found" % (PIOASM_TIMEOUT, pioasm)) from None
        except OSError as e:
            raise PioProjectError("pioasm cannot be run (%s): %s\n  hint: set PIOASM to the pioasm executable, or use project='pio' or 'pico-c', which do not need it" % (pioasm, e)) from None
        if r.returncode != 0:
            raise PioProjectError("pioasm failed:\n" + r.stderr + "\n  hint: run `python -m pioweave doctor` to see which pioasm was found; the .pio file is written first and can be assembled by hand")
        if not os.path.isfile(out):
            raise PioProjectError("pioasm ran but wrote no output file (%s)\n  hint: run `python -m pioweave doctor` to see which pioasm was found" % pioasm)
        with open(out) as fh:
            text = fh.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)
    # pioasm writes a bare @rp2.asm_pio(); the initial pin directions the design needs are added here
    text = re.sub(r"^@rp2\.asm_pio\(\)", _decorator(compiled), text, count=1, flags=re.M)
    kw = []
    if compiled.set_pin:
        kw.append("set_base=Pin(%d)" % compiled.set_pin.gpios[0])
    if compiled.side_pin:
        kw.append("sideset_base=Pin(%d)" % compiled.side_pin.gpios[0])
    if compiled.jmp_gpio is not None:
        kw.append("jmp_pin=Pin(%d, Pin.IN)" % compiled.jmp_gpio)
    if compiled.out_pin:
        kw.append("out_base=Pin(%d)" % compiled.out_pin.gpios[0])
    if compiled.in_pin:
        kw.append("in_base=Pin(%d, Pin.IN)" % compiled.in_pin.gpios[0])
    freq = "freq=%d, " % platform.sm_clock_hz if platform.sm_clock_hz else ""
    host_irq = ["# The simulation case has the host %s IRQ flag %d at the start of cycle %d; this project has no code for it (MicroPython has no call that raises or lowers a PIO IRQ flag [verify])."
                % ("raise" if k else "lower", f, c) for k, events in ((1, irq_set), (0, irq_clear)) for c, f in events]
    main = "\n".join([
        "# Generated from the Amaranth design by python/pio_project.py. Not run on a device [verify the rp2 API names against the MicroPython documentation].",
        ] + (["# This program uses GPIO %d to %d: it needs the PIO GPIO base %d, which MicroPython may not offer [verify]." % (compiled.gpio_base, compiled.gpio_base + 31, compiled.gpio_base)] if compiled.gpio_base else []) + [
        "import rp2",
        "from machine import Pin",
        "from %s_pio import %s" % (name, name), "",
        "sm = rp2.StateMachine(0, %s, %s%s)" % (name, freq, ", ".join(kw)),
        ] + host_irq + ["sm.put(0x%08x)" % w for w in tx[:compiled.tx_depth]] + [
        "sm.active(1)",
        ] + ["sm.put(0x%08x)       # written by the host after cycle %d in the simulation case" % (w, c) for c, w in tx_feed if compiled.tx_depth] + (
        ["while True:", "    print(\"rx %08x\" % sm.get())"] if compiled.uses_rx else []) + [""])
    return text, main


def _readme(compiled, name, project, platform, have_golden):
    k = {"pio": "the PIO program", "pico-c": "a Pico SDK C project", "micropython": "a MicroPython project"}[project]
    out = ["# %s" % name, "", "Generated by `platform.build(..., project=%r)` of python/platform_rp.py for the %s: %s." % (project, platform.chip, k), "",
           "States of the Amaranth design and the PIO instruction each one became (a state without effect that follows an instruction is folded into its delay):", ""]
    for st, idx in compiled.states:
        out.append("- %s: %s" % (st, "instruction %d" % idx if idx is not None else "part of the delay of the instruction before it"))
    out += ["", "Check it with bitweave (the case in `bitweave.toml` %s):" % ("compares the compiled program with the Amaranth simulation of the design" if have_golden else "has no expectations because no simulation factory was given"), "",
            "    bitweave --config bitweave.toml pio %s" % name]
    if project == "pico-c":
        out += ["    bitweave --config bitweave.toml firmware %s     # needs the Pico SDK and arm-none-eabi-gcc" % name]
    out += ["", "Nothing here has run on a chip."]
    return "\n".join(out) + "\n"


def write_project(plan, compiled, platform, name, project, sim=None):
    """Add the files of a project to a build plan.

    Parameters
    ----------
    plan : amaranth.build.run.BuildPlan
        The plan the files are added to.
    compiled : pioweave.compile.Compiled
        The compiled design.
    platform : pioweave.platform.RP2040PioPlatform
        The platform the design was compiled for.
    name : str
        The program and project name.
    project : str
        ``"pio"`` (``NAME.pio``, ``bitweave.toml`` with a simulation case, ``README.md``), ``"pico-c"`` (and a Pico SDK C project) or ``"micropython"`` (and
        ``NAME_pio.py`` written by pioasm, and ``main.py``).
    sim : SimOptions or None
        Feeds the reference simulation whose waveforms become the expectations in ``bitweave.toml``; None writes the case without expectations.

    Raises
    ------
    PioProjectError
        If `project` is not one of the three kinds, or `name` is not a plain identifier (letters, digits and ``_``, see `pioweave._validate.check_name`).
    """
    check_name(name, PioProjectError)
    if project not in ("pio", "pico-c", "micropython"):
        raise PioProjectError("project must be 'pio', 'pico-c' or 'micropython'\n  hint: pass project=\"pio\" for just the .pio file and the bitweave case")
    sim = sim or SimOptions()
    factory, cycles, stim, tx, tx_feed, drain = sim.factory, sim.cycles, sim.stim, tuple(sim.tx), tuple(sim.tx_feed), sim.drain
    irq_set, irq_clear = tuple(sim.irq_set), tuple(sim.irq_clear)
    run = amaranth_run(factory, cycles, stim, tx, tx_feed, drain, irq_set, irq_clear) if factory is not None else None
    golden = golden_rx = golden_irq = golden_dirs = None
    if run is not None:
        golden_dirs = {compiled.pin(g): w for g, w in run["dirs"].items()}
        golden = {compiled.pin(g): w for g, w in run["waves"].items() if g in compiled.output_gpios}
        golden_rx = run["rx"] if (compiled.uses_rx or run["rx"]) else None
        golden_irq = run["irq"][-1] if run["irq"] else None
    stim = {compiled.pin(g): w for g, w in (stim or {}).items()}      # bitweave takes the PIO's own pin numbers
    plan.add_file(name + ".pio", compiled.program.pio_text())
    cfg = _toml_case(compiled, name, cycles, stim, golden, platform.chip, tx, tx_feed, drain, golden_rx, irq_set, irq_clear, golden_irq, golden_dirs)
    if project == "pico-c":
        plan.add_file("CMakeLists.txt", _cmake(name, compiled.uses_rx))
        plan.add_file("main.c", _c_main(compiled, name, platform, tx, tx_feed, irq_set, irq_clear))
        board = ('board = "%s"\n' % platform.sdk_board) if platform.sdk_board else ""
        cfg = ("[firmware.%s]\nsource_dir = \"{root}\"\nchip = \"%s\"\n" % (name, platform.chip)) + board + "# sdk_path = \"/path/to/pico-sdk\"        # or set PICO_SDK_PATH\n# toolchain_dir = \"/path/to/arm-none-eabi/bin\"\n\n" + cfg
    elif project == "micropython":
        prog_py, main_py = _micropython(compiled, name, platform, find_pioasm(), tx, tx_feed, irq_set, irq_clear)
        plan.add_file(name + "_pio.py", prog_py)
        plan.add_file("main.py", main_py)
    plan.add_file("bitweave.toml", cfg)
    plan.add_file("README.md", _readme(compiled, name, project, platform, golden is not None))
    plan.add_file("build_%s.sh" % name, "#!/bin/sh\nset -e\n# the files are the product; assemble and check them with bitweave (see README.md)\n")
