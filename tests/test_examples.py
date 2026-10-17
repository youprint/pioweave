# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""The runnable examples in examples/ run, so that they cannot rot: each script is executed in a fresh Python process with the current directory and the output directory
in a temporary directory; the unittest example is run with `python -m unittest`."""
import os
import subprocess
import sys

from . import support

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXAMPLES = os.path.join(ROOT, "examples")

SCRIPTS = [
    ("blink", "blink.py", ["--board", "pico"], ["blink.pio", "bitweave.toml"]),
    ("blink", "blink.py", ["--board", "pico2"], ["blink.pio"]),
    ("blink", "blink.py", ["--board", "weact"], ["blink.pio"]),
    ("blink", "blink.py", ["--board", "waveshare"], ["blink.pio"]),
    ("uart", "uart.py", [], ["tx/uart_tx.pio", "rx/uart_rx.pio"]),
    ("ws2812", "ws2812.py", [], ["ws2812.pio"]),
    ("block", "block.py", [], ["uart_baud.pio", "main.c", "bitweave.toml"]),
    ("square", "square.py", [], ["square.pio"]),
    ("square", "square.py", ["--high", "20", "--low", "12", "--chip", "rp2350"], ["square.pio"]),
]


def run(args, cwd, **kw):
    env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""), PYTHONWARNINGS="error")
    return subprocess.run([sys.executable] + args, cwd=cwd, env=env, capture_output=True, text=True, timeout=300, **kw)


@support.cases
class ExampleTests(support.TestCase):
    @support.parametrize("folder,script,args,files", SCRIPTS, ids=["%s_%s" % (s[0], "_".join(a for a in s[2] if not a.startswith("-")) or "default") for s in SCRIPTS])
    def test_script_runs_and_writes_its_files(self, folder, script, args, files):
        out = self.tmp_path / "out"
        r = run([os.path.join(EXAMPLES, folder, script), "--out", str(out)] + args, cwd=str(self.tmp_path))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for f in files:
            self.assertTrue((out / f).is_file(), "%s not written\n%s" % (f, r.stdout))

    def test_unittest_example(self):
        r = run(["-m", "unittest", "examples/emulator_test/test_pulse.py"], cwd=ROOT)      # unittest takes a relative file path, from the repository root
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stderr)

    def test_unittest_example_from_its_own_directory(self):
        r = run(["-m", "unittest"], cwd=os.path.join(EXAMPLES, "emulator_test"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_every_example_has_a_readme(self):
        for name in sorted(os.listdir(EXAMPLES)):
            if os.path.isdir(os.path.join(EXAMPLES, name)) and not name.startswith("__"):
                self.assertTrue(os.path.isfile(os.path.join(EXAMPLES, name, "README.rst")), name)
