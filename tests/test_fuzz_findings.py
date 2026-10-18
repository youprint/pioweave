# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""Regression tests of the fuzzing round of 2026-10-18 (findings F1 to F5): a null cycle count, a pin the chip does not have, the size of a VCD run, a failed write_pio that emptied the file, wrong types in run_case."""
import os

from . import support
from .support_security import cli

PROGRAMS = [{"name": "p", "instructions": [{"hex": "e101"}, {"hex": "e100"}]}]


@support.cases
class FuzzFindingTests(support.TestCase):
    def test_f1_a_null_cycles_is_a_value_error_not_a_key_error(self):
        from pioweave.emulator.check import check_case
        for case in ({"cycles": None}, {"cycles": None, "dirs": 1}, None, [], "x"):
            with self.assertRaisesRegex(ValueError, "`cycles`"):
                check_case(PROGRAMS, case)

    def test_f2_a_pin_the_chip_does_not_have_is_a_clean_error(self):
        for argv in (("build", "high_pins", str(self.tmp_path / "o"), "--chip", "rp2354"), ("emulate", "high_pins", "--chip", "rp2040")):
            code, _, err = cli(*argv)
            self.assertEqual(code, 2, err)
            self.assertTrue(any(line.startswith("error:") for line in err.splitlines()), err)
            self.assertNotIn("Traceback", err)

    def test_f3_vcd_has_a_ceiling_and_the_run_is_done_once(self):
        from pioweave.__main__ import VCD_MAX_CYCLES
        path = str(self.tmp_path / "w.vcd")
        code, _, err = cli("emulate", "square_wave", "--cycles", str(VCD_MAX_CYCLES + 1), "--vcd", path)
        self.assertEqual(code, 2, err)
        self.assertIn("--vcd", err)
        self.assertFalse(os.path.exists(path))
        code, with_vcd, err = cli("emulate", "square_wave", "--cycles", "64", "--vcd", path)
        self.assertEqual(code, 0, err)
        code, without, err = cli("emulate", "square_wave", "--cycles", "64")
        self.assertEqual(code, 0, err)
        self.assertEqual(with_vcd.replace("wrote %s\n" % path, ""), without)
        self.assertGreater(os.path.getsize(path), 100)

    def test_f4_a_failed_write_pio_leaves_the_file_alone(self):
        from pioweave.builder import PioError, Program, write_pio
        path = str(self.tmp_path / "keep.pio")
        with open(path, "w") as fh:
            fh.write("good\n")
        with self.assertRaises(PioError):
            write_pio([Program("empty")], path)
        with open(path) as fh:
            self.assertEqual(fh.read(), "good\n")

    @support.parametrize("config", [{"start_pc": 1.5}, {"x": "a"}, {"y": None}, {"dirs": "none"}, {"clkdiv": "x"}, {"push_thresh": "rx"}, {"tx": ["a"]}, {"on_error": "x"}],
                         ids=["start_pc", "x", "y", "dirs", "clkdiv", "push_thresh", "tx", "on_error"])
    def test_f5_wrong_types_in_run_case_are_value_errors(self, config):
        from pioweave.builder import PINS, Program
        from pioweave.emulator import run_case
        p = Program("p", chip="rp2040")
        p.set(PINS, 1)
        with self.assertRaises(ValueError):
            run_case(p, 4, **config)

    def test_f5_a_short_side_tuple_for_a_word_list_is_a_value_error(self):
        from pioweave.emulator import run_case
        with self.assertRaises(ValueError):
            run_case([0xE001], 4, side=(1,))
