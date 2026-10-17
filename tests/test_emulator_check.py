"""`python -m pioweave.emulator.check PROGRAM_JSON CASE_JSON`, the second opinion bitweave asks for on a PIO simulation case: directed tests of the result and the exit status, and a
differential test that runs random programs and cases through bitweave's own `pio` flow and through the check and compares the results. The differential test needs the bitweave
binary and pioasm (BITWEAVE, PIOASM); PIO_EMU_N sets the number of cases (default 40). Nothing here has run on a chip."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

from pioweave.builder import CLEAR, PINS, X, Program
from pioweave.builder.program import CHIP_VERSION
from pioweave.emulator import check

from . import cosim, support
from .emu_random import FAMILIES, random_case
from .test_emulator_differential import N, needs_tools, simulator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIOASM = cosim.find_pioasm()


def programs_json(*programs):
    """The object `pioasm -o json` would write for the programs (built from the builder)."""
    out = []
    for p in programs:
        first, last = p.wrap_range
        out.append({"name": p.name, "pioVersion": CHIP_VERSION[p.chip], "wrapTarget": first, "wrap": last, "origin": -1 if getattr(p, "origin", None) is None else p.origin,
                    "sideset": {"size": p.side_count + (1 if p.side_opt and p.side_count else 0), "optional": bool(p.side_opt), "pindirs": bool(p.side_pindirs)},
                    "instructions": [{"hex": "%04x" % (w & 0xFFFF)} for w in p.words()], "usedGPIORanges": 1})
    return {"programs": out}


def square(name="square"):
    p = Program(name)
    with p.wrapped():
        p.set(PINS, 1, delay=1)
        p.set(PINS, 0, delay=1)
    return p


def run_cli(programs, case, tmp):
    """Run the module as bitweave does; returns (exit status, stdout, stderr)."""
    pj, cj = os.path.join(tmp, "p.json"), os.path.join(tmp, "c.json")
    with open(pj, "w") as fh:
        json.dump(programs, fh)
    with open(cj, "w") as fh:
        json.dump(case, fh)
    env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-m", "pioweave.emulator.check", pj, cj], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout, r.stderr


@support.cases
class CheckTests(support.TestCase):
    def test_a_square_wave_through_the_command_line(self):
        status, out, err = run_cli(programs_json(square()), {"name": "w", "cycles": 8, "set_base": 0, "set_count": 1, "dirs": 1, "expect": {"0": "11001100"}}, str(self.tmp_path))
        self.assertEqual((status, err), (0, ""))
        self.assertEqual(len(out.strip().splitlines()), 1)                      # ONE object
        r = json.loads(out)
        self.assertEqual((r["ok"], r["error"], r["error_cycle"], r["cycles"]), (True, None, None, 8))
        self.assertEqual((r["pads"], r["dirs"]), ({"0": "11001100"}, {"0": "11111111"}))
        self.assertEqual((r["rx"], r["rx_left"], r["tx_left"], r["unsupported"], r["mismatches"]), ([], [], 0, [], []))
        self.assertEqual(r["final"], {"x": 0, "y": 0, "pc": 0, "isr": 0, "osr": 0, "irq_flags": 0})

    def test_a_failed_expectation_is_reported_and_is_not_an_error(self):
        r = check.check_case(programs_json(square()), {"cycles": 4, "set_count": 1, "dirs": 1, "expect": {"0": "1111"}, "expect_x": 5, "expect_dirs": {"0": "1.0."}})
        self.assertEqual((r["ok"], r["error"]), (False, None))
        self.assertIn("pin 0 cycle 2: expected 1 got 0", r["mismatches"])
        self.assertIn("x: expected 5 got 0", r["mismatches"])
        self.assertIn("dir 0 cycle 2: expected 0 got 1", r["mismatches"])

    def test_set_count_defaults_to_zero_as_in_the_sdk_configuration_of_a_case(self):
        # a case describes a state machine configured by the SDK (pio_sm_init writes PINCTRL from a default configuration whose SET_COUNT is 0); the register reset value
        # of 5 (RP2040 Table 386 p.378) is visible only when no configuration is written, which is what `set_count = 5` in the case says
        p = Program("w")
        p.set(PINS, 31)
        r = check.check_case(programs_json(p), {"cycles": 1, "dirs": 0xFFFFFFFF, "expect": {"0": "x"}})
        self.assertEqual(r["pads"]["0"], "0")
        r = check.check_case(programs_json(p), {"cycles": 1, "dirs": 0xFFFFFFFF, "set_count": 5, "expect": {"4": "1", "5": "x"}})
        self.assertEqual((r["ok"], r["pads"]["4"], r["pads"]["5"]), (True, "1", "0"))

    def test_unknown_keys_are_reported_as_unsupported_and_null_means_absent(self):
        r = check.check_case(programs_json(square()), {"cycles": 2, "future_key": 3, "another": "x", "x": None, "set_count": None, "name": "n", "trace": True})
        self.assertEqual(r["unsupported"], ["another", "future_key"])
        self.assertTrue(r["ok"])

    def test_every_key_of_bitweaves_case_is_known(self):
        path = os.path.join(os.environ.get("BITWEAVE_REPO", ""), "src", "config.rs")
        if not os.path.exists(path):
            self.skipTest("BITWEAVE_REPO (a bitweave checkout) not set")
        with open(path) as fh:
            text = fh.read()
        body = text.split("pub struct PioSim {", 1)[1].split("\n}\n", 1)[0]
        keys = set(re.findall(r"^\s+pub (\w+):", body, re.M))
        self.assertLessEqual(keys, check.SIMULATION_KEYS | check.EXPECTATION_KEYS)

    def test_a_program_that_does_something_undefined_is_data_and_exits_zero(self):
        q = Program("undefined", chip="rp2350")
        q.nop()
        words = programs_json(q)
        words["programs"][0]["instructions"] = [{"hex": "e021"}, {"hex": "a044"}]      # set x, 1 ; mov y, <reserved source 100>
        words["programs"][0]["wrap"] = 1
        status, out, err = run_cli(words, {"cycles": 5}, str(self.tmp_path))
        self.assertEqual(status, 0)
        r = json.loads(out)
        self.assertEqual((r["ok"], r["error_cycle"]), (False, 1))
        self.assertRegex(r["error"], r"^cycle 1: ")
        self.assertEqual(r["final"]["x"], 1)

    def test_configuration_errors_have_no_cycle(self):
        for case, text in (({"cycles": 2, "set_count": 6}, "set_count"), ({"cycles": 2, "in_count": 0}, "in_count"), ({"cycles": 2, "fifo_join": "rx_put"}, "version 1"),
                           ({"cycles": 2, "in_shift": "up"}, "left"), ({"cycles": 2, "gpio_base": 16}, "gpio_base"), ({"cycles": 2, "pins_in": {"3": ""}}, "pins_in"),
                           ({"cycles": 2, "pins_in": {"40": "1"}}, "pin"), ({"cycles": 2, "expect": {"0": "2"}}, "expect")):
            r = check.check_case(programs_json(square()), case)
            self.assertFalse(r["ok"], case)
            self.assertIsNone(r["error_cycle"], case)
            self.assertIn(text, r["error"], case)

    def test_usage_errors_exit_with_two(self):
        tmp = str(self.tmp_path)
        env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))

        def run(*args):
            return subprocess.run([sys.executable, "-m", "pioweave.emulator.check"] + list(args), capture_output=True, text=True, env=env)

        r = run()
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn("usage", r.stderr)
        self.assertEqual(run(os.path.join(tmp, "missing.json"), os.path.join(tmp, "missing2.json")).returncode, 2)
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w") as fh:
            fh.write("{")
        self.assertEqual(run(bad, bad).returncode, 2)
        good = os.path.join(tmp, "good.json")
        with open(good, "w") as fh:
            json.dump(programs_json(square()), fh)
        nocycles = os.path.join(tmp, "nocycles.json")
        with open(nocycles, "w") as fh:
            json.dump({"name": "x"}, fh)
        r = run(good, nocycles)
        self.assertEqual(r.returncode, 2)
        self.assertIn("cycles", r.stderr)
        named = os.path.join(tmp, "named.json")
        with open(named, "w") as fh:
            json.dump({"cycles": 1, "program": "nope"}, fh)
        self.assertEqual(run(good, named).returncode, 2)
        empty = os.path.join(tmp, "empty.json")
        with open(empty, "w") as fh:
            json.dump({"programs": []}, fh)
        self.assertEqual(run(empty, named).returncode, 2)

    def test_the_program_of_the_case_is_selected_by_name(self):
        other = Program("other")
        other.set(PINS, 0)
        both = programs_json(other, square("second"))
        r = check.check_case(both, {"cycles": 4, "program": "second", "set_count": 1, "dirs": 1})
        self.assertEqual(r["pads"]["0"], "1100")
        r = check.check_case(both, {"cycles": 2, "set_count": 1, "dirs": 1})                # the first program
        self.assertEqual(r["pads"]["0"], "00")

    def test_stimulus_fifo_traffic_and_irq_events(self):
        p = Program("echo")
        p.pull()
        p.out(X, 8)
        p.in_(X, 8)
        p.push()
        p.wait_irq(1, 2)
        p.irq(3, mode=CLEAR)
        r = check.check_case(programs_json(p), {"cycles": 12, "tx": [0x1AB], "tx_feed": [{"cycle": 7, "value": 9}], "irq_set": [{"cycle": 8, "flag": 2}, {"cycle": 20, "flag": 1}],
                                                "irq_clear": [{"cycle": 8, "flag": 2}], "expect_rx": [0xAB000000], "expect_irq": 0, "rx_drain": False})
        self.assertEqual((r["ok"], r["rx"], r["rx_left"], r["tx_left"]), (True, [], [0xAB000000], 0))
        r = check.check_case(programs_json(p), {"cycles": 12, "tx": [0x1AB], "irq_set": [{"cycle": 8, "flag": 2}]})
        self.assertEqual(r["rx"], [{"cycle": 3, "value": 0xAB000000}])
        self.assertEqual(r["final"]["irq_flags"], 0)

    def test_a_program_runs_at_its_origin_and_must_fit_there(self):
        p = Program("w")
        p.set(X, 3)
        data = programs_json(p)
        data["programs"][0]["origin"] = 4
        r = check.check_case(data, {"cycles": 1})
        self.assertEqual((r["final"]["x"], r["final"]["pc"]), (3, 0))                    # pc is relative to the program
        data["programs"][0]["origin"] = 30
        data["programs"][0]["instructions"] = [{"hex": "e021"}, {"hex": "e022"}, {"hex": "e023"}]
        r = check.check_case(data, {"cycles": 1})
        self.assertIn("does not fit the 32-word instruction memory at offset 30", r["error"])


def ids(values):
    return [str(v) for v in values]


@support.cases
class CheckDifferentialTests(support.TestCase):
    """bitweave's `pio` flow and the check on the same random program and case must agree on ok / error, the pad and direction waveforms of the pins in question, the words
    received with their cycles, the FIFO counts and the final registers."""

    def compare(self, seed, family=None):
        program, cycles, cfg, stim, tx, tx_feed, drain = random_case(seed, family)
        sim = simulator(program, cycles, cfg, stim, tx, tx_feed, drain)
        case = dict(cfg)
        case["cycles"] = cycles
        case["tx"] = list(tx)
        case["tx_feed"] = [{"cycle": c, "value": w} for c, w in tx_feed]
        case["rx_drain"] = drain
        case["pins_in"] = {str(pin): w for pin, w in stim.items()}
        with tempfile.TemporaryDirectory(prefix="emu_check_") as d:
            pj = os.path.join(d, "p.json")
            src = os.path.join(d, "p.pio")
            with open(src, "w") as fh:
                fh.write(program.pio_text())
            subprocess.run([PIOASM, "-v", str(CHIP_VERSION[program.chip]), "-o", "json", src, pj], check=True, capture_output=True)
            with open(pj) as fh:
                programs = json.load(fh)
            status, out, err = run_cli(programs, case, d)
        why = "seed %s family %s\n%s\n%s" % (seed, family, program.pio_text(), case)
        self.assertEqual((status, err), (0, ""), why)
        res = json.loads(out)
        self.assertEqual(res["unsupported"], [], why)
        self.assertEqual(bool(sim["error"]), res["error"] is not None, why)
        self.assertEqual(sim["ok"], res["ok"], why)
        trace = sim["trace"] or []
        if sim["error"]:
            m = re.match(r"cycle (\d+):", sim["error"])
            self.assertEqual(int(m.group(1)) if m else None, res["error_cycle"], why)
        for pin, wave in sim["waves"].items():
            self.assertEqual(res["pads"][pin], wave, "pin %s\n%s" % (pin, why))
            if trace:
                self.assertEqual(res["dirs"][pin], "".join(str((row["dirs"] >> int(pin)) & 1) for row in trace), "dir %s\n%s" % (pin, why))
        if not sim["error"]:
            self.assertEqual(res["rx"], sim["rx"], why)
            self.assertEqual((res["rx_left"], res["tx_left"]), (sim["rx_left"], sim["tx_left"]), why)
            self.assertEqual(res["final"], sim["final"], why)

    @needs_tools
    @support.parametrize("seed", range(N))
    def test_check_matches_the_pio_flow(self, seed):
        self.compare(seed)

    @needs_tools
    @support.parametrize("family", list(FAMILIES))
    @support.parametrize("seed", range(max(N // 4, 4)))
    def test_check_matches_the_pio_flow_on_a_family(self, family, seed):
        self.compare(seed, family)


if __name__ == "__main__":
    unittest.main()
