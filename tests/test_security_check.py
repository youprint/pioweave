# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""Regression tests of the security audit, the emulator (S-09, S-10, S-16): emulator.check on malformed or oversized input, the range checks of the emulator API."""
import os
import subprocess
import sys

from . import support
from .support_security import ROOT, cli, run_py

def _programs():
    return {"programs": [{"name": "w", "pioVersion": 0, "wrapTarget": 0, "wrap": 0, "instructions": [{"hex": "e03f"}]}]}

def _check(tmp, prog, case_text, *opts):
    import json
    pj, cj = os.path.join(str(tmp), "p.json"), os.path.join(str(tmp), "c.json")
    with open(pj, "w") as fh:
        fh.write(prog if isinstance(prog, str) else json.dumps(prog))
    with open(cj, "w") as fh:
        fh.write(case_text)
    return run_py("-m", "pioweave.emulator.check", *opts, pj, cj)

@support.cases
class CheckRobustnessTests(support.TestCase):
    """S-09, S-10: emulator.check exits 0 or 2, never 1."""

    @support.parametrize("case", ['{"cycles": [1]}', '{"cycles": 1e400}', '{"cycles": Infinity}', '{"cycles": -5}', '{"cycles": 2000001}', '{"cycles": {"a": 1}}', '[1, 2]'], ids=[
        "list", "overflow", "infinity", "negative", "too_many", "dict", "not_object"])
    def test_bad_cycles_are_usage_errors(self, case):
        r = _check(self.tmp_path, _programs(), case)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(r.stderr.startswith("pioweave.emulator.check:"), r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    @support.parametrize("case", ['{"cycles": 4, "expect": {"0": 5}}', '{"cycles": 4, "expect": [1]}', '{"cycles": 4, "tx": [1e400]}', '{"cycles": 4, "expect": 5}',
                                  '{"cycles": 4, "expect_dirs": 7}'], ids=["expect_int", "expect_list", "tx_inf", "expect_scalar", "dirs_scalar"])
    def test_wrong_shaped_fields_are_errors_as_data(self, case):
        import json
        r = _check(self.tmp_path, _programs(), case)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertFalse(out["ok"])
        self.assertTrue(out["error"])

    def test_program_list_of_non_objects(self):
        r = _check(self.tmp_path, '{"programs": [5]}', '{"cycles": 3, "program": "x"}')
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = _check(self.tmp_path, '{"programs": [5]}', '{"cycles": 3}')
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_deeply_nested_json(self):
        deep = "[" * 200000 + "]" * 200000
        for which in (0, 1):
            r = _check(self.tmp_path, deep if which == 0 else _programs(), deep if which == 1 else '{"cycles": 3}')
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_oversized_input(self):
        r = _check(self.tmp_path, _programs(), '{"cycles": 3, "pad": "%s"}' % ("x" * 200), "--max-bytes", "100")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--max-bytes", r.stderr)

    def test_a_device_file_is_bounded(self):
        r = run_py("-m", "pioweave.emulator.check", "--max-bytes", "1000", "/dev/zero", "/dev/zero")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_max_cycles_override(self):
        r = _check(self.tmp_path, _programs(), '{"cycles": 1000001}', "--max-cycles", "1000001")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _check(self.tmp_path, _programs(), '{"cycles": 11}', "--max-cycles=10")
        self.assertEqual(r.returncode, 2)

    @support.parametrize("opts", [["--max-cycles"], ["--max-cycles", "x"], ["--max-bytes", "0"], ["--bogus"]])
    def test_bad_options(self, opts):
        r = _check(self.tmp_path, _programs(), '{"cycles": 3}', *opts)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

@support.cases
class EmulatorApiTests(support.TestCase):
    """S-16: the emulator API refuses out-of-range values at the call, not as a crash cycles later."""

    @support.parametrize("kw", [{"in_count": -1}, {"set_base": -1}, {"set_base": 10 ** 9}, {"side_base": 10 ** 9}, {"status_n": -5}, {"out_count": 33}, {"set_count": 6}, {"jmp_pin": 32},
                                {"in_base": 1.5}])
    def test_configure(self, kw):
        from pioweave.emulator import PioBlock
        block = PioBlock()
        loaded = block.load([0xE000])
        with self.assertRaises(ValueError):
            block.sm[0].configure(program=loaded, **kw)

    def test_sync_cycles(self):
        from pioweave.emulator import PioBlock
        for bad in (-3, 65, 1.5, True):
            with self.assertRaises(ValueError):
                PioBlock(sync_cycles=bad)

    def test_set_input(self):
        from pioweave.emulator import PioBlock
        for bad in (-1, 32, 99):
            with self.assertRaises(ValueError):
                PioBlock().set_input(bad, 1)
        PioBlock().set_input(31, 1)

    @support.parametrize("words", [[200000], [-1], ["x"], [1.5]])
    def test_load_words(self, words):
        from pioweave.emulator import PioBlock
        with self.assertRaises(ValueError):
            PioBlock().load(words)

    def test_load_offset(self):
        from pioweave.emulator import PioBlock
        from pioweave.emulator import EmulationError
        for bad in (-1, 1.5):
            with self.assertRaises(ValueError):
                PioBlock().load([0xE000], offset=bad)
        with self.assertRaises(EmulationError):
            PioBlock().load([0xE000], offset=40)

    def test_run_case(self):
        from pioweave.emulator import run_case
        for kw in ({"cycles": -5}, {"cycles": 3, "stim": {0: ""}}, {"cycles": 3, "stim": {0: "2"}}, {"cycles": 3, "stim": {40: "1"}}):
            with self.assertRaises(ValueError):
                run_case([0xE000], **kw)

    def test_valid_runs_still_work(self):
        from pioweave.emulator import run_case
        r = run_case([0xE101, 0xE100], 4, set_base=0, set_count=1, dirs=1, stim={3: "01"})
        self.assertEqual(r.wave(0), "1100")


if __name__ == "__main__":
    import unittest
    unittest.main()
