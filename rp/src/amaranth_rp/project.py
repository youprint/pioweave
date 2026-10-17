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
import os
import re
import shutil
import subprocess
import sys
import tempfile

from amaranth.hdl import ClockDomain, Module
from amaranth.sim import Simulator

from ._compat import Fragment


class PioProjectError(Exception):
    """The project cannot be written."""


def find_pioasm():
    """pioasm: the PIOASM environment variable, the PATH, or next to the running Python (bitweave puts it in the bin directory of its tools environment)."""
    for c in (os.environ.get("PIOASM"), shutil.which("pioasm"), os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "pioasm")):
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None


# ----------------------------------------------------------------------------- reference simulation (Amaranth)
def amaranth_waves(factory, cycles, stim=None):
    """Simulate the design built by `factory() -> (design, platform)` for `cycles` clock cycles; returns {gpio: waveform} for every output GPIO.

    Alignment with the PIO model: the FSM state in cycle t does what the instruction executed in cycle t does; outputs are registers, so the value
    sampled after the t-th clock edge is the pad level of cycle t. Inputs are set before the edge of cycle t and are what the instruction of
    cycle t reads."""
    design, platform = factory()
    platform.simulation = True
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

    async def tb(ctx):
        for t in range(cycles):
            for g, (sig, k) in ins.items():
                w = stim.get(g, "0")
                ctx.set(sig, int(w[min(t, len(w) - 1)]))
            await ctx.tick()
            for g, (sig, k) in outs.items():
                waves[g].append(str((ctx.get(sig) >> k) & 1))

    sim = Simulator(top)
    sim.add_clock(1e-6)
    sim.add_testbench(tb)
    sim.run()
    return {g: "".join(v) for g, v in waves.items()}


# ----------------------------------------------------------------------------- files
def _toml_case(compiled, name, cycles, stim, golden, chip):
    lines = ["[pio.%s]" % name, 'source = "{root}/%s.pio"' % name, 'chip = "%s"' % chip, "",
             "[[pio.%s.sim]]" % name, 'name = "amaranth_reference"', "cycles = %d" % cycles]
    for k, v in compiled.sim_config().items():
        lines.append("%s = %d" % (k, v))
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
    return "\n".join(lines) + "\n"


def _c_main(compiled, name, platform):
    base = compiled.gpio_base
    lines = ['#include "pico/stdlib.h"', '#include "hardware/clocks.h"', '#include "hardware/pio.h"', '#include "%s.pio.h"' % name, "",
             "// Generated from the Amaranth design by python/pio_project.py. Not run on a chip [verify].", ""]
    if platform.sm_clock_hz:
        lines += ["#define SM_CLOCK_HZ %.1ff" % float(platform.sm_clock_hz), ""]
    lines += ["int main(void) {", "    PIO pio = pio0;"]
    if base:
        lines.append("    pio_set_gpio_base(pio, %d);     // this program uses GPIO %d to %d; the SDK pin numbers below are real GPIO numbers" % (base, base, base + 31))
    lines += ["    uint sm = pio_claim_unused_sm(pio, true);", "    uint offset = pio_add_program(pio, &%s_program);" % name,
              "    pio_sm_config c = %s_program_get_default_config(offset);" % name]
    if compiled.set_pin:
        b, n = compiled.set_pin.gpios[0], len(compiled.set_pin.gpios)
        lines += ["    sm_config_set_set_pins(&c, %d, %d);" % (b, n), "    for (uint pin = %d; pin < %d; pin++) pio_gpio_init(pio, pin);" % (b, b + n),
                  "    pio_sm_set_consecutive_pindirs(pio, sm, %d, %d, true);" % (b, n)]
    if compiled.side_pin:
        b, n = compiled.side_pin.gpios[0], len(compiled.side_pin.gpios)
        lines += ["    sm_config_set_sideset_pins(&c, %d);" % b, "    for (uint pin = %d; pin < %d; pin++) pio_gpio_init(pio, pin);" % (b, b + n),
                  "    pio_sm_set_consecutive_pindirs(pio, sm, %d, %d, true);" % (b, n)]
    for g in compiled.input_gpios:
        lines += ["    pio_gpio_init(pio, %d);" % g, "    pio_sm_set_consecutive_pindirs(pio, sm, %d, 1, false);" % g]
    if compiled.jmp_gpio is not None:
        lines.append("    sm_config_set_jmp_pin(&c, %d);" % compiled.jmp_gpio)
    if platform.sm_clock_hz:
        lines.append("    sm_config_set_clkdiv(&c, (float)clock_get_hz(clk_sys) / SM_CLOCK_HZ);")
    lines += ["    pio_sm_init(pio, sm, offset, &c);", "    pio_sm_set_enabled(pio, sm, true);", "    while (true) {", "        tight_loop_contents();", "    }", "}", ""]
    return "\n".join(lines)


def _cmake(name):
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
        "pico_add_extra_outputs(%s)" % name, ""])


def _decorator(compiled):
    args = []
    if compiled.set_pin:
        args.append("set_init=" + ("rp2.PIO.OUT_LOW" if len(compiled.set_pin.gpios) == 1 else "(%s)" % ", ".join(["rp2.PIO.OUT_LOW"] * len(compiled.set_pin.gpios))))
    if compiled.side_pin:
        args.append("sideset_init=" + ("rp2.PIO.OUT_LOW" if len(compiled.side_pin.gpios) == 1 else "(%s)" % ", ".join(["rp2.PIO.OUT_LOW"] * len(compiled.side_pin.gpios))))
    return "@rp2.asm_pio(%s)" % ", ".join(args)


def _micropython(compiled, name, platform, pioasm):
    if pioasm is None:
        raise PioProjectError("project='micropython' needs pioasm to write the MicroPython program (set PIOASM or put pioasm on the PATH)")
    d = tempfile.mkdtemp(prefix="pio_mp_")
    try:
        src = os.path.join(d, name + ".pio")
        with open(src, "w") as fh:
            fh.write(compiled.program.pio_text())
        out = os.path.join(d, name + "_pio.py")
        r = subprocess.run([pioasm, "-v", str(platform.pio_version), "-o", "python", src, out], capture_output=True, text=True)
        if r.returncode != 0:
            raise PioProjectError("pioasm failed:\n" + r.stderr)
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
    freq = "freq=%d, " % platform.sm_clock_hz if platform.sm_clock_hz else ""
    main = "\n".join([
        "# Generated from the Amaranth design by python/pio_project.py. Not run on a device [verify the rp2 API names against the MicroPython documentation].",
        ] + (["# This program uses GPIO %d to %d: it needs the PIO GPIO base %d, which MicroPython may not offer [verify]." % (compiled.gpio_base, compiled.gpio_base + 31, compiled.gpio_base)] if compiled.gpio_base else []) + [
        "import rp2",
        "from machine import Pin",
        "from %s_pio import %s" % (name, name), "",
        "sm = rp2.StateMachine(0, %s, %s%s)" % (name, freq, ", ".join(kw)),
        "sm.active(1)", ""])
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


def write_project(plan, compiled, platform, name, project, elaboratable=None, factory=None, cycles=64, stim=None, **ignored):
    if project not in ("pio", "pico-c", "micropython"):
        raise PioProjectError("project must be 'pio', 'pico-c' or 'micropython'")
    golden = amaranth_waves(factory, cycles, stim) if factory is not None else None
    if golden is not None:
        golden = {compiled.pin(g): w for g, w in golden.items() if g in compiled.output_gpios}
    stim = {compiled.pin(g): w for g, w in (stim or {}).items()}      # bitweave takes the PIO's own pin numbers
    plan.add_file(name + ".pio", compiled.program.pio_text())
    cfg = _toml_case(compiled, name, cycles, stim, golden, platform.chip)
    if project == "pico-c":
        plan.add_file("CMakeLists.txt", _cmake(name))
        plan.add_file("main.c", _c_main(compiled, name, platform))
        board = ('board = "%s"\n' % platform.sdk_board) if platform.sdk_board else ""
        cfg = ("[firmware.%s]\nsource_dir = \"{root}\"\nchip = \"%s\"\n" % (name, platform.chip)) + board + "# sdk_path = \"/path/to/pico-sdk\"        # or set PICO_SDK_PATH\n# toolchain_dir = \"/path/to/arm-none-eabi/bin\"\n\n" + cfg
    elif project == "micropython":
        prog_py, main_py = _micropython(compiled, name, platform, find_pioasm())
        plan.add_file(name + "_pio.py", prog_py)
        plan.add_file("main.py", main_py)
    plan.add_file("bitweave.toml", cfg)
    plan.add_file("README.md", _readme(compiled, name, project, platform, golden is not None))
    plan.add_file("build_%s.sh" % name, "#!/bin/sh\nset -e\n# the files are the product; assemble and check them with bitweave (see README.md)\n")
