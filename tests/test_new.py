# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""`python -m pioweave new` and `--version`: the scaffolded projects exist, and their own tests pass in a fresh process."""
import contextlib
import io
import os
import subprocess
import sys

from pioweave.__main__ import main
from pioweave.scaffold import ScaffoldError, scaffold, template_names

from . import support

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = ["blink", "uart-tx", "uart-rx", "builder-square"]


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


@support.cases
class NewTests(support.TestCase):
    def test_template_names(self):
        self.assertEqual(template_names(), TEMPLATES)

    def test_list(self):
        code, out, _ = cli("new", "--list")
        self.assertEqual(code, 0)
        for t in TEMPLATES:
            self.assertIn(t, out)

    @support.parametrize("template", TEMPLATES)
    def test_scaffold_files_and_their_tests_pass(self, template):
        code, out, err = cli("new", "my-proj", "--template", template, "--dir", str(self.tmp_path))
        self.assertEqual(code, 0, err)
        proj = self.tmp_path / "my-proj"
        self.assertEqual(sorted(p.name for p in proj.iterdir()), ["README.md", "bitweave.toml", "my_proj.py", "test_my_proj.py"])
        toml = (proj / "bitweave.toml").read_text()
        self.assertIn("[pio.my_proj]", toml)
        self.assertIn("generator = [", toml)
        env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""), PYTHONWARNINGS="error")
        r = subprocess.run([sys.executable, "-m", "unittest"], cwd=str(proj), env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_amaranth_templates_use_pioweave_build_as_generator(self):
        scaffold("a", "blink", str(self.tmp_path))
        toml = (self.tmp_path / "a" / "bitweave.toml").read_text()
        self.assertIn('"-m", "pioweave", "build", "a:A"', toml)

    def test_the_generator_of_a_scaffolded_project_builds(self):
        scaffold("gen", "uart-tx", str(self.tmp_path))
        env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
        r = subprocess.run([sys.executable, "-m", "pioweave", "build", "gen:Gen", str(self.tmp_path / "o"), "--chip", "rp2040", "--name", "gen", "--cycles", "60", "--tx", "0x55,0xC3"],
                           cwd=str(self.tmp_path / "gen"), env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("EMIT ", r.stdout)
        self.assertTrue((self.tmp_path / "o" / "project" / "gen.pio").is_file())

    def test_builder_template_follows_the_emit_protocol(self):
        scaffold("sq", "builder-square", str(self.tmp_path))
        r = subprocess.run([sys.executable, str(self.tmp_path / "sq" / "sq.py"), str(self.tmp_path / "o")], env=dict(os.environ, PYTHONPATH=ROOT), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("EMIT {"), r.stdout)
        self.assertTrue((self.tmp_path / "o" / "sq.pio").is_file())

    def test_errors_carry_hints(self):
        with self.assertRaisesRegex(ScaffoldError, "(?s)unknown template .nope..*hint:"):
            scaffold("x", "nope", str(self.tmp_path))
        with self.assertRaisesRegex(ScaffoldError, "(?s)invalid project name.*hint:"):
            scaffold("1bad", "blink", str(self.tmp_path))
        scaffold("dup", "blink", str(self.tmp_path))
        with self.assertRaisesRegex(ScaffoldError, "(?s)not an empty directory.*hint:"):
            scaffold("dup", "blink", str(self.tmp_path))

    def test_cli_reports_errors_without_a_traceback(self):
        code, _, _ = cli("new", "--dir", str(self.tmp_path))
        self.assertNotEqual(code, 0)
        code, _, _ = cli("new", "1bad", "--dir", str(self.tmp_path))
        self.assertNotEqual(code, 0)


class VersionTests(support.TestCase):
    def test_version_option(self):
        code, out, _ = cli("--version")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^pioweave \S+\n$")
        import pioweave
        self.assertIn(pioweave.__version__, out)
