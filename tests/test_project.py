# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Tests of pio_project.py: the three project kinds written by platform.build(..., project=KIND).

The generated bitweave.toml is run with the real bitweave and pioasm when they are found (skipped otherwise). The generated Pico SDK C project is
built through `bitweave firmware` only when PIO_BUILD_FIRMWARE=1 and the Pico SDK and arm-none-eabi-gcc are found (about 35 s). Nothing runs on a chip.
"""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))

from . import cosim, support  # noqa: E402
from pioweave import designs as pio_designs  # noqa: E402
from pioweave.project import PioProjectError, find_pioasm  # noqa: E402
from pioweave.platform import PLATFORMS
from pioweave.project import SimOptions  # noqa: E402

BW, PIOASM = cosim.find_bitweave(), cosim.find_pioasm()
needs_tools = unittest.skipIf(BW is None or PIOASM is None, reason="bitweave or pioasm not found (set BITWEAVE and PIOASM)")
BITWEAVE_REPO = os.environ.get("BITWEAVE_REPO") or os.path.abspath(os.path.join(HERE, "..", "..", "bitweave"))     # a bitweave checkout beside this repository
SDK = os.environ.get("PICO_SDK_PATH") or os.path.join(BITWEAVE_REPO, ".tools", "src", "pico-sdk")
TOOLCHAIN = os.environ.get("ARM_TOOLCHAIN_DIR") or os.path.join(BITWEAVE_REPO, ".tools", "arm", "usr", "bin")


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


@support.cases
class ProjectFileTests(support.TestCase):
    def test_pio_project_files(self):
        out = build(self.tmp_path, "clocked_bits", "pio")
        self.assertEqual(sorted(os.listdir(out)), ["README.md", "bitweave.toml", "build_clocked_bits.sh", "clocked_bits.pio"])
        toml = read(out / "bitweave.toml")
        self.assertTrue('[pio.clocked_bits]' in toml and 'source = "{root}/clocked_bits.pio"' in toml and "set_base = 0" in toml and "sideset_base = 1" in toml)
        self.assertTrue("[pio.clocked_bits.sim.expect]" in toml and 'chip = "rp2040"' in toml)
        readme = read(out / "README.md")
        self.assertTrue("HIGH" not in readme and "B1" in readme and "Nothing here has run on a chip" in readme)

    def test_waveforms_in_the_case_come_from_the_amaranth_simulation(self):
        out = build(self.tmp_path, "square_wave", "pio")
        self.assertIn('0 = "110011001100110011001100"', read(out / "bitweave.toml"))
        out2 = build(self.tmp_path / "x", "square_wave", "pio", with_factory=False)
        self.assertNotIn("expect", read(out2 / "bitweave.toml"))
        self.assertIn("no expectations", read(out2 / "README.md"))

    def test_pico_c_project_files(self):
        out = build(self.tmp_path, "clocked_bits", "pico-c", hz=1000000)
        self.assertLessEqual({"CMakeLists.txt", "main.c", "clocked_bits.pio", "bitweave.toml"}, set(os.listdir(out)))
        cmake, main = read(out / "CMakeLists.txt"), read(out / "main.c")
        self.assertTrue("pico_generate_pio_header(clocked_bits ${CMAKE_CURRENT_LIST_DIR}/clocked_bits.pio)" in cmake and "hardware_pio" in cmake)
        for want in ('#include "clocked_bits.pio.h"', "sm_config_set_set_pins(&c, 0, 1)", "sm_config_set_sideset_pins(&c, 1)", "#define SM_CLOCK_HZ 1000000.0f",
                     "sm_config_set_clkdiv", "pio_sm_set_consecutive_pindirs(pio, sm, 0, 1, true)", "pio_sm_init(pio, sm, offset, &c)", "pio_sm_set_enabled(pio, sm, true)"):
            self.assertIn(want, main, want)
        toml = read(out / "bitweave.toml")
        self.assertTrue("[firmware.clocked_bits]" in toml and "[pio.clocked_bits]" in toml)
        no_clock = read(build(self.tmp_path / "y", "square_wave", "pico-c") / "main.c")
        self.assertTrue("clkdiv" not in no_clock and "SM_CLOCK_HZ" not in no_clock)

    def test_pico_c_for_a_b_variant_sets_the_gpio_base_and_uses_real_pin_numbers(self):
        out = build(self.tmp_path, "high_pins", "pico-c")
        main = read(out / "main.c")
        i = main.index("pio_set_gpio_base(pio, 16)")
        self.assertTrue(i < main.index("pio_claim_unused_sm") < main.index("pio_add_program"))
        for want in ("sm_config_set_set_pins(&c, 40, 1)", "sm_config_set_sideset_pins(&c, 41)", "pio_gpio_init(pio, 44)", "pio_sm_set_consecutive_pindirs(pio, sm, 44, 1, false)"):
            self.assertIn(want, main, want)
        toml = read(out / "bitweave.toml")
        self.assertTrue("gpio_base = 16" in toml and "set_base = 24" in toml and 'chip = "rp2350b"' in toml)
        self.assertTrue('28 = "' in toml and "24 = " in toml and "25 = " in toml)         # the PIO's own pin numbers in the case
        self.assertNotIn("pio_set_gpio_base", read(build(self.tmp_path / "a", "pin_branch", "pico-c") / "main.c"))

    def test_autopull_and_autopush_reach_every_project_kind(self):
        out = build(self.tmp_path, "spi_master", "pico-c")
        toml, main = read(out / "bitweave.toml"), read(out / "main.c")
        for want in ("autopull = true", "autopush = true", "pull_thresh = 8", "push_thresh = 8", 'out_shift = "left"', 'in_shift = "left"'):
            self.assertIn(want, toml, want)
        for want in ("sm_config_set_out_shift(&c, false, true, 8)", "sm_config_set_in_shift(&c, false, true, 8)"):
            self.assertIn(want, main, want)
        # a design without them keeps the defaults of the SDK
        self.assertIn("sm_config_set_out_shift(&c, true, false, 32)", read(build(self.tmp_path / "u", "uart_tx", "pico-c") / "main.c"))
        self.assertNotIn("autopull", read(build(self.tmp_path / "u2", "uart_tx", "pio") / "bitweave.toml"))

    @unittest.skipIf(find_pioasm() is None, reason="pioasm not found (set PIOASM)")
    def test_autopull_and_autopush_reach_the_micropython_decorator(self):
        prog = read(build(self.tmp_path, "spi_master", "micropython") / "spi_master_pio.py")
        for want in ("out_shiftdir=rp2.PIO.SHIFT_LEFT", "in_shiftdir=rp2.PIO.SHIFT_LEFT", "autopull=True", "autopush=True", "pull_thresh=8", "push_thresh=8"):
            self.assertIn(want, prog, want)

    def test_irq_events_and_rel_reach_the_generated_files(self):
        out = build(self.tmp_path, "irq_handshake", "pico-c")
        toml, main = read(out / "bitweave.toml"), read(out / "main.c")
        self.assertIn("[[pio.irq_handshake.sim.irq_set]]", toml)
        self.assertIn("[[pio.irq_handshake.sim.irq_clear]]", toml)
        self.assertIn("flag = 1", toml)
        self.assertRegex(toml, r"expect_irq = \d+")
        for want in ("uint sm = 0;", "pio_sm_claim(pio, sm);", "pio->irq_force = 1u << 1;", "pio_interrupt_clear(pio, 0);"):
            self.assertIn(want, main, want)
        self.assertNotIn("pio_claim_unused_sm", main)
        self.assertIn("pio_claim_unused_sm", read(build(self.tmp_path / "n", "uart_tx", "pico-c") / "main.c"))     # without rel the SDK picks a free state machine
        self.assertIn("irq set 2 rel", read(out / "irq_handshake.pio"))

    @unittest.skipIf(find_pioasm() is None, reason="pioasm not found (set PIOASM)")
    def test_micropython_notes_the_host_irq_events_it_cannot_reproduce(self):
        main = read(build(self.tmp_path, "irq_handshake", "micropython") / "main.py")
        self.assertIn("no code for it", main)

    def test_micropython_refuses_status_and_in_count(self):
        for example, chip in (("osre_loop", "rp2040"),):
            cls, chip, stim, cycles, opts = pio_designs.entry(example)
            design, pl = cls(), PLATFORMS[chip]()
            with self.assertRaisesRegex(PioProjectError, "cannot configure `mov"):
                pl.build(design, example, str(self.tmp_path / example), project="micropython")

    def test_pin_directions_reach_the_generated_files(self):
        out = build(self.tmp_path, "open_drain", "pico-c")
        toml, main = read(out / "bitweave.toml"), read(out / "main.c")
        self.assertIn("dirs = 0", toml)
        self.assertRegex(toml, r"\[pio\.open_drain\.sim\.expect_dirs\][^\n]*\n0 = \"[01]{60}\"")
        self.assertIn("pio_sm_set_consecutive_pindirs(pio, sm, 0, 1, false);", main)           # initial direction: input
        self.assertNotIn("pio_sm_set_consecutive_pindirs(pio, sm, 0, 1, true);", main)
        self.assertIn("set pindirs, 1 [2]", read(out / "open_drain.pio"))
        toml2, main2 = (read(build(self.tmp_path / "b", "bus_turnaround", "pico-c") / f) for f in ("bitweave.toml", "main.c"))
        self.assertIn("[pio.bus_turnaround.sim.expect_dirs]", toml2)
        self.assertEqual(main2.count("pio_sm_set_consecutive_pindirs(pio, sm, 4, 4, false);"), 1)          # the OUT and IN group is one bidirectional bus
        self.assertNotIn("pio_sm_set_consecutive_pindirs(pio, sm, 4, 1, false)", main2)                      # not once more as a plain input
        self.assertIn("sm_config_set_out_pins(&c, 4, 4)", main2)
        self.assertNotIn("expect_dirs", read(build(self.tmp_path / "s", "square_wave", "pio") / "bitweave.toml"))

    def _dir_design(self, init):
        from amaranth.build import Pins, Resource
        from amaranth.hdl import Module

        from pioweave import PioDesign

        class Bus(PioDesign):
            def elaborate(self, platform):
                m = Module()
                platform.add_resources([Resource("bus", 0, Pins("4 5 6", dir="io"))])
                bus = platform.buffer(m, "io", "bus", 0)
                d = platform.directions(bus, init=init)
                with m.FSM():
                    with m.State("A"):
                        m.d.sync += d.eq(2)
                        m.next = "B"
                    with m.State("B"):
                        m.d.sync += d.eq(5)
                        m.next = "A"
                return m
        return lambda: (Bus(), PLATFORMS["rp2040"]())

    def test_a_partly_output_group_gets_one_direction_call_per_run(self):
        factory = self._dir_design(0b101)
        design, pl = factory()
        pl.build(design, "bus", str(self.tmp_path / "c"), project="pico-c", sim=SimOptions(factory=factory, cycles=8, stim={4: "0110", 5: "1010", 6: "0011"}))
        main, toml = read(self.tmp_path / "c" / "main.c"), read(self.tmp_path / "c" / "bitweave.toml")
        want = ["pio_sm_set_consecutive_pindirs(pio, sm, 4, 1, true)", "pio_sm_set_consecutive_pindirs(pio, sm, 5, 1, false)", "pio_sm_set_consecutive_pindirs(pio, sm, 6, 1, true)"]
        self.assertEqual([main.index(w) for w in want], sorted(main.index(w) for w in want))
        self.assertIn("dirs = %d" % (0b101 << 4), toml)
        dirs = toml.split("expect_dirs]")[1]
        self.assertTrue('4 = "01010101"' in dirs and '5 = "10101010"' in dirs, dirs)        # the register after each cycle: 2 (0b010), 5 (0b101), 2, ...

    @unittest.skipIf(find_pioasm() is None, reason="pioasm not found (set PIOASM)")
    def test_micropython_marks_input_pins_and_refuses_side_set_directions(self):
        factory = self._dir_design(0b101)
        design, pl = factory()
        pl.build(design, "bus", str(self.tmp_path / "m"), project="micropython", sim=SimOptions(factory=factory, cycles=8))
        self.assertIn("set_init=(rp2.PIO.OUT_LOW, rp2.PIO.IN_LOW, rp2.PIO.OUT_LOW)", read(self.tmp_path / "m" / "bus_pio.py"))
        from amaranth.build import Pins, Resource
        from amaranth.hdl import Module

        from pioweave import PioDesign

        class SideDirs(PioDesign):
            def elaborate(self, platform):
                m = Module()
                platform.add_resources([Resource("sd", 0, Pins("8", dir="io"))])
                sd = platform.buffer(m, "io", "sd", 0)
                d = platform.directions(sd)
                x = platform.scratch("x")
                with m.FSM():
                    with m.State("A"):
                        m.d.sync += [d.eq(1), x.eq(3)]            # side-set pindirs
                        m.next = "B"
                    with m.State("B"):
                        m.d.sync += d.eq(0)
                        m.next = "A"
                return m
        pl2 = PLATFORMS["rp2040"]()
        with self.assertRaisesRegex(PioProjectError, "side-set that writes pin directions"):
            pl2.build(SideDirs(), "sd", str(self.tmp_path / "sd"), project="micropython")
        pl3 = PLATFORMS["rp2040"]()
        pl3.build(SideDirs(), "sd", str(self.tmp_path / "sd3"), project="pico-c")
        self.assertIn(".side_set 1 opt pindirs", read(self.tmp_path / "sd3" / "sd.pio"))

    def test_fifo_join_and_wait_pin_reach_the_generated_files(self):
        out = build(self.tmp_path, "tx_join", "pico-c")
        toml, main = read(out / "bitweave.toml"), read(out / "main.c")
        self.assertIn('fifo_join = "tx"', toml)
        self.assertIn("sm_config_set_fifo_join(&c, PIO_FIFO_JOIN_TX);", main)
        self.assertEqual(main.count("pio_sm_put_blocking"), 9)          # eight words fit the joined FIFO of the example's nine; the ninth is the host write after cycle 30
        self.assertNotIn("printf", main)                                 # no RX FIFO to read
        gate = build(self.tmp_path / "g", "pin_gate", "pico-c")
        self.assertIn("sm_config_set_in_pins(&c, 4)", read(gate / "main.c"))
        self.assertIn("in_base = 4", read(gate / "bitweave.toml"))
        self.assertIn("wait 1 pin 1", read(gate / "pin_gate.pio"))

    @unittest.skipIf(find_pioasm() is None, reason="pioasm not found (set PIOASM)")
    def test_fifo_join_reaches_the_micropython_decorator(self):
        prog = read(build(self.tmp_path, "tx_join", "micropython") / "tx_join_pio.py")
        self.assertIn("fifo_join=rp2.PIO.JOIN_TX", prog)

    def test_pico_c_inputs_and_jmp_pin(self):
        main = read(build(self.tmp_path, "pin_branch", "pico-c") / "main.c")
        self.assertTrue("sm_config_set_jmp_pin(&c, 4)" in main and "pio_sm_set_consecutive_pindirs(pio, sm, 4, 1, false)" in main and "pio_gpio_init(pio, 4)" in main)

    @unittest.skipIf(find_pioasm() is None, reason="pioasm not found (set PIOASM)")
    def test_micropython_project_files(self):
        out = build(self.tmp_path, "clocked_bits", "micropython", hz=1000000)
        self.assertLessEqual({"main.py", "clocked_bits_pio.py", "clocked_bits.pio", "bitweave.toml"}, set(os.listdir(out)))
        prog = read(out / "clocked_bits_pio.py")
        self.assertTrue("@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, sideset_init=rp2.PIO.OUT_LOW)" in prog and "def clocked_bits():" in prog)
        main = read(out / "main.py")
        self.assertTrue("from clocked_bits_pio import clocked_bits" in main and "rp2.StateMachine(0, clocked_bits, freq=1000000, set_base=Pin(0), sideset_base=Pin(1))" in main)
        wait_main = read(build(self.tmp_path / "w", "pin_branch", "micropython") / "main.py")
        self.assertIn("jmp_pin=Pin(4, Pin.IN)", wait_main)

    def test_micropython_needs_pioasm(self):
        from pioweave import project as pio_project
        with mock.patch.dict(os.environ, {"PIOASM": str(self.tmp_path / "missing"), "PATH": "/nonexistent"}), \
                mock.patch.object(pio_project.os.path, "isfile", lambda p: False):
            with self.assertRaisesRegex(PioProjectError, "needs pioasm"):
                build(self.tmp_path, "square_wave", "micropython")

    def test_unknown_kind_and_no_loading(self):
        with self.assertRaisesRegex(PioProjectError, "project must be"):
            build(self.tmp_path, "square_wave", "vhdl")
        cls, chip, stim, cycles, opts = pio_designs.entry("square_wave")
        pl = PLATFORMS[chip]()
        never_elaborated = cls()
        never_elaborated._MustUse__silence = True     # build() refuses before it elaborates the design
        with self.assertRaisesRegex(NotImplementedError, "bitweave"):
            pl.build(never_elaborated, "x", str(self.tmp_path / "z"), do_program=True)
        plan = PLATFORMS[chip]().build(cls(), "x", str(self.tmp_path / "plan"), do_build=False, project="pio")
        self.assertIn("x.pio", plan.files)


# ----------------------------------------------------------------------------- bitweave runs the generated projects
def run_bitweave(project_dir, name, tool, extra_toml=""):
    cfg = read(project_dir / "bitweave.toml")
    tools = '[tools]\nbin_dirs = ["%s"]\n' % os.path.dirname(PIOASM)
    (project_dir / "run.toml").write_text(tools + cfg + extra_toml)
    r = subprocess.run([BW, "--config", str(project_dir / "run.toml"), "--json", tool, name], capture_output=True, text=True)
    return r, (json.loads(r.stdout) if r.stdout.strip().startswith("{") else None)


@support.cases
class BitweaveRunTests(support.TestCase):
    @needs_tools
    @support.parametrize("example", sorted(pio_designs.PROGRAMS))
    @support.parametrize("kind", ["pio", "pico-c"])
    def test_generated_case_passes_in_bitweave(self, example, kind):
        out = build(self.tmp_path, example, kind)
        r, res = run_bitweave(out, example, "pio")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sims = [s for s in res["steps"] if s["step"].startswith("sim:")]
        self.assertTrue(sims and all(s["status"] == "pass" for s in sims))

    @needs_tools
    def test_a_wrong_expectation_fails(self):
        out = build(self.tmp_path, "square_wave", "pio")
        toml = read(out / "bitweave.toml").replace("110011001100110011001100", "111111111111111111111111")
        (out / "bitweave.toml").write_text(toml)
        r, res = run_bitweave(out, "square_wave", "pio")
        self.assertEqual(r.returncode, 1)

    @needs_tools
    @unittest.skipIf(os.environ.get("PIO_BUILD_FIRMWARE") != "1" or not os.path.exists(os.path.join(SDK, "pico_sdk_init.cmake"))
                        or not os.path.exists(os.path.join(TOOLCHAIN, "arm-none-eabi-gcc")), reason="set PIO_BUILD_FIRMWARE=1 with the Pico SDK and arm-none-eabi-gcc under ../bitweave/.tools")
    @support.parametrize("example", ["clocked_bits", "pin_branch", "high_pins", "spi_master", "osre_loop", "irq_handshake", "tx_join", "pin_gate", "open_drain", "bus_turnaround"])
    def test_generated_c_project_builds_with_the_pico_sdk(self, example):
        out = build(self.tmp_path, example, "pico-c", hz=1000000)
        chip = pio_designs.PROGRAMS[example][1]
        extra = '\n[firmware.%s]\nsdk_path = "%s"\ntoolchain_dir = "%s"\n' % (example, SDK, TOOLCHAIN)
        cfg = read(out / "bitweave.toml").replace('[firmware.%s]\n' % example, "").replace('source_dir = "{root}"\nchip = "%s"\n' % chip, "", 1)
        (out / "bitweave.toml").write_text(cfg)
        board = 'board = "waveshare_core2350b"\n' if chip.endswith("b") else ""     # a B part needs a board header with PICO_RP2350A 0
        extra = '\n[firmware.%s]\nsource_dir = "{root}"\nchip = "%s"\n%ssdk_path = "%s"\ntoolchain_dir = "%s"\n' % (example, chip, board, SDK, TOOLCHAIN)
        r, res = run_bitweave(out, example, "firmware", extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(res["record"]) as fh:
            rec = json.load(fh)["extra"]["firmware:" + example]
        self.assertTrue(os.path.getsize(rec["uf2"]) > 1000 and rec["flash_bytes"] > 1000)

    def test_the_old_build_keywords_still_work_and_warn(self):
        cls, chip, stim, cycles, opts = pio_designs.entry("clocked_bits")
        factory = lambda: (cls(), PLATFORMS[chip]())  # noqa: E731
        new = build(self.tmp_path / "new", "clocked_bits", "pio")
        design, pl = factory()
        with self.assertWarnsRegex(DeprecationWarning, "sim=SimOptions"):
            pl.build(design, "clocked_bits", str(self.tmp_path / "old"), project="pio", factory=factory, cycles=cycles, stim=stim)
        self.assertEqual(read(self.tmp_path / "old" / "bitweave.toml"), read(new / "bitweave.toml"))
        d2, p2 = factory()
        d2._MustUse__silence = True
        with self.assertRaisesRegex(TypeError, "either sim=SimOptions"):
            p2.build(d2, "x", str(self.tmp_path / "x"), sim=SimOptions(), cycles=3)
        with self.assertRaisesRegex(TypeError, "unexpected keyword arguments: colour"):
            p2.build(d2, "x", str(self.tmp_path / "x"), colour="red")

    def test_designs_are_wiring_components_and_the_fifo_model_has_a_signature(self):
        from amaranth.lib import wiring

        from pioweave import PioDesign
        from pioweave.data import FifoModel
        for name, entry in pio_designs.PROGRAMS.items():
            d = entry[0]()
            self.assertTrue(isinstance(d, PioDesign) and isinstance(d, wiring.Component) and not list(d.signature.members), name)
            d._MustUse__silence = True
        f = FifoModel()
        f._MustUse__silence = True
        members = dict(f.signature.members.items())
        self.assertTrue(members["tx_pop"].flow == wiring.In and members["tx_ready"].flow == wiring.Out)
        self.assertTrue(len(f.tx_head) == 32 and len(f.host_tx_dropped) == 8)


class RP2350FeatureProject(support.TestCase):
    """The configuration calls that exist only for PIO version 1 (STATUS_IRQ_SET, sm_config_set_in_pin_count) in a generated project built with the real SDK."""

    @needs_tools
    @unittest.skipIf(os.environ.get("PIO_BUILD_FIRMWARE") != "1" or not os.path.exists(os.path.join(SDK, "pico_sdk_init.cmake"))
                        or not os.path.exists(os.path.join(TOOLCHAIN, "arm-none-eabi-gcc")), reason="set PIO_BUILD_FIRMWARE=1 with the Pico SDK and arm-none-eabi-gcc under ../bitweave/.tools")
    def test_irq_status_and_pin_count_build(self):
        from amaranth.build import Pins, Resource
        from amaranth.hdl import Module

        from pioweave import PioDesign

        class Version1Features(PioDesign):
            def elaborate(self, platform):
                m = Module()
                platform.add_resources([Resource("bus", 0, Pins("4 5", dir="i"))])
                out = platform.buffer(m, "o", "gpio", 0)
                bus = platform.buffer(m, "i", "bus", 0)
                sh = platform.shifter(m, status="irq", status_n=3)
                with m.FSM():
                    with m.State("A"):
                        sh.irq(m, 3)
                        m.next = "B"
                    with m.State("B"):
                        sh.mov(m, "x", "status")
                        m.next = "C"
                    with m.State("C"):
                        sh.mov(m, "y", bus, "reverse")
                        m.next = "D"
                    with m.State("D"):
                        sh.mov(m, out, "x")
                        m.next = "E"
                    with m.State("E"):
                        sh.irq(m, 3, "clear")
                        m.next = "A"
                return m
        factory = lambda: (Version1Features(), PLATFORMS["rp2350"]())  # noqa: E731
        design, pl = factory()
        out = self.tmp_path / "v1"
        pl.build(design, "v1", str(out), project="pico-c", sim=SimOptions(factory=factory, cycles=30, stim={4: "0110"}))
        main = read(out / "main.c")
        self.assertTrue("STATUS_IRQ_SET, 3" in main and "sm_config_set_in_pin_count(&c, 2)" in main)
        cfg = read(out / "bitweave.toml").replace('[firmware.v1]\n', "").replace('source_dir = "{root}"\nchip = "rp2350"\n', "", 1)
        (out / "bitweave.toml").write_text(cfg)
        extra = '\n[firmware.v1]\nsource_dir = "{root}"\nchip = "rp2350"\nsdk_path = "%s"\ntoolchain_dir = "%s"\n' % (SDK, TOOLCHAIN)
        r, res = run_bitweave(out, "v1", "firmware", extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
