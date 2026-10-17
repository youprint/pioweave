"""The emulator against bitweave's PIO simulator, cycle by cycle, on random programs (every instruction form, both PIO versions) with random state machine configurations,
input waveforms and FIFO traffic. The two were written separately (the simulator in Rust, the emulator in Python) from the same datasheets: a difference is a bug in one of them.
Needs the bitweave binary and pioasm; skipped otherwise. PIO_EMU_N sets the number of cases (default 40)."""
import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from pioweave.emulator import run_case

from . import cosim
from .emu_random import random_case

BW, PIOASM = cosim.find_bitweave(), cosim.find_pioasm()
needs_tools = pytest.mark.skipif(BW is None or PIOASM is None, reason="bitweave or pioasm not found (set BITWEAVE and PIOASM)")
N = int(os.environ.get("PIO_EMU_N", "40"))
TOML_KEYS = ("set_base", "set_count", "out_base", "out_count", "sideset_base", "in_base", "in_count", "jmp_pin", "in_shift", "out_shift", "autopush", "autopull", "push_thresh",
             "pull_thresh", "x", "y", "dirs", "fifo_join", "status_sel", "status_n")


def toml_value(v):
    return '"%s"' % v if isinstance(v, str) else ("true" if v is True else "false" if v is False else str(v))


def simulator(program, cycles, cfg, stim, tx, tx_feed, drain):
    d = tempfile.mkdtemp(prefix="emu_diff_")
    try:
        src = os.path.join(d, "p.pio")
        with open(src, "w") as fh:
            fh.write(program.pio_text())
        lines = ["[project]", 'name = "d"', 'build_dir = "build"', "[tools]", 'bin_dirs = ["%s"]' % os.path.dirname(PIOASM), "[pio.p]", 'source = "%s"' % src,
                 'chip = "%s"' % program.chip, "[[pio.p.sim]]", 'name = "c"', "cycles = %d" % cycles, "trace = true"]
        lines += ["%s = %s" % (k, toml_value(cfg[k])) for k in TOML_KEYS if k in cfg]
        if tx:
            lines.append("tx = [%s]" % ", ".join(str(w) for w in tx))
        if not drain:
            lines.append("rx_drain = false")
        if stim:
            lines.append("[pio.p.sim.pins_in]")
            lines += ['%d = "%s"' % (pin, w) for pin, w in stim.items()]
        for c, w in tx_feed:
            lines += ["[[pio.p.sim.tx_feed]]", "cycle = %d" % c, "value = %d" % w]
        with open(os.path.join(d, "bitweave.toml"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        r = subprocess.run([BW, "--config", os.path.join(d, "bitweave.toml"), "--json", "pio", "p"], capture_output=True, text=True)
        out = json.loads(r.stdout)
        with open(out["record"]) as fh:
            rec = json.load(fh)
        return rec["extra"]["pio:p"]["sim"][0]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def compare(seed):
    program, cycles, cfg, stim, tx, tx_feed, drain = random_case(seed)
    version = 0 if program.chip == "rp2040" else 1
    sim = simulator(program, cycles, cfg, stim, tx, tx_feed, drain)
    emu_cfg = {k: v for k, v in cfg.items()}
    emu = run_case(program, cycles, version=version, stim=stim, tx=tx, tx_feed=tx_feed, drain=drain, record=True, on_error="stop", **emu_cfg)
    why = "seed %d\n%s\n%s" % (seed, program.pio_text(), cfg)
    if sim.get("error") and re.match(r"cycle \d+:", sim["error"]) and "cycle" in sim["error"]:
        # a program that does something undefined: both must stop at the same cycle
        assert emu.error is not None, "the simulator stopped (%s) but the emulator did not\n%s" % (sim["error"], why)
        assert int(re.match(r"cycle (\d+):", sim["error"]).group(1)) == emu.error_cycle, "%s vs cycle %s: %s\n%s" % (sim["error"], emu.error_cycle, emu.error, why)
    elif sim.get("error"):
        raise AssertionError("the simulator failed before running: %s\n%s" % (sim["error"], why))
    else:
        assert emu.error is None, "the emulator stopped (%s) but the simulator did not\n%s" % (emu.error, why)
    rows = sim["trace"] or []
    for t, row in enumerate(rows):
        e = emu.trace[t]["sms"][0]
        got = {"kind": e["kind"], "word": "%04X" % e["word"], "pc_at": e["pc_at"], "pc": e["pc"], "x": e["x"], "y": e["y"], "isr": e["isr"], "isr_count": e["isr_count"],
               "osr": e["osr"], "osr_count": e["osr_count"], "pad": emu.trace[t]["pads"], "dirs": emu.trace[t]["dirs"], "irq": emu.trace[t]["irq"]}
        want = {k: row[k] for k in got}
        assert got == want, "cycle %d\n  emulator %s\n  simulator %s\n%s" % (t, got, want, why)
    if not sim.get("error"):
        assert emu.rx == [(e["cycle"], e["value"]) for e in sim["rx"]], why
        assert (len(emu.rx_left), emu.tx_left) == (len(sim["rx_left"]), sim["tx_left"]), why
        assert (emu.rx_lost, emu.tx_dropped) == (sim["rx_lost"], sim["tx_dropped"]), why


@needs_tools
@pytest.mark.parametrize("seed", range(N))
def test_emulator_matches_the_pio_simulator(seed):
    compare(seed)
