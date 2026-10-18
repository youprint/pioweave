# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""Blocks with several state machines (pioweave/block.py): the example blocks, the semantics of the shared IRQ flags and pins, the instruction memory, the project files, the
command line, and the generated cases in bitweave's simulator (when the tools are found). The refusals are in tests/test_refusals.py, the random blocks in
tests/test_block_cosim.py. Nothing runs on a chip."""
import json
import os
import subprocess
import sys
import unittest

from amaranth.hdl import Module

from pioweave.block import Block, BlockError, BlockSim, Machine, block_run, emulate_block, machine_cases, solo_expectations
from pioweave.block_designs import BLOCKS, RingStage, entry
from pioweave.designs import ClockedBits, HighPins, PioDesign, PulseTrain
from pioweave.platform import PLATFORMS, RP2040PioPlatform, RP2350BPioPlatform

from . import support
from .test_project import BW, PIOASM, SDK, TOOLCHAIN, needs_tools, read, run_bitweave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class PinPulse(PioDesign):
    """A pulse of `width` cycles high on `gpio` every `period` cycles (set pins and folded delays)."""

    def __init__(self, gpio, width=1, period=4, value=1):
        super().__init__()
        self.gpio, self.width, self.period, self.value = gpio, width, period, value

    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", self.gpio)
        with m.FSM():
            with m.State("ON"):
                m.d.sync += pin.o.eq(self.value)
                m.next = "H0" if self.width > 1 else "OFF"
            for j in range(self.width - 1):
                with m.State("H%d" % j):
                    m.next = "H%d" % (j + 1) if j + 2 < self.width else "OFF"
            with m.State("OFF"):
                m.d.sync += pin.o.eq(1 - self.value)
                m.next = "L0" if self.period - self.width > 1 else "ON"
            for j in range(self.period - self.width - 1):
                with m.State("L%d" % j):
                    m.next = "L%d" % (j + 1) if j + 2 < self.period - self.width else "ON"
        return m


class HoldWriter(PioDesign):
    """Writes `value` to `gpio` every second cycle (`set pins` with a one-cycle delay), so the pin never changes unless another machine writes it."""

    def __init__(self, gpio, value):
        super().__init__()
        self.gpio, self.value = gpio, value

    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", self.gpio)
        with m.FSM():
            with m.State("W"):
                m.d.sync += pin.o.eq(self.value)
                m.next = "D"
            with m.State("D"):
                m.next = "W"
        return m


class IrqEveryOther(PioDesign):
    """`irq set` or `irq clear` of one flag, then a one-cycle delay, forever."""

    def __init__(self, flag, mode):
        super().__init__()
        self.flag, self.mode = flag, mode

    def elaborate(self, platform):
        m = Module()
        sh = platform.shifter(m)
        with m.FSM():
            with m.State("DO"):
                sh.irq(m, self.flag, self.mode)
                m.next = "HOLD"
            with m.State("HOLD"):
                m.next = "DO"
        return m


def agree(test, block, sim, why=""):
    """The Amaranth simulation of the block and the emulator running the compiled programs together agree on everything they both report."""
    bc = block.compile()
    a, e = block_run(block, sim, bc), emulate_block(bc, sim)
    test.assertEqual(a["pads"], e["pads"], why)
    test.assertEqual(a["view"], e["view"], why)
    test.assertTrue(not a["irq"] or a["irq"] == e["irq"], why + "\n%s\n%s" % (a["irq"], e["irq"]))
    for n in bc.names:
        if sim.drain.get(n, True):
            test.assertEqual(a["rx"][n], e["rx"][n], why + n)
        else:
            test.assertEqual([w for _, w in a["rx"][n]], e["rx_left_words"][n], why + n)
    test.assertEqual((a["tx_left"], a["rx_left"]), (e["tx_left"], e["rx_left"]), why)
    return bc, a, e


def solo_matches(test, bc, sim, a, why=""):
    """Each machine alone in the emulator, in the environment `machine_cases` derives, behaves as it did in the block."""
    for k, (n, env) in enumerate(zip(bc.names, machine_cases(bc, sim, a))):
        pads, dirs = solo_expectations(bc, k, a, env)
        test.assertEqual(env["pads"], {g: w for g, w in pads.items() if g in env["pads"]}, why + n)
        test.assertEqual(env["dirs"], dirs, why + n)
        if sim.drain.get(n, True):
            test.assertEqual(env["rx"], a["rx"][n], why + n)


@support.cases
class ExampleBlockTests(support.TestCase):
    @support.parametrize("name", sorted(BLOCKS))
    def test_amaranth_simulation_and_emulator_agree(self, name):
        block, sim = entry(name)
        bc, a, e = agree(self, block, sim, name)
        solo_matches(self, bc, sim, a, name)

    def test_uart_baud_sends_two_frames_paced_by_the_tick_flag(self):
        block, sim = entry("uart_baud")
        sim.cycles = 200
        bc = block.compile()
        a = block_run(block, sim, bc)
        tx, tick = a["pads"][0], a["pads"][1]
        self.assertEqual(tick[:24], "100000001000000010000000")             # one tick every 8 cycles
        frame = []                                                             # sample the transmitter in the middle of each 8-cycle bit after the first falling edge
        start = tx.index("0")
        for k in range(10):
            frame.append(tx[start + 8 * k + 4])
        bits = "".join(frame)
        self.assertTrue(bits[0] == "0" and bits[-1] == "1")
        self.assertEqual(int(bits[1:9][::-1], 2), 0x41)                         # least significant bit first
        self.assertEqual(bc.offsets, [0, 4])

    def test_handshake_receives_the_bytes_that_the_other_machine_puts_on_the_bus(self):
        block, sim = entry("handshake")
        bc, a, e = agree(self, block, sim)
        self.assertEqual([w for _, w in a["rx"]["consumer"]], [0xA5, 0x3C])
        self.assertEqual(a["rx"]["producer"], [])

    def test_ring_passes_the_token_round_and_the_host_starts_it(self):
        block, sim = entry("ring")
        bc, a, e = agree(self, block, sim)
        order = sorted((w.index("1"), g) for g, w in a["pads"].items())
        self.assertEqual([g for _, g in order], [0, 1, 2, 3])                   # the pulses come in the order of the stages
        block2, sim2 = entry("ring")
        sim2.irq_set = ()
        self.assertEqual(set("".join(block_run(block2, sim2)["pads"].values())), {"0"})          # nobody raised flag 0: nothing happens

    def test_example_words_are_the_same_on_every_amaranth_version(self):
        """tests/example_blocks.json holds the instruction words, load addresses and simulator configuration of every example block, written with Amaranth 0.5.10.

        The file is checked in. After adding or changing an example block, regenerate it with Amaranth 0.5.10 (from the repository root, with ``PYTHONPATH=.``)::

            import json
            from pioweave.block_designs import BLOCKS, entry
            out = {}
            for name in sorted(BLOCKS):
                block, sim = entry(name)
                bc = block.compile()
                out[name] = {"chip": bc.chip, "gpio_base": bc.gpio_base, "machines": {n: {"offset": o, "words": c.program.words(), "sim_config": c.sim_config()}
                                                                                       for n, o, c in zip(bc.names, bc.offsets, bc.compiled)}}
            with open("tests/example_blocks.json", "w") as fh:
                fh.write(json.dumps(out, indent=1, sort_keys=True))
        """
        with open(os.path.join(HERE, "example_blocks.json")) as fh:
            want = json.load(fh)
        self.assertEqual(sorted(want), sorted(BLOCKS))
        for name, ref in want.items():
            block, sim = entry(name)
            bc = block.compile()
            got = {"chip": bc.chip, "gpio_base": bc.gpio_base, "machines": {n: {"offset": o, "words": c.program.words(), "sim_config": c.sim_config()}
                                                                              for n, o, c in zip(bc.names, bc.offsets, bc.compiled)}}
            self.assertEqual(got, ref, name)


@support.cases
class SharedStateTests(support.TestCase):
    def test_a_flag_set_and_cleared_in_the_same_cycle_ends_up_set(self):
        block = Block([Machine("setter", lambda: IrqEveryOther(3, "set")), Machine("clearer", lambda: IrqEveryOther(3, "clear"))], RP2040PioPlatform)
        sim = BlockSim(cycles=12)
        bc, a, e = agree(self, block, sim)
        self.assertEqual(a["irq"], [8] * 12)
        # the other order of the machines changes nothing: it is the flag, not the priority, that decides
        block2 = Block([Machine("clearer", lambda: IrqEveryOther(3, "clear")), Machine("setter", lambda: IrqEveryOther(3, "set"))], RP2040PioPlatform)
        self.assertEqual(agree(self, block2, sim)[1]["irq"], [8] * 12)

    def test_machines_see_each_others_flags_one_cycle_after_the_change(self):
        block, sim = entry("uart_baud")
        bc, a, e = agree(self, block, sim)
        ticks = [t for t, f in enumerate(a["irq"]) if f & 1]
        self.assertEqual(ticks[:3], [0, 8, 16])                              # the generator raises flag 0 in cycles 0, 8, 16
        cleared = [t for t in range(1, len(a["irq"])) if a["irq"][t - 1] & 1 and not a["irq"][t] & 1]
        self.assertEqual(cleared[:3], [1, 9, 17])                            # the transmitter, waiting in cycle 1, passes and lowers it at the end of that cycle

    def test_the_highest_numbered_machine_wins_a_pin_that_both_write_in_the_same_cycle(self):
        for first, second, level in ((0, 1, "1"), (1, 0, "0")):
            block = Block([Machine("a", lambda v=first: HoldWriter(5, v)), Machine("b", lambda v=second: HoldWriter(5, v))], RP2040PioPlatform)
            sim = BlockSim(cycles=16)
            bc, a, e = agree(self, block, sim)
            # both write in every other cycle: machine b (state machine 1) decides, machine a never shows
            self.assertEqual(set(a["pads"][5]), {level})
            self.assertNotEqual(a["own"]["a"][5], a["own"]["b"][5])
            solo_matches(self, bc, sim, a)

    def test_a_pin_keeps_its_level_in_cycles_nobody_writes_it(self):
        block = Block([Machine("a", lambda: PinPulse(4, 1, 6)), Machine("b", lambda: PinPulse(4, 1, 4, 0))], RP2040PioPlatform)
        bc, a, e = agree(self, block, BlockSim(cycles=48))
        self.assertEqual(a["pads"][4], e["pads"][4])
        self.assertIn("0", a["pads"][4])
        self.assertIn("1", a["pads"][4])

    def test_a_machine_reads_the_pin_another_machine_drives_with_one_cycle_of_delay(self):
        block, sim = entry("handshake")
        bc, a, e = agree(self, block, sim)
        for g in range(4):
            self.assertEqual(a["view"][g], "0" + a["pads"][g][:-1])
        self.assertEqual(set(bc.compiled[1].output_gpios), set())              # the consumer drives nothing: its pins stay inputs, the producer's directions stand

    def test_programs_are_placed_one_after_the_other_and_keep_their_own_wrap_and_side_set(self):
        block = Block([Machine("pulses", PulseTrain), Machine("clock", ClockedBits), Machine("stage", lambda: RingStage(2))], RP2040PioPlatform, name="mix")
        bc = block.compile()
        n = [len(c.program.words()) for c in bc.compiled]
        self.assertEqual(bc.offsets, [0, n[0], n[0] + n[1]])
        self.assertEqual(bc.instructions, sum(n))
        text = bc.program_text()
        self.assertEqual(text.count(".program"), 3)
        self.assertIn(".program mix_clock", text)
        self.assertEqual(text.count(".side_set"), 1)                           # only the clock program has side-set
        self.assertEqual(text.count(".wrap_target"), sum(1 for c in bc.compiled if c.wrap_target_state))
        sim = BlockSim(cycles=60, irq_set=((0, 2),))
        bc2, a, e = agree(self, block, sim)                                     # pulses and the clock write GPIO 0 together; the sharing is by set pins only
        solo_matches(self, bc2, sim, a)

    def test_a_block_on_the_upper_gpio_window_uses_one_gpio_base_for_all_machines(self):
        block = Block([Machine("high", HighPins), Machine("pulse", lambda: PinPulse(20, 1, 4))], RP2350BPioPlatform)
        sim = BlockSim(cycles=40, stim={44: "000011110000011111000000111100"})
        bc, a, e = agree(self, block, sim)
        self.assertEqual(bc.gpio_base, 16)
        self.assertTrue(all(c.gpio_base == 16 for c in bc.compiled))
        self.assertEqual(sorted(a["pads"]), [20, 40, 41])
        solo_matches(self, bc, sim, a)

    def test_the_host_raises_a_flag_before_cycle_zero(self):
        block, sim = entry("ring")
        sim.cycles = 8
        bc, a, e = agree(self, block, sim)
        self.assertEqual(a["pads"][0][:3], "010")

    def test_platform_block_is_a_shortcut(self):
        pl = RP2040PioPlatform(sm_clock_hz=2_000_000)
        block = pl.block(Machine("a", lambda: PinPulse(0)), Machine("b", lambda: PinPulse(1)), name="two")
        self.assertEqual((block.platform, block.sm_clock_hz, block.name), (RP2040PioPlatform, 2_000_000, "two"))
        self.assertEqual(block.compile().offsets, [0, 2])


@support.cases
class BlockProjectTests(support.TestCase):
    def build(self, name, kind):
        block, sim = entry(name)
        out = self.tmp_path / name / kind
        block.build(name, str(out), project=kind, sim=sim)
        return out

    def test_pio_project_has_one_program_and_one_case_per_machine(self):
        out = self.build("uart_baud", "pio")
        self.assertEqual(sorted(os.listdir(out)), ["README.md", "bitweave.toml", "build_uart_baud.sh", "uart_baud.pio"])
        pio, toml = read(out / "uart_baud.pio"), read(out / "bitweave.toml")
        self.assertEqual(pio.count(".program"), 2)
        self.assertIn(".program uart_baud_baud", pio)
        self.assertIn(".program uart_baud_tx", pio)                          # prefixed: a machine called `tx` would be a pioasm keyword
        self.assertEqual(toml.count("[pio.uart_baud]"), 1)
        self.assertEqual(toml.count("[[pio.uart_baud.sim]]"), 2)
        self.assertIn('program = "uart_baud_tx"', toml)
        self.assertIn("[pio.uart_baud.sim.expect]", toml)
        self.assertIn("Nothing here has run on a chip", read(out / "README.md"))

    def test_pico_c_project_claims_each_state_machine_and_starts_them_together(self):
        out = self.build("handshake", "pico-c")
        main, cmake = read(out / "main.c"), read(out / "CMakeLists.txt")
        self.assertIn("pico_generate_pio_header(handshake ${CMAKE_CURRENT_LIST_DIR}/handshake.pio)", cmake)
        for want in ("uint sm = 0;", "uint sm = 1;", "pio_sm_claim(pio, sm);", "uint offset = 0;", "uint offset = 6;", "pio_add_program_at_offset(pio, &handshake_producer_program, offset);",
                     "pio_add_program_at_offset(pio, &handshake_consumer_program, offset);", "handshake_consumer_program_get_default_config(offset)", "pio_enable_sm_mask_in_sync(pio, 0x3);",
                     "sm_config_set_out_pins(&c, 0, 4)", "sm_config_set_in_pins(&c, 0)", 'printf("consumer rx %08lx\\n"', "0xa5000000u"):
            self.assertIn(want, main, want)
        self.assertNotIn("pio_claim_unused_sm", main)
        # the consumer only reads GPIO 0 to 3: the directions of the pins the producer drives are set again at the end, so that the outputs win
        tail = main[main.index("the directions are per GPIO"):main.index("pio_enable_sm_mask_in_sync")]
        for g in range(4):
            self.assertIn("pio_sm_set_consecutive_pindirs(pio, 0, %d, 1, true);" % g, tail)
        self.assertLess(main.index("pio_enable_sm_mask_in_sync"), main.rindex("while (true)"))

    def test_pico_c_block_zeroes_every_machine_and_bypasses_the_synchroniser_once(self):
        # COR-01 / COR-02 for blocks: each machine's scope clears X, Y, ISR and OSR; the bypass is one register of the block, set once before the machines are configured
        main = read(self.build("handshake", "pico-c") / "main.c")
        self.assertEqual(main.count("pio_sm_exec(pio, sm, pio_encode_set(pio_x, 0));"), 2)
        self.assertEqual(main.count("pio_sm_exec(pio, sm, pio_encode_mov(pio_osr, pio_null));"), 2)
        self.assertEqual(main.count("pio_set_input_sync_bypass_with_mask("), 1)
        self.assertLess(main.index("pio_set_input_sync_bypass_with_mask("), main.index("uint sm = 0;"))

    def test_host_events_and_words_are_written_in_the_order_of_the_cycles(self):
        block, sim = entry("ring")
        sim.tx_feed = {}
        out = self.tmp_path / "r"
        block.build("ring", str(out), project="pico-c", sim=sim)
        main = read(out / "main.c")
        self.assertIn("pio->irq_force = 1u << 0;", main)
        self.assertLess(main.index("pio_enable_sm_mask_in_sync"), main.index("pio->irq_force"))

    def test_micropython_is_refused_for_a_block(self):
        from pioweave.project import PioProjectError
        block, sim = entry("ring")
        with self.assertRaisesRegex(PioProjectError, "not written for a block"):
            block.build("ring", str(self.tmp_path / "m"), project="micropython", sim=sim)
        with self.assertRaisesRegex(PioProjectError, "'pio' or 'pico-c'"):
            block.build("ring", str(self.tmp_path / "v"), project="vhdl", sim=sim)

    def test_do_build_false_returns_the_plan(self):
        block, sim = entry("shared_pin")
        plan = block.build("shared_pin", do_build=False, sim=sim)
        self.assertIn("shared_pin.pio", plan.files)

    @needs_tools
    @support.parametrize("name", sorted(BLOCKS))
    @support.parametrize("kind", ["pio", "pico-c"])
    def test_every_case_passes_in_bitweaves_simulator(self, name, kind):
        out = self.build(name, kind)
        r, res = run_bitweave(out, name, "pio")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sims = [s for s in res["steps"] if s["step"].startswith("sim:")]
        block, _ = entry(name)
        self.assertEqual(len(sims), len(block.machines))
        self.assertTrue(all(s["status"] == "pass" for s in sims), sims)

    @needs_tools
    def test_a_wrong_expectation_fails(self):
        out = self.build("handshake", "pio")
        toml = read(out / "bitweave.toml").replace("expect_rx = [165, 60]", "expect_rx = [165, 61]")
        (out / "bitweave.toml").write_text(toml)
        r, res = run_bitweave(out, "handshake", "pio")
        self.assertEqual(r.returncode, 1)

    @needs_tools
    @unittest.skipIf(os.environ.get("PIO_BUILD_FIRMWARE") != "1" or not os.path.exists(os.path.join(SDK, "pico_sdk_init.cmake"))
                     or not os.path.exists(os.path.join(TOOLCHAIN, "arm-none-eabi-gcc")), reason="set PIO_BUILD_FIRMWARE=1 with the Pico SDK and arm-none-eabi-gcc under ../bitweave/.tools")
    @support.parametrize("name", ["uart_baud", "handshake", "ring", "shared_pin"])
    def test_generated_c_project_builds_with_the_pico_sdk(self, name):
        out = self.build(name, "pico-c")
        cfg = read(out / "bitweave.toml").replace('source_dir = "{root}"\nchip = "rp2040"\n', "", 1).replace("[firmware.%s]\n" % name, "", 1)
        (out / "bitweave.toml").write_text(cfg)
        extra = '\n[firmware.%s]\nsource_dir = "{root}"\nchip = "rp2040"\nsdk_path = "%s"\ntoolchain_dir = "%s"\n' % (name, SDK, TOOLCHAIN)
        r, res = run_bitweave(out, name, "firmware", extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(res["record"]) as fh:
            rec = json.load(fh)["extra"]["firmware:" + name]
        self.assertTrue(os.path.getsize(rec["uf2"]) > 1000)


class CommandLineTests(support.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""), PYTHONWARNINGS="error")
        return subprocess.run([sys.executable, "-m", "pioweave"] + list(args), cwd=str(self.tmp_path), env=env, capture_output=True, text=True, timeout=300)

    def test_list_shows_the_blocks(self):
        r = self.run_cli("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ring (block of 4 state machines: stage0, stage1, stage2, stage3)", r.stdout)
        self.assertIn("square_wave (rp2040)", r.stdout)

    def test_emulate_a_block(self):
        r = self.run_cli("emulate", "handshake")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("consumer received: 0x000000a5@12, 0x0000003c@28", r.stdout)
        self.assertIn("sm1 consumer     offset  6,  3 instructions", r.stdout)

    def test_build_a_block(self):
        r = self.run_cli("build", "uart_baud", "out", "--project", "pico-c", "--clock-hz", "2000000")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("EMIT", r.stdout)
        main = read(self.tmp_path / "out" / "project" / "main.c")
        self.assertIn("#define SM_CLOCK_HZ 2000000.0f", main)
        r2 = self.run_cli("build", "uart_baud", "out2", "--tx", "1")
        self.assertNotEqual(r2.returncode, 0)
        self.assertIn("not available for a block", r2.stderr)
