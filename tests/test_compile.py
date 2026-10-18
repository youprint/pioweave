# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""Tests of pioweave/platform.py, pioweave/compile.py and pioweave/designs.py.

The main test compares the Amaranth simulation of a design with the bitweave PIO simulator running the compiled program, pin by pin and cycle by
cycle, on the example designs and on random designs of the accepted subset. It is skipped (not passed) when the bitweave binary or pioasm is
missing (set BITWEAVE and PIOASM). PIO_COSIM_N sets the number of random designs (default 15; the full check ran 300). Nothing here runs on a chip.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

from amaranth.build import Pins, Resource  # noqa: E402
from amaranth.hdl import Elaboratable, Module, Signal  # noqa: E402

from . import cosim, support  # noqa: E402
from pioweave import designs as pio_designs  # noqa: E402
from pioweave.compile import PioCompileError, compile_design  # noqa: E402
from pioweave.platform import PLATFORMS, RP2040PioPlatform, RP2350PioPlatform, RP2354PioPlatform  # noqa: E402

BW, PIOASM = cosim.find_bitweave(), cosim.find_pioasm()
needs_tools = unittest.skipIf(BW is None or PIOASM is None, reason="bitweave or pioasm not found (set BITWEAVE and PIOASM)")


def compile_factory(factory, name="t"):
    design, platform = factory()
    return compile_design(design, platform, name)


# ----------------------------------------------------------------------------- the examples
@support.cases
class ExampleTests(support.TestCase):
    @needs_tools
    @support.parametrize("name", sorted(pio_designs.PROGRAMS))
    def test_examples_match_the_pio_simulator(self, name):
        cls, chip, stim, cycles, opts = pio_designs.entry(name)
        factory = lambda: (cls(), PLATFORMS[chip]())  # noqa: E731
        compiled = compile_factory(factory, name)
        a = cosim.amaranth_run(factory, cycles, stim, **opts)
        b = cosim.pio_run(compiled, cycles, stim, **opts)
        self.assertEqual(a["waves"], b["waves"])
        self.assertTrue(a["rx"] == b["rx"] and a["tx_left"] == b["tx_left"] and a["rx_left"] == b["rx_left"])
        self.assertTrue(not a["irq"] or a["irq"] == b["irq"])
        self.assertEqual(a["dirs"], b["dirs"])
        self.assertTrue(any("1" in w for w in a["waves"].values()) or a["rx"], "the example should drive something")

    def test_square_wave_compiles_to_two_instructions(self):
        c = compile_factory(lambda: (pio_designs.SquareWave(), RP2040PioPlatform()), "square")
        self.assertEqual(c.program.words(), [0xE101, 0xE100])            # set pins,1 [1] ; set pins,0 [1]
        self.assertEqual(c.states, [("HIGH", 0), ("HIGH_HOLD", None), ("LOW", 1), ("LOW_HOLD", None)])
        self.assertEqual(c.sim_config(), {"set_base": 0, "set_count": 1, "dirs": 1})
        self.assertIn("set pins, 1 [1]", c.program.pio_text())

    def test_pulse_train_uses_jmp_x_decrement(self):
        c = compile_factory(lambda: (pio_designs.PulseTrain(), RP2040PioPlatform()), "pt")
        text = c.program.pio_text()
        self.assertTrue("set x, 2" in text and "jmp x-- s_ON" in text)
        self.assertTrue(c.program.words()[3] == 0x0041 or "jmp x--" in text)

    def test_clocked_bits_uses_side_set_and_set_pins(self):
        c = compile_factory(lambda: (pio_designs.ClockedBits(), RP2040PioPlatform()), "cb")
        text = c.program.pio_text()
        self.assertTrue(".side_set 1 opt" in text and "side 1" in text)
        self.assertTrue(c.sim_config()["sideset_base"] == 1 and c.sim_config()["set_base"] == 0)

    def test_button_pulse_uses_wait(self):
        c = compile_factory(lambda: (pio_designs.ButtonPulse(), RP2350PioPlatform()), "bp")
        text = c.program.pio_text()
        self.assertTrue("wait 1 gpio 4" in text and "wait 0 gpio 4" in text)

    def test_pin_branch_uses_jmp_pin(self):
        c = compile_factory(lambda: (pio_designs.PinBranch(), RP2350PioPlatform()), "pb")
        self.assertIn("jmp pin", c.program.pio_text())
        self.assertEqual(c.sim_config()["jmp_pin"], 4)

    def test_high_pins_pick_gpio_base_16_on_the_48_gpio_parts(self):
        c = compile_factory(lambda: (pio_designs.HighPins(), PLATFORMS["rp2350b"]()), "hp")
        self.assertEqual(c.gpio_base, 16)
        self.assertEqual(c.sim_config(), {"set_base": 24, "set_count": 1, "sideset_base": 25, "dirs": 3 << 24, "gpio_base": 16})
        self.assertIn("wait 1 gpio 44", c.program.pio_text())          # real GPIO numbers in the program; the SDK flips bit 4 when it loads it


def _two_pins(a, b, chip):
    def body(m, pl):
        x = pl.buffer(m, "o", "gpio", a)
        y = pl.buffer(m, "o", "gpio", b)
        pl.pio_role(y, "side")

        def s0():
            m.d.sync += [x.o.eq(1), y.o.eq(1)]
            m.next = "S1"

        def s1():
            m.d.sync += [x.o.eq(0), y.o.eq(0)]
            m.next = "S0"
        _chain(m, [("S0", s0), ("S1", s1)])
    return compile_factory(lambda: (_Dyn(body), PLATFORMS[chip]()))


@support.cases
class PlatformVariantTests(support.TestCase):
    def test_gpio_window_selection_per_variant(self):
        self.assertEqual(_two_pins(3, 20, "rp2350b").gpio_base, 0)
        self.assertEqual(_two_pins(20, 40, "rp2354b").gpio_base, 16)
        self.assertEqual(_two_pins(16, 47, "rp2350b").gpio_base, 16)
        self.assertEqual(_two_pins(0, 31, "rp2350b").gpio_base, 0)
        for chip in ("rp2040", "rp2350", "rp2350a", "rp2354", "rp2354a"):
            self.assertEqual(_two_pins(2, 29, chip).gpio_base, 0)
        with self.assertRaisesRegex(PioCompileError, "do not fit one 32-GPIO window"):
            _two_pins(5, 40, "rp2350b")
        from amaranth.build import ResourceError
        for chip in ("rp2040", "rp2350a", "rp2354a"):
            with self.assertRaises(ResourceError):
                PLATFORMS[chip]().lookup("gpio", 40)          # the A variants have 30 GPIOs
        PLATFORMS["rp2354b"]().lookup("gpio", 47)

    def test_variants_are_the_same_pio_with_different_pinouts(self):
        from pioweave.platform import RP2350APioPlatform, RP2350BPioPlatform, RP2354APioPlatform, RP2354BPioPlatform
        for cls, chip, count, bases in ((RP2350APioPlatform, "rp2350a", 30, (0,)), (RP2350BPioPlatform, "rp2350b", 48, (0, 16)),
                                        (RP2354APioPlatform, "rp2354a", 30, (0,)), (RP2354BPioPlatform, "rp2354b", 48, (0, 16))):
            pl = cls()
            self.assertEqual((pl.chip, pl.pio_version, pl.gpio_count, pl.gpio_bases), (chip, 1, count, bases))
            self.assertEqual(len(cls.resources), count)
        same = [compile_factory(lambda c=c: (pio_designs.SquareWave(), PLATFORMS[c]()), "sq").program.words() for c in ("rp2350", "rp2350a", "rp2350b", "rp2354a", "rp2354b")]
        self.assertTrue(all(w == same[0] for w in same))


# ----------------------------------------------------------------------------- random designs of the subset
N = int(os.environ.get("PIO_COSIM_N", "15"))


@support.cases
class RandomDesignTests(support.TestCase):
    @needs_tools
    @support.parametrize("seed", range(N))
    def test_random_designs_match_the_pio_simulator(self, seed):
        spec = cosim.random_spec(seed)
        factory = cosim.random_factory(spec)
        compiled = compile_factory(factory, "rnd")
        cycles = 160
        ar = cosim.amaranth_run(factory, cycles, spec["stim"])
        br = cosim.pio_run(compiled, cycles, spec["stim"])
        a, b = ar["waves"], br["waves"]
        undriven = {g: w for g, w in a.items() if g not in b}
        self.assertTrue(all(set(w) <= {"0"} for w in undriven.values()), "a pin the program never drives changed in the Amaranth simulation")
        a = {g: w for g, w in a.items() if g in b}
        self.assertEqual(a, b, "seed %d\n%s\n%s" % (seed, compiled.program.pio_text(), spec))
        self.assertEqual(ar["dirs"], br["dirs"], "seed %d\n%s\n%s" % (seed, compiled.program.pio_text(), spec))


# ----------------------------------------------------------------------------- forms the random generator does not reach
class _Dyn(Elaboratable):
    def __init__(self, body):
        self.body = body
        self._MustUse__silence = True     # designs that are refused (or fail to elaborate) are not an error to report

    def elaborate(self, platform):
        m = Module()
        self.body(m, platform)
        return m


def _cosim(body, chip="rp2040", cycles=120, stim=None, resources=()):
    def factory():
        pl = PLATFORMS[chip]()
        pl.add_resources(list(resources))
        return _Dyn(body), pl
    compiled = compile_factory(factory, "t")
    a = cosim.amaranth_waves(factory, cycles, stim)
    b = cosim.pio_waves(compiled, cycles, stim)
    assert {g: w for g, w in a.items() if g in b} == b
    return compiled, b


def _chain(m, names_fns):
    with m.FSM():
        for name, fn in names_fns:
            with m.State(name):
                fn()


@support.cases
class DirectedFormTests(support.TestCase):
    @needs_tools
    def test_decrement_without_a_branch_is_jmp_x_to_the_next_state(self):
        def body(m, pl):
            pin = pl.buffer(m, "o", "gpio", 0)
            x = pl.scratch("x")

            def s0():
                m.d.sync += x.eq(3)
                m.next = "S1"

            def s1():                      # x - 1 and go on: jmp x-- S2 (both outcomes lead to the next state)
                m.d.sync += x.eq(x - 1)
                m.next = "S2"

            def s2():
                m.d.sync += pin.o.eq(1)
                m.next = "S3"

            def s3():
                m.d.sync += pin.o.eq(0)
                with m.If(x == 0):
                    m.next = "S3"
                with m.Else():
                    m.next = "S0"
            _chain(m, [("S0", s0), ("S1", s1), ("S2", s2), ("S3", s3)])
        c, waves = _cosim(body)
        self.assertIn("jmp x-- s_S2", c.program.pio_text())

    @needs_tools
    def test_last_state_going_elsewhere_sets_the_wrap_target(self):
        def body(m, pl):
            pin = pl.buffer(m, "o", "gpio", 0)

            def s0():
                m.d.sync += pin.o.eq(1)
                m.next = "S1"

            def s1():
                m.d.sync += pin.o.eq(0)
                m.next = "S2"

            def s2():                      # the last state wraps to S1, not to S0: the pin idles low after the first pulse
                m.d.sync += pin.o.eq(1)
                m.next = "S1"
            _chain(m, [("S0", s0), ("S1", s1), ("S2", s2)])
        c, waves = _cosim(body, cycles=20)
        self.assertTrue(c.wrap_target_state == 1 and ".wrap_target" in c.program.pio_text())
        self.assertEqual(waves[0], "1" + "01" * 9 + "0")

    @needs_tools
    def test_long_idle_runs_fill_the_delay_and_then_become_nops(self):
        def body(m, pl):
            pin = pl.buffer(m, "o", "gpio", 0)

            def s0():
                m.d.sync += pin.o.eq(1)
                m.next = "I0"
            states = [("S0", s0)]
            for i in range(40):
                states.append(("I%d" % i, (lambda i=i: setattr(m, "next", "I%d" % (i + 1) if i < 39 else "S1"))))

            def s1():
                m.d.sync += pin.o.eq(0)
                m.next = "S0"
            states.append(("S1", s1))
            _chain(m, states)
        c, waves = _cosim(body, cycles=100)
        text = c.program.pio_text()
        self.assertTrue("[31]" in text and "nop [7]" in text or "nop [8]" in text or "[8]" in text)

    @needs_tools
    def test_a_wide_side_set_leaves_no_bits_for_delay(self):
        def body(m, pl):
            bus = pl.buffer(m, "o", "sb", 0)
            pl.pio_role(bus, "side")
            x = pl.scratch("x")

            def s0():
                m.d.sync += [bus.o.eq(5), x.eq(1)]
                m.next = "S1"

            def s1():
                m.next = "S2"

            def s2():
                m.d.sync += bus.o.eq(10)
                m.next = "S0"
            _chain(m, [("S0", s0), ("S1", s1), ("S2", s2)])
        c, waves = _cosim(body, cycles=24, resources=[Resource("sb", 0, Pins("8 9 10 11", dir="o"))])
        self.assertTrue(c.program.pio_text().count("nop") == 2 and "[" not in c.program.pio_text().split(".side_set")[1])

    @needs_tools
    def test_a_pin_driven_both_as_set_and_as_side_set(self):
        def body(m, pl):
            pin = pl.buffer(m, "o", "gpio", 0)
            x = pl.scratch("x")

            def s0():
                m.d.sync += pin.o.eq(1)           # alone: set pins
                m.next = "S1"

            def s1():
                m.d.sync += [pin.o.eq(0), x.eq(2)]  # with a register operation: side-set
                m.next = "S2"

            def s2():
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "S2"
                with m.Else():
                    m.next = "S0"
            _chain(m, [("S0", s0), ("S1", s1), ("S2", s2)])
        c, waves = _cosim(body, cycles=40)
        self.assertTrue(".side_set 1 opt" in c.program.pio_text() and c.sim_config()["set_base"] == 0 and c.sim_config()["sideset_base"] == 0)

    def test_program_order_is_the_definition_order_not_amarans_state_numbers(self):
        """Amaranth numbers FSM states by first mention; PIO addresses follow the order of the m.State blocks. A design whose transitions assume the
        other order is refused (here A is followed by C in the source but goes to B), and the error points at the user's line."""
        def refused(m, pl):
            pin = pl.buffer(m, "o", "gpio", 0)

            def a():
                m.d.sync += pin.o.eq(1)
                m.next = "B"                  # B is defined last, so it is not the instruction after A

            def c():
                m.d.sync += pin.o.eq(0)
                m.next = "A"

            def b():
                m.next = "C"
            with m.FSM():
                with m.State("A"):
                    a()
                with m.State("C"):
                    c()
                with m.State("B"):
                    b()
        with self.assertRaisesRegex(PioCompileError, r"test_compile\.py:\d+: a state that changes a pin and then jumps"):
            compile_factory(lambda: (_Dyn(refused), RP2040PioPlatform()))

    @needs_tools
    def test_forward_references_compile_when_the_order_matches(self):
        def body(m, pl):
            pin = pl.buffer(m, "o", "gpio", 0)

            def a():
                m.d.sync += pin.o.eq(1)
                m.next = "B"                  # mentioned before it is defined; B is next in the source too

            def b():
                m.d.sync += pin.o.eq(0)
                m.next = "A"
            _chain(m, [("A", a), ("B", b)])
        c, waves = _cosim(body, cycles=10)
        self.assertEqual(waves[0], "1010101010")


# ----------------------------------------------------------------------------- the data path (FIFOs, OSR, ISR)
def run_both(name, **override):
    cls, chip, stim, cycles, opts = pio_designs.entry(name)
    opts = dict(opts, **override)
    factory = lambda: (cls(), PLATFORMS[chip]())  # noqa: E731
    compiled = compile_factory(factory, name)
    a = cosim.amaranth_run(factory, cycles, stim, **opts)
    b = cosim.pio_run(compiled, cycles, stim, **opts)
    return compiled, a, b


@support.cases
class DataPathTests(support.TestCase):
    def test_uart_tx_compiles_to_the_sdk_program(self):
        c = compile_factory(lambda: (pio_designs.UartTx(), RP2040PioPlatform()), "uart_tx")
        text = c.program.pio_text()
        for line in ("pull block side 1 [1]", "set x, 7 side 0", "out pins, 1", "jmp x-- s_BIT"):
            self.assertIn(line, text, text)
        self.assertEqual(c.sim_config(), {"sideset_base": 0, "dirs": 1, "out_base": 0, "out_count": 1, "out_shift": "right"})
        self.assertTrue(c.uses_tx and not c.uses_rx)

    @needs_tools
    def test_uart_tx_sends_frames_like_the_amaranth_design(self):
        c, a, b = run_both("uart_tx")
        self.assertEqual(a["waves"], b["waves"])
        wave = a["waves"][0]
        # COR-04: 8N1 at two cycles per bit: idle high, a start bit of TWO cycles, then 0x55 least significant bit first at two cycles per bit (out + jmp)
        frame = wave[wave.index("0"):][:18]
        self.assertTrue(frame == "00" + "".join(c * 2 for c in "10101010") and a["tx_left"] == 0, frame)

    @needs_tools
    def test_uart_tx_frames_decode_as_8n1_at_two_cycles_per_bit(self):
        # an independent check against the 8N1 definition (start bit low, 8 data bits least significant first, stop bit high), not against the program text
        c, a, b = run_both("uart_tx")
        wave, bytes_, i = a["waves"][0], [], 0
        while "0" in wave[i:]:
            i += wave[i:].index("0")                            # the falling edge of a start bit
            if i + 20 > len(wave):
                break
            self.assertTrue(wave[i:i + 2] == "00", "start bit lasts two cycles: " + wave[i:i + 6])
            bits = [int(wave[i + 2 + 2 * k]) for k in range(8)]       # sampled at the first cycle of each bit
            self.assertEqual([wave[i + 2 + 2 * k + 1] for k in range(8)], [wave[i + 2 + 2 * k] for k in range(8)], "each bit lasts two cycles")
            self.assertEqual(wave[i + 18:i + 20], "11", "stop bit high for two cycles")
            bytes_.append(sum(bit << k for k, bit in enumerate(bits)))
            i += 20
        self.assertEqual(bytes_, [0x55, 0xC3])

    @needs_tools
    def test_uart_rx_receives_the_bytes(self):
        c, a, b = run_both("uart_rx")
        self.assertTrue(a["rx"] == b["rx"] and [w for _, w in a["rx"]] == [0x5A000000, 0xC3000000])
        self.assertTrue(c.sim_config()["in_base"] == 4 and c.in_shift == "right" and c.uses_rx and not c.uses_tx)

    @needs_tools
    def test_byte_swap_uses_x_y_null_and_left_shifts(self):
        c, a, b = run_both("byte_swap")
        self.assertEqual(a["rx"], b["rx"])
        # 0x11223344: X = 0x11, Y = 0x22, ISR = 0 (16 bits) then Y (0x22) then X (0x11)
        self.assertTrue(a["rx"][0][1] == 0x00002211 and a["rx"][1][1] == 0x0000BBAA)
        self.assertTrue("in null, 16" in c.program.pio_text() and c.out_shift == "left" and c.in_shift == "left")

    @needs_tools
    def test_noblock_pull_and_push(self):
        c, a, b = run_both("noblock_echo")
        self.assertTrue(a["rx"] == b["rx"] and [w for _, w in a["rx"]][:3] == [7, 9, 21])
        # without draining the RX FIFO fills up after four words; the fifth is dropped by `push noblock` in both models
        c, a, b = run_both("noblock_echo", drain=False)
        self.assertTrue(cosim.same_rx(a, b, drain=False) and a["rx_left"] == b["rx_left"] == 4 and [w for _, w in a["rx"]] == [7, 9, 21, 21])
        self.assertEqual(a["waves"], b["waves"])
        self.assertTrue(a["tx_left"] == b["tx_left"] == 0)

    @needs_tools
    def test_blocking_pull_stalls_until_the_host_writes(self):
        c, a, b = run_both("byte_swap", tx=[], tx_feed=[(30, 0x01020304)])
        self.assertTrue(a["rx"] == b["rx"] and a["rx"] and a["rx"][0][0] > 30 and a["rx"][0][1] == 0x00000201)
        c, a, b = run_both("byte_swap", tx=[], tx_feed=[])
        self.assertTrue(a["rx"] == b["rx"] == [] and a["tx_left"] == b["tx_left"])

    # ----------------------------------------------------------------------------- random designs with a data path
    @needs_tools
    @support.parametrize("seed", range(N))
    def test_random_data_path_designs_match_the_pio_simulator(self, seed):
        spec = cosim.random_data_spec(seed)
        factory = cosim.random_data_factory(spec)
        compiled = compile_factory(factory, "rnd")
        cycles = 160
        events = dict(irq_set=spec["irq_set"], irq_clear=spec["irq_clear"])
        a = cosim.amaranth_run(factory, cycles, spec["stim"], tx=spec["tx"], tx_feed=spec["tx_feed"], drain=spec["drain"], **events)
        b = cosim.pio_run(compiled, cycles, spec["stim"], tx=spec["tx"], tx_feed=spec["tx_feed"], drain=spec["drain"], **events)
        why = "seed %d\n%s\n%s" % (seed, compiled.program.pio_text(), spec)
        self.assertTrue(not a["irq"] or a["irq"] == b["irq"], why + "\n%s\n%s" % (a["irq"], b["irq"]))
        self.assertEqual({g: w for g, w in a["waves"].items() if g in b["waves"]}, b["waves"], why)
        self.assertTrue(cosim.same_rx(a, b, spec["drain"]), why)
        self.assertEqual((a["tx_left"], a["rx_left"]), (b["tx_left"], b["rx_left"]), why)
        self.assertEqual(a["dirs"], b["dirs"], why)

    # ----------------------------------------------------------------------------- the same programs on every Amaranth version
    def test_example_programs_are_the_same_on_every_amaranth_version(self):
        """tests/example_programs.json holds the instruction words and the simulator configuration of every example design, written with Amaranth 0.5.10.
        The matrix (scripts/matrix.sh) runs this on the other versions: a different word means the compiler read the design differently.

        The file is checked in. After adding or changing an example design, regenerate it with Amaranth 0.5.10 (from the repository root, with ``PYTHONPATH=.``)::

            import json
            from pioweave import PLATFORMS, compile_design, designs
            out = {}
            for name in sorted(designs.PROGRAMS):
                cls, chip, stim, cycles, opts = designs.entry(name)
                c = compile_design(cls(), PLATFORMS[chip](), name)
                out[name] = {"chip": chip, "words": c.program.words(), "sim_config": c.sim_config()}
            with open("tests/example_programs.json", "w") as fh:
                fh.write(json.dumps(out, indent=1, sort_keys=True))
        """
        import json
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "example_programs.json")) as fh:
            want = json.load(fh)
        self.assertEqual(sorted(want), sorted(pio_designs.PROGRAMS))
        for name, ref in want.items():
            cls, chip, stim, cycles, opts = pio_designs.entry(name)
            c = compile_factory(lambda cls=cls, chip=chip: (cls(), PLATFORMS[chip]()), name)
            self.assertEqual({"chip": chip, "words": c.program.words(), "sim_config": c.sim_config()}, ref, name)


# ----------------------------------------------------------------------------- autopull / autopush, pull ifempty / push iffull, mov with the OSR / ISR / pins / status
def data_design(body, chip="rp2040", resources=(), **config):
    """A design with one shifter (`config` goes to platform.shifter) and the pins gpio 0 (output), gpio 1 (side-set output) and gpio 4 (input); `body(m, sh, pl, io)` builds the FSM."""
    def factory():
        pl = PLATFORMS[chip]()
        pl.add_resources(list(resources))

        def build(m, platform):
            sh = platform.shifter(m, **config)
            io_ = {"out": platform.buffer(m, "o", "gpio", 0), "side": platform.buffer(m, "o", "gpio", 1), "in": platform.buffer(m, "i", "gpio", 4)}
            platform.pio_role(io_["side"], "side")
            body(m, sh, platform, io_)
        return _Dyn(build), pl
    return factory


def both_ways(factory, cycles=80, stim=None, tx=(), tx_feed=(), drain=True, irq_set=(), irq_clear=()):
    """Compile `factory`, run it in the Amaranth simulation and in the bitweave PIO simulator; assert that pins, words pushed, the IRQ flags after every cycle and the FIFO
    contents left agree."""
    compiled = compile_factory(factory, "t")
    a = cosim.amaranth_run(factory, cycles, stim, tx, tx_feed, drain, irq_set, irq_clear)
    b = cosim.pio_run(compiled, cycles, stim, tx=tx, tx_feed=tuple(tx_feed), drain=drain, irq_set=irq_set, irq_clear=irq_clear)
    assert {g: w for g, w in a["waves"].items() if g in b["waves"]} == b["waves"], (a["waves"], b["waves"], compiled.program.pio_text())
    assert cosim.same_rx(a, b, drain) and (a["tx_left"], a["rx_left"]) == (b["tx_left"], b["rx_left"]), (a, b, compiled.program.pio_text())
    assert not a["irq"] or a["irq"] == b["irq"], (a["irq"], b["irq"], compiled.program.pio_text())
    assert a["dirs"] == b["dirs"], (a["dirs"], b["dirs"], compiled.program.pio_text())
    return compiled, a, b


@support.cases
class AutoTransferTests(support.TestCase):
    @needs_tools
    def test_autopull_out_stalls_when_the_tx_fifo_is_empty_and_refills_in_the_background(self):
        def body(m, sh, pl, io):
            with m.FSM():
                with m.State("OUT"):
                    with sh.out(m, io["out"], 1):
                        m.next = "HOLD"
                with m.State("HOLD"):
                    m.next = "OUT"
        c, a, b = both_ways(data_design(body, autopull=True, pull_thresh=4), cycles=60, tx=[0b0101], tx_feed=[(30, 0b1111)])
        self.assertEqual(c.program.pio_text().count("out pins, 1"), 1)
        w = a["waves"][0]
        # cycle 0 is the stall in which the empty OSR is refilled; then the four bits of 0b0101, least significant first, two cycles each; then the pin keeps its level
        # while the OUT waits for the FIFO, until the host writes the next word in cycle 30
        self.assertTrue(w[:9] == "011001100" and w[9:31] == "0" * 22 and "1" in w[31:], w)
        self.assertEqual(c.sim_config()["pull_thresh"], 4)

    @needs_tools
    def test_autopush_in_stalls_while_the_rx_fifo_is_full(self):
        def body(m, sh, pl, io):
            with m.FSM():
                with m.State("IN"):
                    m.d.sync += io["side"].o.eq(1)
                    with sh.in_(m, "x", 8):
                        m.next = "TOGGLE"
                with m.State("TOGGLE"):
                    m.d.sync += io["side"].o.eq(0)
                    m.next = "IN"
        c, a, b = both_ways(data_design(body, autopush=True, push_thresh=8), cycles=40, drain=False)
        self.assertTrue(a["rx_left"] == b["rx_left"] == 4)
        self.assertTrue(a["waves"][1][-10:] == "1" * 10)      # the fifth push cannot complete: the in stalls (side-set 1) and the pin stays high

    @needs_tools
    def test_autopush_with_a_threshold_below_32_and_pull_noop_after_autopull(self):
        def body(m, sh, pl, io):
            x = pl.scratch("x")
            with m.FSM():
                with m.State("SET"):
                    m.d.sync += x.eq(5)
                    m.next = "PULL"
                with m.State("PULL"):
                    with sh.pull(m):                 # does nothing right after an autopull refill (osr count 0)
                        m.next = "OUT"
                with m.State("OUT"):
                    with sh.out(m, "x", 3):
                        m.next = "IN"
                with m.State("IN"):
                    with sh.in_(m, "x", 3):
                        m.next = "SET"
        both_ways(data_design(body, autopull=True, pull_thresh=6, autopush=True, push_thresh=6), cycles=80, tx=[0x2D, 0x1B, 7], tx_feed=[(40, 0x55)])


@support.cases
class ExtendedInstructionTests(support.TestCase):
    def one_state_each(self, ops, chip="rp2040", **config):
        """A design with one state per entry of `ops` (callables (m, sh, pl, io)); returns the compiled program."""
        def body(m, sh, pl, io):
            with m.FSM():
                for i, op in enumerate(ops):
                    with m.State("S%d" % i):
                        op(m, sh, pl, io)
                        m.next = "S%d" % ((i + 1) % len(ops))
        return compile_factory(data_design(body, chip, **config), "t")

    def test_the_text_of_the_new_forms(self):
        def pull_ifempty(m, sh, pl, io):
            with sh.pull(m, block=False, ifempty=True):
                pass

        def push_iffull_nb(m, sh, pl, io):
            with sh.push(m, block=False, iffull=True):
                pass
        ops = [pull_ifempty, push_iffull_nb,
               lambda m, sh, pl, io: sh.mov(m, "x", "osr"), lambda m, sh, pl, io: sh.mov(m, "osr", "y", "reverse"), lambda m, sh, pl, io: sh.mov(m, "isr", "x", "invert"),
               lambda m, sh, pl, io: sh.mov(m, io["out"], "isr"), lambda m, sh, pl, io: sh.mov(m, "y", "status"),
               lambda m, sh, pl, io: sh.in_(m, "isr", 8), lambda m, sh, pl, io: sh.in_(m, "osr", 7), lambda m, sh, pl, io: sh.out(m, "isr", 5),
               lambda m, sh, pl, io: sh.mov(m, "x", "null", "invert")]
        c = self.one_state_each(ops, status="rx", status_n=3, pull_thresh=16, push_thresh=12)
        text = c.program.pio_text()
        for line in ("pull ifempty noblock", "push iffull noblock", "mov x, osr", "mov osr, ::y", "mov isr, !x", "mov pins, isr", "mov y, status", "in isr, 8", "in osr, 7", "out isr, 5", "mov x, !null"):
            self.assertIn(line, text)
        cfg = c.sim_config()
        self.assertTrue(cfg["status_sel"] == "rx" and cfg["status_n"] == 3 and cfg["pull_thresh"] == 16 and cfg["push_thresh"] == 12 and "autopull" not in cfg)

    def test_mov_from_pins_masks_the_input_on_pio_version_1(self):
        ops = [lambda m, sh, pl, io: sh.mov(m, "x", io["in"])]
        c = self.one_state_each(ops + [lambda m, sh, pl, io: sh.mov(m, "osr", "x")], chip="rp2350")
        self.assertIn("mov x, pins", c.program.pio_text())
        self.assertEqual(c.sim_config()["in_count"], 1)
        self.assertEqual(c.in_pin.gpios, [4])

    def test_jmp_not_osre(self):
        def body(m, sh, pl, io):
            with m.FSM():
                with m.State("A"):
                    sh.out(m, io["out"], 1)
                    m.next = "B"
                with m.State("B"):
                    with m.If(sh.osr_not_empty):
                        m.next = "A"
                    with m.Else():
                        m.next = "C"
                with m.State("C"):
                    m.next = "A"
        c = compile_factory(data_design(body, pull_thresh=4), "t")
        self.assertIn("jmp !osre s_A", c.program.pio_text())

    @needs_tools
    def test_every_new_form_agrees_with_the_pio_simulator(self):
        def body(m, sh, pl, io):
            x, y = pl.scratch("x"), pl.scratch("y")
            with m.FSM():
                with m.State("PULL"):
                    with sh.pull(m, ifempty=True):
                        m.next = "OUT"
                with m.State("OUT"):
                    with sh.out(m, "isr", 5):
                        m.next = "MOV_X"
                with m.State("MOV_X"):
                    sh.mov(m, "x", "osr", "reverse")
                    m.next = "MOV_Y"
                with m.State("MOV_Y"):
                    sh.mov(m, "y", "isr", "invert")
                    m.next = "IN_X"
                with m.State("IN_X"):
                    with sh.in_(m, "osr", 6):
                        m.next = "IN_Y"
                with m.State("IN_Y"):
                    with sh.in_(m, "isr", 7):
                        m.next = "MOV_OSR"
                with m.State("MOV_OSR"):
                    sh.mov(m, "osr", "x", "invert")
                    m.next = "OUTP"
                with m.State("OUTP"):
                    with sh.out(m, io["out"], 1):
                        m.next = "ST"
                with m.State("ST"):
                    sh.mov(m, "x", "status")
                    m.next = "PUSH"
                with m.State("PUSH"):
                    with sh.push(m, iffull=True):
                        m.next = "OSRE"
                with m.State("OSRE"):
                    with m.If(sh.osr_not_empty):
                        m.next = "OUTP"
                    with m.Else():
                        m.next = "LAST"
                with m.State("LAST"):
                    sh.mov(m, "isr", "y")
                    m.next = "PULL"
        both_ways(data_design(body, push_thresh=20, pull_thresh=12, status="tx", status_n=2), cycles=120, tx=[0xDEADBEEF, 0x12345678], tx_feed=[(50, 0xCAFEF00D)])
        both_ways(data_design(body, autopull=True, pull_thresh=12, autopush=True, push_thresh=20, status="rx", status_n=1), cycles=120, tx=[0xDEADBEEF, 0x12345678], drain=False)


@support.cases
class IrqTests(support.TestCase):
    @needs_tools
    def test_irq_wait_sets_the_flag_and_stalls_until_the_host_clears_it(self):
        def body(m, sh, pl, io):
            with m.FSM():
                with m.State("UP"):
                    m.d.sync += io["out"].o.eq(1)
                    m.next = "WAIT"
                with m.State("WAIT"):
                    with sh.irq(m, 5, "wait"):
                        m.next = "DOWN"
                with m.State("DOWN"):
                    m.d.sync += io["out"].o.eq(0)
                    m.next = "UP"
        c, a, b = both_ways(data_design(body), cycles=40, irq_clear=[(15, 5), (30, 5)])
        self.assertIn("irq wait 5", c.program.pio_text())
        # flag 5 is raised by the first cycle of the irq (visible from cycle 2 on) and stays up until the host lowers it at the start of cycle 15
        self.assertEqual(a["irq"][0], 0)
        self.assertTrue(all(v == 0x20 for v in a["irq"][2:15]))
        self.assertEqual(a["waves"][0][:17], "1" * 16 + "0")          # the instruction completes in cycle 15, the next state lowers the pin in cycle 16
        self.assertEqual(a["irq"], b["irq"])

    @needs_tools
    def test_wait_irq_one_clears_the_flag_and_wait_irq_zero_does_not(self):
        def body(m, sh, pl, io):
            with m.FSM():
                with m.State("ONE"):
                    with sh.wait_irq(m, 2, 1):
                        m.next = "ZERO"
                with m.State("ZERO"):
                    with sh.wait_irq(m, 3, 0):
                        m.next = "PIN"
                with m.State("PIN"):
                    m.d.sync += io["out"].o.eq(1)
                    m.next = "ONE2"
                with m.State("ONE2"):
                    with sh.wait_irq(m, 2, 1):
                        m.next = "PIN0"
                with m.State("PIN0"):
                    m.d.sync += io["out"].o.eq(0)
                    m.next = "ONE"
        c, a, b = both_ways(data_design(body), cycles=40, irq_set=[(4, 2), (4, 3), (20, 2), (22, 3)], irq_clear=[(8, 3), (25, 3)])
        text = c.program.pio_text()
        self.assertTrue("wait 1 irq 2" in text and "wait 0 irq 3" in text)
        self.assertEqual(a["irq"], b["irq"])

    @needs_tools
    def test_a_flag_set_and_cleared_by_the_program_in_one_cycle_ends_up_set(self):
        # the host clears flag 1 in the cycle the program sets it: the clear comes first (start of the cycle), the set at its end
        def body(m, sh, pl, io):
            with m.FSM():
                with m.State("SET"):
                    sh.irq(m, 1)
                    m.next = "NOP"
                with m.State("NOP"):
                    m.next = "CLEAR"
                with m.State("CLEAR"):
                    sh.irq(m, 1, "clear")
                    m.next = "SET"
        both_ways(data_design(body), cycles=30, irq_clear=[(0, 1), (3, 1), (7, 1), (9, 1)], irq_set=[(3, 1), (8, 1)])

    @needs_tools
    def test_mov_status_from_the_irq_flags_on_pio_version_1_and_the_rel_form(self):
        def body(m, sh, pl, io):
            with m.FSM():
                with m.State("RAISE"):
                    sh.irq(m, 6, "set", rel=True)
                    m.next = "STATUS"
                with m.State("STATUS"):
                    sh.mov(m, "x", "status")
                    m.next = "SHOW"
                with m.State("SHOW"):
                    sh.mov(m, io["out"], "x")
                    m.next = "DROP"
                with m.State("DROP"):
                    sh.irq(m, 6, "clear", rel=True)
                    m.next = "RAISE"
        c, a, b = both_ways(data_design(body, chip="rp2350", status="irq", status_n=6), cycles=30, irq_clear=[(7, 6)])
        self.assertTrue("irq set 6 rel" in c.program.pio_text() and c.sim_config()["status_sel"] == "irq" and c.irq_rel)
        self.assertEqual(a["waves"][0][:4], "0011")           # mov x, status sees flag 6 (raised in the cycle before) and mov pins, x shows its bit 0


@support.cases
class SimConfigTests(support.TestCase):
    @support.parametrize("name", sorted(pio_designs.PROGRAMS))
    def test_sim_config_with_the_chip_version_runs_in_the_emulator(self, name):
        # COR-13: the docstring of Compiled.sim_config promised keyword arguments of run_case; they need the PIO version of the chip (gpio_base 16 needs version 1)
        from pioweave.builder import CHIP_VERSION
        from pioweave.emulator import run_case
        cls, chip, stim, cycles, opts = pio_designs.entry(name)
        c = compile_factory(lambda: (cls(), PLATFORMS[chip]()), name)
        run_case(c.program, 5, version=CHIP_VERSION[c.chip], on_error="stop", **c.sim_config())
        run_case(c.program, 5, on_error="stop", **c.sim_config())      # the version is taken from the program
