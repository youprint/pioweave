# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""The build hook pdm_build.py (COR-14): the Documentation URL in the package metadata. `python -m build` builds the wheel from the sdist, so the hook runs a second time on
metadata that already has the URL; it must not add the version again (the wheel said ".../tree/main/docsmain/docs"). pdm-backend is not needed: the hook's one import is stubbed."""
import importlib.util
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeVersion:
    """The two properties the hook reads, for the version strings used below."""
    def __init__(self, text):
        self.local = "+" in text
        self.is_prerelease = ".dev" in text or "rc" in text
        self.text = text

    def __str__(self):
        return self.text


def load_hook():
    names = ["pdm", "pdm.backend", "pdm.backend._vendor", "pdm.backend._vendor.packaging", "pdm.backend._vendor.packaging.version"]
    saved = {n: sys.modules.get(n) for n in names}
    for n in names:
        sys.modules[n] = types.ModuleType(n)
    sys.modules[names[-1]].Version = FakeVersion
    try:
        path = os.path.join(ROOT, "pdm_build.py")
        if not os.path.exists(path):
            raise unittest.SkipTest("pdm_build.py is not in the source tree (installed package)")
        spec = importlib.util.spec_from_file_location("pioweave_pdm_build", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


class DocumentationUrlTests(unittest.TestCase):
    def test_release_and_development_versions(self):
        hook = load_hook()
        base = hook.DOCUMENTATION_BASE
        self.assertEqual(hook.documentation_url(base, "0.1.0"), base + "v0.1.0/docs")
        self.assertEqual(hook.documentation_url(base, "0.1.1.dev16+ge295b9c"), base + "main/docs")
        self.assertEqual(hook.documentation_url(base, "0.2.0rc1"), base + "main/docs")

    def test_a_second_run_on_the_rewritten_metadata_changes_nothing(self):
        hook = load_hook()
        once = hook.documentation_url(hook.DOCUMENTATION_BASE, "0.1.1.dev16+ge295b9c")
        self.assertEqual(hook.documentation_url(once, "0.1.1.dev16+ge295b9c"), once)
        self.assertEqual(hook.documentation_url(once, "0.1.0"), once)

    def test_the_hook_rewrites_the_metadata_in_place(self):
        hook = load_hook()
        config = types.SimpleNamespace(metadata={"version": "0.1.0", "urls": {"Documentation": hook.DOCUMENTATION_BASE}})
        hook.pdm_build_initialize(types.SimpleNamespace(config=config))
        hook.pdm_build_initialize(types.SimpleNamespace(config=config))
        self.assertEqual(config.metadata["urls"]["Documentation"], hook.DOCUMENTATION_BASE + "v0.1.0/docs")

    def test_the_base_in_the_hook_is_the_url_in_pyproject(self):
        hook = load_hook()
        with open(os.path.join(ROOT, "pyproject.toml")) as fh:
            text = fh.read()
        self.assertIn('"Documentation" = "%s"' % hook.DOCUMENTATION_BASE, text)


class VersionTests(unittest.TestCase):
    def test_the_subpackages_report_the_version_of_the_package(self):
        # COR-16: they had a hard-coded "0.1.0" while the package version comes from the git tag
        import pioweave
        import pioweave.boards
        import pioweave.builder
        self.assertEqual(pioweave.builder.__version__, pioweave.__version__)
        self.assertEqual(pioweave.boards.__version__, pioweave.__version__)


class TypingMarkerTests(unittest.TestCase):
    def test_py_typed_and_the_classifier_are_only_present_when_the_code_is_annotated(self):
        # COR-17: the markers promise type annotations; the package had four py.typed files and no annotated function (0 of 341)
        import ast
        import glob
        pkg = os.path.join(ROOT, "pioweave")
        markers = glob.glob(os.path.join(pkg, "**", "py.typed"), recursive=True)
        with open(os.path.join(ROOT, "pyproject.toml")) as fh:
            classified = "Typing :: Typed" in fh.read()
        total = annotated = 0
        for path in glob.glob(os.path.join(pkg, "**", "*.py"), recursive=True):
            with open(path) as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    total += 1
                    args = node.args.args + node.args.kwonlyargs
                    annotated += bool(node.returns or any(a.annotation for a in args))
        if markers or classified:
            self.assertGreaterEqual(annotated, 0.9 * total, "py.typed or the Typing :: Typed classifier without annotations (%d of %d functions)" % (annotated, total))


class LicenseLinkTests(unittest.TestCase):
    def test_every_source_header_points_at_an_existing_license_file(self):
        # COR-41: 32 of 47 headers named "LICENSE" (the file is LICENSE.txt) and the README link was broken too
        import glob
        import re
        checked = 0
        for path in glob.glob(os.path.join(ROOT, "pioweave", "**", "*.py"), recursive=True) + glob.glob(os.path.join(ROOT, "tests", "*.py")):
            with open(path) as fh:
                first = fh.readline()
            m = re.search(r"\(see ([^)]*)\)", first)
            if m:
                checked += 1
                self.assertTrue(os.path.exists(os.path.join(os.path.dirname(path), m.group(1))), "%s: %s" % (path, first.strip()))
        self.assertGreater(checked, 30)
        with open(os.path.join(ROOT, "README.md")) as fh:
            for target in re.findall(r"\]\(([^)#]+)\)", fh.read()):
                if not target.startswith("http"):
                    self.assertTrue(os.path.exists(os.path.join(ROOT, target)), "README link " + target)
