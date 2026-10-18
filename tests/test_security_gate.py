# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""Regression tests of the security audit, the version gate and pathological designs (S-11 to S-14): the gate at import, the override warning, scripts/matrix.sh, designs the compiler must refuse with PioCompileError."""
import os
import subprocess
import sys

from . import support
from .support_security import ROOT, cli, run_py

_FAKE_META = ("import importlib.metadata as m\nreal = m.version\nm.version = lambda n, _r=real: '%s' if n == 'amaranth' else _r(n)\n")

class VersionGateTests(support.TestCase):
    """S-11, S-13: `import pioweave` runs the gate; the override warns; the builder stays free of Amaranth."""

    def test_import_pioweave_refuses_an_untested_version(self):
        r = run_py("-c", _FAKE_META % "0.4.0" + "import pioweave")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("UnsupportedAmaranth", r.stderr)
        self.assertIn("found 0.4.0", r.stderr)

    def test_import_builder_refuses_too_but_does_not_import_amaranth(self):
        r = run_py("-c", _FAKE_META % "0.4.0" + "import pioweave.builder")
        self.assertIn("UnsupportedAmaranth", r.stderr)
        r = run_py("-c", "import sys, pioweave.builder, pioweave.emulator; print('amaranth' in sys.modules)")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "False"), r.stderr)

    def test_a_tested_version_imports(self):
        r = run_py("-c", _FAKE_META % "0.5.7" + "import pioweave")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_override_warns(self):
        r = run_py("-W", "error", "-c", _FAKE_META % "0.4.0" + "import pioweave", env={"PIOWEAVE_ALLOW_UNTESTED": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("UntestedAmaranthWarning", r.stderr)
        r = run_py("-c", _FAKE_META % "0.4.0" + "import pioweave", env={"PIOWEAVE_ALLOW_UNTESTED": "1", "PYTHONWARNINGS": ""})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not tested with Amaranth 0.4.0", r.stderr)

    def test_the_gate_checks_amaranth_version_too(self):
        r = run_py("-c", "import amaranth; amaranth.__version__ = '0.4.0'\nimport pioweave\nimport pioweave.compile")
        self.assertIn("UnsupportedAmaranth", r.stderr)

    def test_unicode_digits_are_not_versions(self):
        from pioweave._compat import UnsupportedAmaranth, check_version
        with self.assertRaises(UnsupportedAmaranth):
            check_version("0.5.\u0663", allow=False)

    def test_no_amaranth_distribution_is_not_an_error(self):
        r = run_py("-c", "import importlib.metadata as m\nreal = m.version\ndef v(n, _r=real):\n    if n == 'amaranth':\n        raise m.PackageNotFoundError(n)\n    return _r(n)\n"
                         "m.version = v\nimport pioweave\nprint(pioweave._gate.check_installed())")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "None"), r.stderr)

    def test_changed_state_decoding_is_diagnosed(self):
        from unittest import mock
        from pioweave import PioCompileError, RP2040PioPlatform, compile_design
        from pioweave import compile as comp
        from pioweave.designs import SquareWave
        with mock.patch.object(comp, "fsm_state_names", lambda sig: None):
            with self.assertRaisesRegex(PioCompileError, "Amaranth internals changed"):
                compile_design(SquareWave(), RP2040PioPlatform(), "p")

class MatrixScriptTests(support.TestCase):
    """S-12: scripts/matrix.sh reports every version and fails when one fails."""

    def run_matrix(self, *args, **env):
        e = dict(os.environ, **env)
        return subprocess.run(["sh", os.path.join(ROOT, "scripts", "matrix.sh")] + list(args), capture_output=True, text=True, env=e, timeout=300)

    def test_syntax(self):
        self.assertEqual(subprocess.run(["sh", "-n", os.path.join(ROOT, "scripts", "matrix.sh")]).returncode, 0)

    def test_no_version_is_a_usage_error(self):
        r = self.run_matrix()
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", r.stderr)

    def test_a_version_that_cannot_be_installed_fails(self):
        r = self.run_matrix("9.9.9", PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("result 9.9.9: FAILED (cannot install amaranth==9.9.9)", r.stdout)

def _design(kind, n):
    from amaranth.hdl import Module
    from pioweave import PioDesign

    class D(PioDesign):
        def elaborate(self, platform):
            m = Module()
            pin = platform.buffer(m, "o", "gpio", 0)
            x = platform.scratch("x")
            if kind == "chain":
                with m.FSM():
                    for i in range(n):
                        with m.State("S%d" % i):
                            m.d.sync += pin.o.eq(i & 1)
                            m.next = "S%d" % ((i + 1) % n)
            elif kind == "elif":
                with m.FSM():
                    with m.State("A"):
                        with m.If(x != 0):
                            m.next = "A"
                        for _ in range(1, n):
                            with m.Elif(x == 0):
                                m.next = "A"
                    with m.State("B"):
                        m.next = "A"
            else:
                with m.FSM():
                    with m.State("A"):
                        m.d.sync += x.eq(2 ** n)
                        m.next = "A"
            return m
    return D

@support.cases
class PathologicalDesignTests(support.TestCase):
    """S-14: a design the compiler cannot handle is a PioCompileError, not a KeyError, RecursionError or ValueError."""

    @support.parametrize("kind,n", [("chain", 4097), ("elif", 1100), ("const", 100000)])
    def test_compile_error(self, kind, n):
        from pioweave import PioCompileError, RP2040PioPlatform, compile_design
        with self.assertRaises(PioCompileError):
            compile_design(_design(kind, n)(), RP2040PioPlatform(), "p")


if __name__ == "__main__":
    import unittest
    unittest.main()
