# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""`pioweave.clock`: the quantisation of the divider, the refusals, the command line, and agreement with the emulator's fractional divider."""
import contextlib
import io

from pioweave.__main__ import main
from pioweave.builder import PioError
from pioweave.clock import DEFAULT_SYS_HZ, ClkDiv, pio_bitrate_div, pio_clkdiv
from pioweave.emulator.core import StateMachine

from . import support


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def enables(clkdiv, cycles):
    """The system clock cycles in which the emulator's divider lets a state machine advance."""
    sm = StateMachine.__new__(StateMachine)
    sm.cfg = type("Cfg", (), {"clkdiv": clkdiv})()
    sm.cd_left, sm.cd_total = 0, 0
    return [sm._clock_enable() for _ in range(cycles)]


@support.cases
class ClockTests(support.TestCase):
    def test_exact_integer(self):
        c = pio_clkdiv(125_000_000, 4_000)
        self.assertEqual((c.int_part, c.frac, c.divider, c.sm_hz, c.error_ppm, c.period_jitter_cycles), (31250, 0, 31250.0, 4000.0, 0.0, 0))
        self.assertEqual(c.notes, ())
        self.assertIsInstance(c, ClkDiv)

    def test_fraction(self):
        c = pio_clkdiv(125_000_000, 125_000_000 / 2.5)
        self.assertEqual((c.int_part, c.frac, c.divider, c.period_jitter_cycles), (2, 128, 2.5, 1))
        self.assertIn("fractional", c.notes[0])

    def test_rounds_to_nearest_256th(self):
        c = pio_clkdiv(1_000_000, 1_000_000 / 3.1)           # 3.1 * 256 = 793.6 -> 794
        self.assertEqual((c.int_part, c.frac), (3, 26))
        self.assertAlmostEqual(c.divider, 794 / 256)
        self.assertAlmostEqual(c.error_ppm, (1 / c.divider * 3.1 - 1) * 1e6, places=3)
        self.assertLess(c.error_ppm, 0)

    def test_divider_one(self):
        c = pio_clkdiv(125_000_000, 125_000_000)
        self.assertEqual((c.int_part, c.frac, c.divider), (1, 0, 1.0))
        self.assertIn("divider 1", c.notes[0])

    def test_divider_65536_is_int_zero(self):
        c = pio_clkdiv(65536, 1)
        self.assertEqual((c.int_part, c.frac, c.divider, c.sm_hz), (0, 0, 65536.0, 1.0))
        self.assertIn("INT is written as 0", c.notes[0])

    def test_rounding_up_to_65536_is_allowed(self):
        self.assertEqual(pio_clkdiv(65536.001, 1).int_part, 0)

    def test_chip_defaults(self):
        self.assertEqual(pio_clkdiv(None, 1e6).sm_hz, 1e6)         # 125 MHz / 125
        self.assertEqual(pio_clkdiv(None, 1e6, chip="rp2350").divider, 150.0)
        for chip, hz in DEFAULT_SYS_HZ.items():
            self.assertEqual(pio_clkdiv(None, hz, chip=chip).divider, 1.0)

    def test_max_error(self):
        pio_clkdiv(125_000_000, 125_000_000 / 3.1, max_error_ppm=1000)
        with self.assertRaisesRegex(PioError, "above the limit"):
            pio_clkdiv(125_000_000, 125_000_000 / 3.1, max_error_ppm=10)
        pio_clkdiv(125_000_000, 4_000, max_error_ppm=0)

    def test_bitrate(self):
        c = pio_bitrate_div(125_000_000, 115_200, 8)
        self.assertEqual(c.sm_hz, 125_000_000 / c.divider)
        self.assertEqual((c.int_part, c.frac), (135, 162))      # 125e6 / 921600 = 135.6337 -> 34722 / 256 = 135 + 162 / 256
        self.assertEqual(pio_bitrate_div(None, 1000, 8, chip="rp2350").sm_hz, 8000.0)
        with self.assertRaisesRegex(PioError, "above the limit"):
            pio_bitrate_div(125_000_000, 115_200, 8, max_error_ppm=1)

    @support.parametrize("args, message", [
        ((125e6, 0), "target clock"), ((125e6, -1), "target clock"), ((125e6, float("nan")), "target clock"), ((125e6, True), "target clock"),
        ((125e6, "4000"), "target clock"), ((0, 1000), "system clock"), ((float("inf"), 1000), "system clock"),
        ((125e6, 126e6), "below 1"), ((125e6, 1900), "above 65536")])
    def test_refused(self, args, message):
        with self.assertRaisesRegex(PioError, message):
            pio_clkdiv(*args)

    def test_refused_options(self):
        with self.assertRaisesRegex(PioError, "unknown chip"):
            pio_clkdiv(125e6, 1000, chip="rp2040x")
        for bad in (-1, True, "1", float("nan")):
            with self.assertRaisesRegex(PioError, "max_error_ppm"):
                pio_clkdiv(125e6, 4000, max_error_ppm=bad)
        with self.assertRaisesRegex(PioError, "bit rate"):
            pio_bitrate_div(125e6, 0, 8)
        with self.assertRaisesRegex(PioError, "cycles per bit"):
            pio_bitrate_div(125e6, 9600, 0)

    @support.parametrize("divider", [1.0, 2.0, 2.5, 3.1, 3.5, 7.25, 10.333, 31.99, 100.004, 255.5])
    def test_agrees_with_emulator(self, divider):
        sys_hz = 100_000_000
        c = pio_clkdiv(sys_hz, sys_hz / divider)
        self.assertEqual(c.int_part * 256 + c.frac, int(round(divider * 256)))
        n = 256 * 200
        # the emulator, given the helper's INT and FRAC, advances 1 / divider of the cycles on average (n is a whole number of 256-cycle periods of the fraction)
        got = enables(c.int_part + c.frac / 256, n)
        self.assertAlmostEqual(sum(got) / n, 1 / c.divider, delta=2.0 / n)
        # periods are int_part or int_part + 1 cycles: the jitter the helper reports
        hits = [i for i, e in enumerate(got) if e]
        gaps = [b - a for a, b in zip(hits, hits[1:])]
        self.assertEqual(set(gaps) <= {c.int_part, c.int_part + 1}, True)
        self.assertEqual(max(gaps) - min(gaps), c.period_jitter_cycles)

    def test_cli_target(self):
        code, out, err = cli("clock", "--sys", "125000000", "--target", "4000")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("CLKDIV INT        31250", out)
        self.assertIn("CLKDIV FRAC       0", out)

    def test_cli_bitrate_and_chip(self):
        code, out, _ = cli("clock", "--chip", "rp2350", "--bitrate", "115200", "--cycles-per-bit", "8", "--max-error-ppm", "5000")
        self.assertEqual(code, 0)
        self.assertIn("150000000 Hz", out)
        self.assertIn("note: fractional divider", out)
        self.assertIn("jitter            1 system clock cycles", out)

    def test_cli_errors(self):
        for argv, text in [(("clock",), "give --target"), (("clock", "--bitrate", "9600"), "give --target"), (("clock", "--target", "1", "--bitrate", "2"), "not both"),
                           (("clock", "--target", "0"), "target clock"), (("clock", "--target", "1000", "--chip", "x"), "unknown chip")]:
            code, out, err = cli(*argv)
            self.assertEqual((code, out), (2, ""))
            self.assertIn(text, err)
