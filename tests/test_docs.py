"""The code in the documentation runs, and the output it shows is what it produces: every self-contained Python block of README.md and docs/*.md (a fenced block that starts with
`from` or `import`) is executed in a temporary directory, and the .pio text shown after the first two examples is compared with the file they write."""
import glob
import os
import re
import subprocess
import sys

from . import support

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.S | re.M)


def blocks(path):
    with open(path) as fh:
        text = fh.read()
    return [(m.group(1), m.group(2), text[:m.start()].count("\n") + 1) for m in FENCE.finditer(text)]


def python_blocks():
    out, ids = [], []
    for path in [os.path.join(ROOT, "README.md")] + sorted(glob.glob(os.path.join(ROOT, "docs", "*.md"))):
        for lang, body, line in blocks(path):
            if lang == "python" and re.match(r"(from|import) ", body):
                out.append((path, body))
                ids.append("%s_%d" % (os.path.relpath(path, ROOT), line))
    return out, ids


def run_block(body, cwd):
    r = subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", body], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


@support.cases
class DocsCodeTests(support.TestCase):
    @support.parametrize("path,body", python_blocks()[0], ids=python_blocks()[1])
    def test_every_self_contained_python_block_runs(self, path, body):
        run_block(body, str(self.tmp_path))


def shown_pio(path, after):
    """The text of the first plain fenced block after the line that contains `after`."""
    with open(path) as fh:
        text = fh.read()
    start = text.index(after)
    m = re.search(r"^```\n(.*?)^```", text[start:], re.S | re.M)
    return m.group(1)


@support.cases
class DocsOutputTests(support.TestCase):
    def test_the_pio_programs_shown_are_what_the_examples_write(self):
        readme = os.path.join(ROOT, "README.md")
        py = [b for lang, b, _ in blocks(readme) if lang == "python"][0]
        run_block(py, str(self.tmp_path))
        self.assertEqual(shown_pio(readme, "This compiles to").strip(), (self.tmp_path / "build" / "uart_tx" / "uart_tx.pio").read_text().replace(".pio_version 0\n", "").strip())
        start = os.path.join(ROOT, "docs", "getting-started.md")
        py = [b for lang, b, _ in blocks(start) if lang == "python"][0]
        run_block(py, str(self.tmp_path))
        self.assertEqual(shown_pio(start, "This writes `build/blink/blink.pio`").strip(), (self.tmp_path / "build" / "blink" / "blink.pio").read_text().strip())
