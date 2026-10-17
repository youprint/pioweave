# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Blocks whose machines use pin directions (design notes 2f and 2g): the directions are per GPIO, a pad is the writer's level where the GPIO is an output and the stimulus
where it is an input. The Amaranth simulation of the whole block, the emulator running the compiled programs together and each machine alone in the emulator must agree on
the pads, the directions and the views; the project files carry ``expect_dirs`` and the direction of every driven GPIO is set again at the end of ``main.c``. Nothing runs
on a chip."""
import json
import os
import random
import unittest

from amaranth.hdl import Module

from pioweave.block import Block, BlockSim, Machine, block_run, emulate_block
from pioweave.designs import BusTurnaround, OpenDrain, PioDesign
from pioweave.platform import RP2040PioPlatform, RP2350PioPlatform

from . import support
from .test_block import agree, solo_matches
from .test_project import SDK, TOOLCHAIN, needs_tools, read, run_bitweave


class LineFollower(PioDesign):
    """Reads GPIO `src` (an input) and drives GPIO `dst`: high after the line went high, low after it went low (`wait` states and `set pins`)."""

    def __init__(self, src, dst):
        super().__init__()
        self.src, self.dst = src, dst

    def elaborate(self, platform):
        m = Module()
        src = platform.buffer(m, "i", "gpio", self.src)
        dst = platform.buffer(m, "o", "gpio", self.dst)
        with m.FSM():
            with m.State("WAIT_HIGH"):
                with m.If(src.i):
                    m.next = "RISE"
            with m.State("RISE"):
                m.d.sync += dst.o.eq(1)
                m.next = "WAIT_LOW"
            with m.State("WAIT_LOW"):
                with m.If(~src.i):
                    m.next = "FALL"
            with m.State("FALL"):
                m.d.sync += dst.o.eq(0)
                m.next = "WAIT_HIGH"
        return m


def open_drain_block(first=False):
    """OpenDrain (a direction register on GPIO 0) and a follower of GPIO 0 that drives GPIO 1 (RP2040); `first`: the follower is state machine 0."""
    ms = [Machine("line", OpenDrain), Machine("follow", lambda: LineFollower(0, 1))]
    return Block(ms[::-1] if first else ms, RP2040PioPlatform, name="od")


def bus_block():
    """BusTurnaround (GPIO 4 to 7 with a direction register, `out pindirs` and `mov pindirs`), OpenDrain on GPIO 0 and a follower of GPIO 5 that drives GPIO 12 (RP2350)."""
    return Block([Machine("bus", BusTurnaround), Machine("line", OpenDrain), Machine("follow", lambda: LineFollower(5, 12))], RP2350PioPlatform, name="bd")


OD_STIM = {0: "1111111100001111111111110000000011111111"}
BUS_STIM = {0: "1111000011111111", 4: "0011010110", 5: "1100101011", 6: "0101100110", 7: "1110001101"}
BUS_TX = {"bus": [0x31, 0xA5, 0x0F, 0x72]}


def random_stim(r, gpios):
    return {g: "".join(r.choice("01") for _ in range(r.randint(6, 40))) for g in gpios}


@support.cases
class BlockPinDirectionTests(support.TestCase):
    @support.parametrize("first", [False, True])
    def test_open_drain_line_and_a_follower_agree_in_all_models(self, first):
        sim = BlockSim(cycles=70, stim=OD_STIM)
        bc, a, e = agree(self, open_drain_block(first), sim, "first=%s" % first)
        solo_matches(self, bc, sim, a, "first=%s" % first)

    def test_the_pad_is_the_pull_down_where_the_pin_is_an_output_and_the_stimulus_elsewhere(self):
        sim = BlockSim(cycles=70, stim=OD_STIM)
        block = open_drain_block()
        a = block_run(block, sim)
        pad, stim = a["pads"][0], OD_STIM[0]
        self.assertIn("1", pad)
        self.assertIn("0", pad)
        self.assertNotEqual(pad, "".join(stim[min(t, len(stim) - 1)] for t in range(70)))       # the machine pulls the line low while the stimulus is high
        self.assertIn("0000", pad[:12])
        self.assertEqual(a["own_dirs"]["line"][0][:5], "11100")                                  # pulled low in cycles 0 to 2, then released
        self.assertEqual(pad[:6], "000" + stim[3:6])                                              # ... the pad follows the stimulus once the line is released
        self.assertEqual(a["own"]["line"][0], "0" * 70)                                          # the level register never changes
        self.assertIn("1", a["pads"][1])                                                         # the follower saw the line go high ...
        self.assertIn("0", a["pads"][1])                                                         # ... and low
        self.assertEqual(emulate_block(block.compile(), sim)["pads"], a["pads"])

    def test_a_bus_a_line_and_a_follower_on_the_rp2350_agree_in_all_models(self):
        sim = BlockSim(cycles=80, stim=BUS_STIM, tx=BUS_TX)
        bc, a, e = agree(self, bus_block(), sim)
        solo_matches(self, bc, sim, a)
        self.assertTrue(a["rx"]["bus"])

    @support.parametrize("seed", range(12))
    def test_random_stimulus_and_words(self, seed):
        r = random.Random(41_000 + seed)
        block = open_drain_block(r.random() < .5) if seed % 2 else bus_block()
        stim = random_stim(r, [0] if seed % 2 else [0, 4, 5, 6, 7])
        tx = {} if seed % 2 else {"bus": [r.getrandbits(32) for _ in range(r.randint(1, 4))]}
        sim = BlockSim(cycles=r.randint(40, 100), stim=stim, tx=tx, drain={"bus": r.random() < .7} if not seed % 2 else {})
        bc, a, e = agree(self, block, sim, "seed %d %s" % (seed, stim))
        solo_matches(self, bc, sim, a, "seed %d %s" % (seed, stim))

    def test_a_follower_that_only_reads_the_line_does_not_make_it_an_output(self):
        # nobody pulls the line low in the cycles after the release: the pad is the stimulus and the follower's own output stays low
        sim = BlockSim(cycles=20, stim={0: "1"})
        a = block_run(open_drain_block(), sim)
        self.assertEqual(a["pads"][0][:6], "000111")
        self.assertEqual(a["own_dirs"]["follow"], {})                       # the follower has no direction register


@support.cases
class BlockPinDirectionProjectTests(support.TestCase):
    def build(self, kind, which="od"):
        block, sim = (open_drain_block(), BlockSim(cycles=70, stim=OD_STIM)) if which == "od" else (bus_block(), BlockSim(cycles=80, stim=BUS_STIM, tx=BUS_TX))
        out = self.tmp_path / which / kind
        block.build(which, str(out), project=kind, sim=sim)
        return out

    def test_pio_project_has_the_direction_expectations_of_the_machine_with_a_register(self):
        out = self.build("pio")
        toml = read(out / "bitweave.toml")
        self.assertEqual(toml.count("[pio.od.sim.expect_dirs]"), 1)                  # only the machine `line` has a direction register
        self.assertEqual(toml.count("[[pio.od.sim]]"), 2)
        line = toml[toml.index('name = "line"'):toml.index('name = "follow"')]
        self.assertIn("[pio.od.sim.expect_dirs]", line)
        self.assertIn("dirs = 0", line)                                              # starts as an input
        self.assertNotIn("expect_dirs", toml[toml.index('name = "follow"'):])
        self.assertIn("0 = \"", line[line.index("expect_dirs"):])

    def test_pico_c_project_configures_the_initial_directions_in_each_machine_and_again_at_the_end(self):
        main = read(self.build("pico-c") / "main.c")
        self.assertIn("// initial direction of the pins of", main)                    # _c_pindirs inside the configuration of the machine `line`
        tail = main[main.index("the directions are per GPIO"):main.index("pio_enable_sm_mask_in_sync")]
        self.assertIn("pio_sm_set_consecutive_pindirs(pio, 0, 0, 1, false);", tail)       # GPIO 0 starts as an input although the follower reads it too
        self.assertIn("pio_sm_set_consecutive_pindirs(pio, 1, 1, 1, true);", tail)        # GPIO 1 is a fixed output of the follower
        main2 = read(self.build("pico-c", "bus") / "main.c")
        tail2 = main2[main2.index("the directions are per GPIO"):main2.index("pio_enable_sm_mask_in_sync")]
        for g in (4, 5, 6, 7, 0):
            self.assertIn("pio_sm_set_consecutive_pindirs(pio, %d" % (1 if g == 0 else 0) + ", %d, 1, false);" % g, tail2)
        self.assertIn("pio_sm_set_consecutive_pindirs(pio, 2, 12, 1, true);", tail2)

    @needs_tools
    @support.parametrize("which", ["od", "bus"])
    @support.parametrize("kind", ["pio", "pico-c"])
    def test_every_case_passes_in_bitweaves_simulator(self, which, kind):
        out = self.build(kind, which)
        r, res = run_bitweave(out, which, "pio")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sims = [s for s in res["steps"] if s["step"].startswith("sim:")]
        self.assertEqual(len(sims), 2 if which == "od" else 3)
        self.assertTrue(all(s["status"] == "pass" for s in sims), sims)

    @needs_tools
    def test_a_wrong_direction_expectation_fails(self):
        out = self.build("pio")
        toml = read(out / "bitweave.toml")
        a = toml.index("[pio.od.sim.expect_dirs]")
        b = toml.index('0 = "', a) + 5
        flipped = toml[:b] + ("1" if toml[b] == "0" else "0") + toml[b + 1:]
        (out / "bitweave.toml").write_text(flipped)
        r, res = run_bitweave(out, "od", "pio")
        self.assertEqual(r.returncode, 1)

    @needs_tools
    @unittest.skipIf(os.environ.get("PIO_BUILD_FIRMWARE") != "1" or not os.path.exists(os.path.join(SDK, "pico_sdk_init.cmake"))
                     or not os.path.exists(os.path.join(TOOLCHAIN, "arm-none-eabi-gcc")), reason="set PIO_BUILD_FIRMWARE=1 with the Pico SDK and arm-none-eabi-gcc under ../bitweave/.tools")
    @support.parametrize("which", ["od", "bus"])
    def test_generated_c_project_builds_with_the_pico_sdk(self, which):
        out = self.build("pico-c", which)
        chip = "rp2040" if which == "od" else "rp2350"
        cfg = read(out / "bitweave.toml").replace('source_dir = "{root}"\nchip = "%s"\n' % chip, "", 1).replace("[firmware.%s]\n" % which, "", 1)
        (out / "bitweave.toml").write_text(cfg)
        extra = '\n[firmware.%s]\nsource_dir = "{root}"\nchip = "%s"\nsdk_path = "%s"\ntoolchain_dir = "%s"\n' % (which, chip, SDK, TOOLCHAIN)
        r, res = run_bitweave(out, which, "firmware", extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(res["record"]) as fh:
            rec = json.load(fh)["extra"]["firmware:" + which]
        self.assertTrue(os.path.getsize(rec["uf2"]) > 1000)
