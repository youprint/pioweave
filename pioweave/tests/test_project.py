# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Tests of pio_project.py: the three project kinds written by platform.build(..., project=KIND).

The generated bitweave.toml is run with the real bitweave and pioasm when they are found (skipped otherwise). The generated Pico SDK C project is
built through `bitweave firmware` only when PIO_BUILD_FIRMWARE=1 and the Pico SDK and arm-none-eabi-gcc are found (about 35 s). Nothing runs on a chip.
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))

import cosim  # noqa: E402
from pioweave import designs as pio_designs  # noqa: E402
from pioweave.project import PioProjectError, find_pioasm  # noqa: E402
from pioweave.platform import PLATFORMS
from pioweave.project import SimOptions  # noqa: E402

BW, PIOASM = cosim.find_bitweave(), cosim.find_pioasm()
needs_tools = pytest.mark.skipif(BW is None or PIOASM is None, reason="bitweave or pioasm not found (set BITWEAVE and PIOASM)")
BITWEAVE_REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SDK = os.path.join(BITWEAVE_REPO, ".tools", "src", "pico-sdk")
TOOLCHAIN = os.path.join(BITWEAVE_REPO, ".tools", "arm", "usr", "bin")


def build(tmp_path, example, kind, hz=None, with_factory=True):
    cls, chip, stim, cycles, opts = pio_designs.entry(example)
    factory = lambda: (cls(), PLATFORMS[chip](sm_clock_hz=hz))  # noqa: E731
    design, pl = factory()
    out = tmp_path / kind
    sim = SimOptions(factory=factory if with_factory else None, cycles=cycles, stim=stim, **opts)
    pl.build(design, example, str(out), project=kind, sim=sim)
    return out


def read(path):
    with open(path) as fh:
        return fh.read()


def test_pio_project_files(tmp_path):
    out = build(tmp_path, "clocked_bits", "pio")
    assert sorted(os.listdir(out)) == ["README.md", "bitweave.toml", "build_clocked_bits.sh", "clocked_bits.pio"]
    toml = read(out / "bitweave.toml")
    assert '[pio.clocked_bits]' in toml and 'source = "{root}/clocked_bits.pio"' in toml and "set_base = 0" in toml and "sideset_base = 1" in toml
    assert "[pio.clocked_bits.sim.expect]" in toml and 'chip = "rp2040"' in toml
    readme = read(out / "README.md")
    assert "HIGH" not in readme and "B1" in readme and "Nothing here has run on a chip" in readme


def test_waveforms_in_the_case_come_from_the_amaranth_simulation(tmp_path):
    out = build(tmp_path, "square_wave", "pio")
    assert '0 = "110011001100110011001100"' in read(out / "bitweave.toml")
    out2 = build(tmp_path / "x", "square_wave", "pio", with_factory=False)
    assert "expect" not in read(out2 / "bitweave.toml")
    assert "no expectations" in read(out2 / "README.md")


def test_pico_c_project_files(tmp_path):
    out = build(tmp_path, "clocked_bits", "pico-c", hz=1000000)
    assert {"CMakeLists.txt", "main.c", "clocked_bits.pio", "bitweave.toml"} <= set(os.listdir(out))
    cmake, main = read(out / "CMakeLists.txt"), read(out / "main.c")
    assert "pico_generate_pio_header(clocked_bits ${CMAKE_CURRENT_LIST_DIR}/clocked_bits.pio)" in cmake and "hardware_pio" in cmake
    for want in ('#include "clocked_bits.pio.h"', "sm_config_set_set_pins(&c, 0, 1)", "sm_config_set_sideset_pins(&c, 1)", "#define SM_CLOCK_HZ 1000000.0f",
                 "sm_config_set_clkdiv", "pio_sm_set_consecutive_pindirs(pio, sm, 0, 1, true)", "pio_sm_init(pio, sm, offset, &c)", "pio_sm_set_enabled(pio, sm, true)"):
        assert want in main, want
    toml = read(out / "bitweave.toml")
    assert "[firmware.clocked_bits]" in toml and "[pio.clocked_bits]" in toml
    no_clock = read(build(tmp_path / "y", "square_wave", "pico-c") / "main.c")
    assert "clkdiv" not in no_clock and "SM_CLOCK_HZ" not in no_clock


def test_pico_c_for_a_b_variant_sets_the_gpio_base_and_uses_real_pin_numbers(tmp_path):
    out = build(tmp_path, "high_pins", "pico-c")
    main = read(out / "main.c")
    i = main.index("pio_set_gpio_base(pio, 16)")
    assert i < main.index("pio_claim_unused_sm") < main.index("pio_add_program")
    for want in ("sm_config_set_set_pins(&c, 40, 1)", "sm_config_set_sideset_pins(&c, 41)", "pio_gpio_init(pio, 44)", "pio_sm_set_consecutive_pindirs(pio, sm, 44, 1, false)"):
        assert want in main, want
    toml = read(out / "bitweave.toml")
    assert "gpio_base = 16" in toml and "set_base = 24" in toml and 'chip = "rp2350b"' in toml
    assert '28 = "' in toml and "24 = " in toml and "25 = " in toml         # the PIO's own pin numbers in the case
    assert "pio_set_gpio_base" not in read(build(tmp_path / "a", "pin_branch", "pico-c") / "main.c")


def test_pico_c_inputs_and_jmp_pin(tmp_path):
    main = read(build(tmp_path, "pin_branch", "pico-c") / "main.c")
    assert "sm_config_set_jmp_pin(&c, 4)" in main and "pio_sm_set_consecutive_pindirs(pio, sm, 4, 1, false)" in main and "pio_gpio_init(pio, 4)" in main


@pytest.mark.skipif(find_pioasm() is None, reason="pioasm not found (set PIOASM)")
def test_micropython_project_files(tmp_path):
    out = build(tmp_path, "clocked_bits", "micropython", hz=1000000)
    assert {"main.py", "clocked_bits_pio.py", "clocked_bits.pio", "bitweave.toml"} <= set(os.listdir(out))
    prog = read(out / "clocked_bits_pio.py")
    assert "@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, sideset_init=rp2.PIO.OUT_LOW)" in prog and "def clocked_bits():" in prog
    main = read(out / "main.py")
    assert "from clocked_bits_pio import clocked_bits" in main and "rp2.StateMachine(0, clocked_bits, freq=1000000, set_base=Pin(0), sideset_base=Pin(1))" in main
    wait_main = read(build(tmp_path / "w", "pin_branch", "micropython") / "main.py")
    assert "jmp_pin=Pin(4, Pin.IN)" in wait_main


def test_micropython_needs_pioasm(tmp_path, monkeypatch):
    monkeypatch.setenv("PIOASM", str(tmp_path / "missing"))
    monkeypatch.setenv("PATH", "/nonexistent")
    from pioweave import project as pio_project
    monkeypatch.setattr(pio_project.os.path, "exists", lambda p: False)
    with pytest.raises(PioProjectError, match="needs pioasm"):
        build(tmp_path, "square_wave", "micropython")


def test_unknown_kind_and_no_loading(tmp_path):
    with pytest.raises(PioProjectError, match="project must be"):
        build(tmp_path, "square_wave", "vhdl")
    cls, chip, stim, cycles, opts = pio_designs.entry("square_wave")
    pl = PLATFORMS[chip]()
    never_elaborated = cls()
    never_elaborated._MustUse__silence = True     # build() refuses before it elaborates the design
    with pytest.raises(NotImplementedError, match="bitweave"):
        pl.build(never_elaborated, "x", str(tmp_path / "z"), do_program=True)
    plan = PLATFORMS[chip]().build(cls(), "x", str(tmp_path / "plan"), do_build=False, project="pio")
    assert "x.pio" in plan.files


# ----------------------------------------------------------------------------- bitweave runs the generated projects
def run_bitweave(project_dir, name, tool, extra_toml=""):
    cfg = read(project_dir / "bitweave.toml")
    tools = '[tools]\nbin_dirs = ["%s"]\n' % os.path.dirname(PIOASM)
    (project_dir / "run.toml").write_text(tools + cfg + extra_toml)
    r = subprocess.run([BW, "--config", str(project_dir / "run.toml"), "--json", tool, name], capture_output=True, text=True)
    return r, (json.loads(r.stdout) if r.stdout.strip().startswith("{") else None)


@needs_tools
@pytest.mark.parametrize("example", sorted(pio_designs.PROGRAMS))
@pytest.mark.parametrize("kind", ["pio", "pico-c"])
def test_generated_case_passes_in_bitweave(tmp_path, example, kind):
    out = build(tmp_path, example, kind)
    r, res = run_bitweave(out, example, "pio")
    assert r.returncode == 0, r.stdout + r.stderr
    sims = [s for s in res["steps"] if s["step"].startswith("sim:")]
    assert sims and all(s["status"] == "pass" for s in sims)


@needs_tools
def test_a_wrong_expectation_fails(tmp_path):
    out = build(tmp_path, "square_wave", "pio")
    toml = read(out / "bitweave.toml").replace("110011001100110011001100", "111111111111111111111111")
    (out / "bitweave.toml").write_text(toml)
    r, res = run_bitweave(out, "square_wave", "pio")
    assert r.returncode == 1


@needs_tools
@pytest.mark.skipif(os.environ.get("PIO_BUILD_FIRMWARE") != "1" or not os.path.exists(os.path.join(SDK, "pico_sdk_init.cmake"))
                    or not os.path.exists(os.path.join(TOOLCHAIN, "arm-none-eabi-gcc")), reason="set PIO_BUILD_FIRMWARE=1 with the Pico SDK and arm-none-eabi-gcc under ../bitweave/.tools")
@pytest.mark.parametrize("example", ["clocked_bits", "pin_branch", "high_pins"])
def test_generated_c_project_builds_with_the_pico_sdk(tmp_path, example):
    out = build(tmp_path, example, "pico-c", hz=1000000)
    chip = pio_designs.PROGRAMS[example][1]
    extra = '\n[firmware.%s]\nsdk_path = "%s"\ntoolchain_dir = "%s"\n' % (example, SDK, TOOLCHAIN)
    cfg = read(out / "bitweave.toml").replace('[firmware.%s]\n' % example, "").replace('source_dir = "{root}"\nchip = "%s"\n' % chip, "", 1)
    (out / "bitweave.toml").write_text(cfg)
    board = 'board = "waveshare_core2350b"\n' if chip.endswith("b") else ""     # a B part needs a board header with PICO_RP2350A 0
    extra = '\n[firmware.%s]\nsource_dir = "{root}"\nchip = "%s"\n%ssdk_path = "%s"\ntoolchain_dir = "%s"\n' % (example, chip, board, SDK, TOOLCHAIN)
    r, res = run_bitweave(out, example, "firmware", extra)
    assert r.returncode == 0, r.stdout + r.stderr
    with open(res["record"]) as fh:
        rec = json.load(fh)["extra"]["firmware:" + example]
    assert os.path.getsize(rec["uf2"]) > 1000 and rec["flash_bytes"] > 1000


def test_the_old_build_keywords_still_work_and_warn(tmp_path):
    cls, chip, stim, cycles, opts = pio_designs.entry("clocked_bits")
    factory = lambda: (cls(), PLATFORMS[chip]())  # noqa: E731
    new = build(tmp_path / "new", "clocked_bits", "pio")
    design, pl = factory()
    with pytest.warns(DeprecationWarning, match="sim=SimOptions"):
        pl.build(design, "clocked_bits", str(tmp_path / "old"), project="pio", factory=factory, cycles=cycles, stim=stim)
    assert read(tmp_path / "old" / "bitweave.toml") == read(new / "bitweave.toml")
    d2, p2 = factory()
    d2._MustUse__silence = True
    with pytest.raises(TypeError, match="either sim=SimOptions"):
        p2.build(d2, "x", str(tmp_path / "x"), sim=SimOptions(), cycles=3)
    with pytest.raises(TypeError, match="unexpected keyword arguments: colour"):
        p2.build(d2, "x", str(tmp_path / "x"), colour="red")


def test_designs_are_wiring_components_and_the_fifo_model_has_a_signature():
    from amaranth.lib import wiring

    from pioweave import PioDesign
    from pioweave.data import FifoModel
    for name, entry in pio_designs.PROGRAMS.items():
        d = entry[0]()
        assert isinstance(d, PioDesign) and isinstance(d, wiring.Component) and not list(d.signature.members), name
        d._MustUse__silence = True
    f = FifoModel()
    f._MustUse__silence = True
    members = dict(f.signature.members.items())
    assert members["tx_pop"].flow == wiring.In and members["tx_ready"].flow == wiring.Out
    assert len(f.tx_head) == 32 and len(f.host_tx_dropped) == 8
