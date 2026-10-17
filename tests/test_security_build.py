# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""Regression tests of the security audit, builder, workflow and packaging (S-15, S-18, S-20)."""
import os
import subprocess
import sys

from . import support
from .support_security import ROOT, cli, run_py

@support.cases
class BuilderClaimTests(support.TestCase):
    """S-15: the builder refuses what pioasm refuses."""

    @support.parametrize("div", [float("nan"), float("inf"), float("-inf"), 10 ** 400, 0.5, 65536, 1e300], ids=["nan", "inf", "neg_inf", "huge_int", "half", "65536", "1e300"])
    def test_clock_divider(self, div):
        from pioweave.builder import PioError, Program
        with self.assertRaises(PioError):
            Program("p").clock_divider(div)

    def test_clock_divider_range_is_accepted(self):
        from pioweave.builder import Program
        for d in (1, 2.5, 65535):
            Program("p").clock_divider(d)

    @support.parametrize("make", [lambda P: P("é²"), lambda P: P("p").label("é"), lambda P: P("p").label("a²"), lambda P: P("p").define("é", 1), lambda P: P(5),
                                  lambda P: P("p").label(5), lambda P: P("p").define(5, 1), lambda P: P("p").label("x\n"), lambda P: P("٣")],
                         ids=["program", "label_e", "label_sq", "define", "program_int", "label_int", "define_int", "label_newline", "arabic_digit"])
    def test_names(self, make):
        from pioweave.builder import PioError, Program
        with self.assertRaises(PioError):
            make(Program)

    def test_unhashable_operands(self):
        from pioweave.builder import PioError, Program, X
        with self.assertRaises(PioError):
            Program("p").mov(X, [])
        with self.assertRaises(PioError):
            Program("p").in_([], 1)

    def test_clock_divider_matches_pioasm(self):
        # the range was read from pioasm: "clock divider must be between 1 and 65535"
        pioasm = os.environ.get("PIOASM")
        if not pioasm or not os.path.exists(pioasm):
            self.skipTest("needs pioasm")
        src = self.tmp_path / "a.pio"
        for d, ok in ((1, True), (65535, True), (65536, False), (0.5, False)):
            src.write_text(".program a\n.clock_div %s\n  nop\n" % d)
            r = subprocess.run([pioasm, "-o", "c-sdk", str(src), str(self.tmp_path / "a.h")], capture_output=True, text=True)
            self.assertEqual(r.returncode == 0, ok, (d, r.stderr))

class WorkflowTests(support.TestCase):
    """S-18: the CI workflow reads only, and pins its actions by commit."""

    def text(self):
        with open(os.path.join(ROOT, ".github", "workflows", "main.yaml")) as fh:
            return fh.read()

    def test_permissions_are_read_only(self):
        import re
        self.assertRegex(self.text(), r"(?m)^permissions:\n  contents: read$")
        self.assertNotRegex(self.text(), r"(?m)^\s+(contents|id-token|pull-requests|actions): write")

    def test_every_action_is_pinned_by_commit(self):
        import re
        uses = re.findall(r"(?m)^\s+uses:\s*(\S+)", self.text())
        self.assertTrue(uses)
        for u in uses:
            self.assertRegex(u, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$", u)

    def test_credentials_are_not_persisted(self):
        self.assertEqual(self.text().count("persist-credentials: false"), self.text().count("actions/checkout@"))

    def test_python_14_is_in_the_matrix(self):
        self.assertIn("- '3.14'", self.text())

class PackagingTests(support.TestCase):
    """S-20: private-package classifier, a build without git, no private pdm-backend import."""

    def read(self, name):
        with open(os.path.join(ROOT, name)) as fh:
            return fh.read()

    def test_the_package_cannot_be_uploaded_by_accident(self):
        self.assertIn('"Private :: Do Not Upload"', self.read("pyproject.toml"))

    def test_the_backend_is_bounded_and_has_a_fallback_version(self):
        text = self.read("pyproject.toml")
        self.assertIn('requires = ["pdm-backend>=2.4,<3"]', text)
        self.assertIn('fallback_version = "0.0.0.dev0+nogit"', text)

    def test_the_build_hook_uses_no_private_import(self):
        self.assertNotIn("_vendor", self.read("pdm_build.py"))

    def test_the_license_placeholder_note_is_kept(self):
        self.assertIn("[confirm", self.read("LICENSE.txt"))

    def build(self, **env):
        import importlib.util
        import shutil
        try:
            found = importlib.util.find_spec("pdm.backend") is not None
        except ImportError:
            found = False
        if not found:
            self.skipTest("needs pdm-backend (pip install pdm-backend)")
        src = self.tmp_path / "tree"
        shutil.copytree(ROOT, str(src), ignore=shutil.ignore_patterns(".git", "__pycache__", ".pdm-build", "audit-scratch", "*.pyc", "build", "dist", "_build"))
        out = self.tmp_path / "out"
        r = subprocess.run([sys.executable, "-m", "pip", "wheel", "-q", "--no-build-isolation", "--no-deps", "-w", str(out), str(src)], capture_output=True, text=True,
                           env=dict(os.environ, PYTHONWARNINGS="", **env), timeout=300)
        return r, sorted(p.name for p in out.glob("*.whl")) if out.exists() else []

    def test_a_tree_without_git_builds_with_the_placeholder_version(self):
        r, wheels = self.build()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(wheels, ["pioweave-0.0.0.dev0+nogit-py3-none-any.whl"])

    def test_the_version_can_be_given_in_the_environment(self):
        r, wheels = self.build(PDM_BUILD_SCM_VERSION="1.2.3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(wheels, ["pioweave-1.2.3-py3-none-any.whl"])


if __name__ == "__main__":
    import unittest
    unittest.main()
