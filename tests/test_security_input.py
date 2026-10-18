# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""Regression tests of the security audit, input handling (S-01 to S-08, S-10, S-05, S-17): names, injection into the generated files, clean command line errors and exit status 2, limits, symbolic links, pioasm failures."""
import os
import subprocess
import sys

from . import support
from .support_security import ROOT, cli, run_py

BAD_NAMES = ["../esc", "a/b", "my-prog", "a.b", "1abc", "x)\nexecute_process(COMMAND touch PWNED)\n#", "evil\n", 'x"y', "", "in", "jmp", "int", "class", "é", "a" * 65, "a b"]

@support.cases
class NameTests(support.TestCase):
    """S-01, S-02, S-04: one strict identifier check at every entry point."""

    @support.parametrize("name", BAD_NAMES, ids=["dotdot", "slash", "hyphen", "dot", "digit", "newline_inject", "trailing_newline", "quote", "empty", "kw_in", "kw_jmp", "kw_int",
                                                 "kw_class", "unicode", "too_long", "space"])
    def test_platform_build_refuses(self, name):
        from pioweave.designs import SquareWave
        from pioweave.platform import RP2040PioPlatform
        from pioweave.project import PioProjectError
        for project in ("pio", "pico-c", "micropython"):
            design = SquareWave()
            design._MustUse__silence = True            # never elaborated, on purpose
            with self.assertRaisesRegex(PioProjectError, "(?s)invalid name.*hint:"):
                RP2040PioPlatform().build(design, name, str(self.tmp_path / "b"), project=project)
        self.assertFalse((self.tmp_path / "b").exists())

    def test_write_project_refuses(self):
        from amaranth.build.run import BuildPlan
        from pioweave.project import PioProjectError, write_project
        with self.assertRaises(PioProjectError):
            write_project(BuildPlan(script="x"), None, None, "../x", "pio")

    @support.parametrize("name", BAD_NAMES[:6], ids=["dotdot", "slash", "hyphen", "dot", "digit", "newline_inject"])
    def test_block_refuses(self, name):
        from pioweave.block import Block, BlockError, Machine
        from pioweave.designs import SquareWave
        b = Block([Machine("a", SquareWave)])
        with self.assertRaisesRegex(BlockError, "invalid name"):
            b.prepare(name)
        self.assertFalse(os.path.exists(os.path.join(str(self.tmp_path), "esc.pio")))

    @support.parametrize("name", ["../esc", "my-prog", "x\ny"])
    def test_cli_name_is_an_error_message(self, name):
        code, out, err = cli("build", "square_wave", str(self.tmp_path / "o"), "--chip", "rp2040", "--name", name)
        self.assertEqual(code, 2)
        self.assertRegex(err, "(?s)^error: invalid name.*hint:")
        self.assertNotIn("Traceback", err)
        self.assertFalse((self.tmp_path / "o").exists())

    def test_cli_block_name_is_an_error_message(self):
        code, _, err = cli("build", "ring", str(self.tmp_path / "o"), "--name", "../esc")
        self.assertEqual(code, 2)
        self.assertIn("invalid name", err)

    def test_the_guard_does_not_depend_on_assert(self):
        r = run_py("-O", "-m", "pioweave", "build", "square_wave", str(self.tmp_path / "o3"), "--chip", "rp2040", "--name", "../esc")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual([p for p in self.tmp_path.rglob("*") if p.is_file()], [])

    def test_micropython_writes_nothing_outside(self):
        tmpd = self.tmp_path / "tmpd"
        tmpd.mkdir()
        r = run_py("-m", "pioweave", "build", "square_wave", str(self.tmp_path / "o7"), "--chip", "rp2040", "--project", "micropython", "--name", "../esc_mp",
                   env={"TMPDIR": str(tmpd)})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(list(tmpd.iterdir()), [])

    def test_a_good_name_still_builds_and_matches_the_c_symbols(self):
        code, out, err = cli("build", "square_wave", str(self.tmp_path / "o"), "--chip", "rp2040", "--project", "pico-c", "--name", "my_prog")
        self.assertEqual(code, 0, err)
        main_c = (self.tmp_path / "o" / "project" / "main.c").read_text()
        self.assertIn("my_prog_program", main_c)
        self.assertTrue((self.tmp_path / "o" / "project" / "my_prog.pio").read_text().startswith(".program my_prog"))

    def test_default_name_from_module_class_is_checked(self):
        d = self.tmp_path / "w"
        d.mkdir()
        (d / "m.py").write_text("from pioweave.designs import SquareWave\nclass Class(SquareWave):\n    pass\n")
        r = run_py("-m", "pioweave", "build", "m:Class", str(self.tmp_path / "o"), "--chip", "rp2040", cwd=str(d))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("invalid name", r.stderr)

@support.cases
class ScaffoldNameTests(support.TestCase):
    """S-06."""

    @support.parametrize("name", ["evil\n", "json", "amaranth", "class", "Test", "pioweave", "a" * 300, "sys", "in"])
    def test_refused(self, name):
        from pioweave.scaffold import ScaffoldError, scaffold
        with self.assertRaises(ScaffoldError):
            scaffold(name, "blink", str(self.tmp_path))
        self.assertEqual(list(self.tmp_path.iterdir()), [])

    def test_cli_reports_it(self):
        code, _, err = cli("new", "json", "--dir", str(self.tmp_path))
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_hyphen_names_still_work(self):
        from pioweave.scaffold import scaffold
        files = scaffold("my-blink", "blink", str(self.tmp_path))
        self.assertEqual(len(files), 4)

    def test_an_oserror_is_a_scaffold_error(self):
        from pioweave.scaffold import ScaffoldError, scaffold
        (self.tmp_path / "file.txt").write_text("x")
        with self.assertRaises(ScaffoldError):
            scaffold("ok", "blink", str(self.tmp_path / "file.txt"))

@support.cases
class SimOptionTests(support.TestCase):
    """S-03, S-08: waveforms, words and cycles are validated for the API and the CLI alike."""

    @support.parametrize("stim", [{0: '0"\n[pio.pwn]\ngenerator = ["sh"]\n#'}, {0: ""}, {0: "012"}, {99: "01"}, {-1: "01"}, {"0": "01"}, {5: 1}])
    def test_stim_refused(self, stim):
        from pioweave.project import PioProjectError, SimOptions
        with self.assertRaises(PioProjectError):
            SimOptions(stim=stim)

    @support.parametrize("kw", [{"cycles": 0}, {"cycles": -5}, {"cycles": 1.5}, {"cycles": True}, {"tx": (2 ** 32,)}, {"tx": (-3,)}, {"tx_feed": ((-1, 2),)}, {"tx_feed": ((1,),)},
                                {"irq_set": ((0, 9),)}])
    def test_other_options_refused(self, kw):
        from pioweave.project import PioProjectError, SimOptions
        with self.assertRaises(PioProjectError):
            SimOptions(**kw)

    def test_a_valid_one_is_kept(self):
        from pioweave.project import SimOptions
        s = SimOptions(cycles=10, stim={3: "0101"}, tx=(1, 0xFFFFFFFF), tx_feed=((2, 3),))
        self.assertEqual(s.cycles, 10)

    @support.parametrize("args", [["--stim", "0=0\n[pio.pwn]"], ["--cycles", "-5"], ["--tx", "0x1ffffffffffff"], ["--tx", "-3"], ["--clock-hz", "-5"], ["--stim", "99=0101"],
                                  ["--stim", "5="], ["--stim", "0=01x2"]])
    def test_cli_refuses(self, args):
        code, _, err = cli("build", "square_wave", str(self.tmp_path / "o"), "--chip", "rp2040", *args)
        self.assertEqual(code, 2, err)
        self.assertTrue(err.startswith("error:"), err)
        self.assertFalse((self.tmp_path / "o").exists())

    def test_clock_hz_zero_is_not_ignored(self):
        code, _, err = cli("build", "square_wave", str(self.tmp_path / "o"), "--chip", "rp2040", "--clock-hz", "0")
        self.assertEqual(code, 2, err)

    def test_block_sim_is_checked(self):
        from pioweave.block import BlockError, BlockSim
        with self.assertRaises(BlockError):
            BlockSim(stim={0: "a\nb"})
        with self.assertRaises(BlockError):
            BlockSim(cycles=-1)

@support.cases
class CliErrorTests(support.TestCase):
    """S-07, S-10: user errors are messages with exit status 2, never tracebacks; cycles have a ceiling."""

    @support.parametrize("argv", [
        ["build", "square_wave", "O", "--chip", "rp2040", "--stim", "abc"],
        ["build", "square_wave", "O", "--chip", "rp2040", "--tx", "zz"],
        ["build", "square_wave", "O", "--chip", "rp2040", "--tx-feed", "5"],
        ["build", "square_wave", "O", "--chip", "rp2040", "--tx-feed", "a=1"],
        ["build", "ring", "O", "--chip", "zz"],
        ["build", "ring", "O", "--stim", "x=1"],
        ["build", "ring", "O", "--cycles", "0"],
        ["build", "ring", "O", "--clock-hz", "-1"],
        ["build", "ring", "O", "--tx", "1"],
        ["emulate", "square_wave", "--chip", "rp2040", "--stim", "0="],
        ["emulate", "square_wave", "--chip", "rp2040", "--cycles", "0"],
        ["emulate", "square_wave", "--chip", "rp2040", "--vcd", "/nonexistent/dir/x.vcd"],
        ["emulate", "square_wave", "--chip", "zz"],
        ["emulate", "ring", "--stim", "0=2"],
        ["build", "nomod:Cls", "O", "--chip", "rp2040"],
        ["build", "os:nothing", "O", "--chip", "rp2040"],
        ["build", ":X", "O", "--chip", "rp2040"],
        ["build", "pioweave.designs:", "O", "--chip", "rp2040"],
        ["build", "collections:OrderedDict", "O", "--chip", "rp2040"],
        ["build", "sys:exit", "O", "--chip", "rp2040"],
        ["build", "nosuchdesign", "O", "--chip", "rp2040"],
        ["new"],
        ["new", "1bad"],
    ])
    def test_message_and_status_2(self, argv):
        argv = [str(self.tmp_path / "o") if x == "O" else x for x in argv]
        code, out, err = cli(*argv)
        self.assertEqual(code, 2, out + err)
        self.assertTrue(err.startswith("error:"), err)
        self.assertNotIn("Traceback", err)

    def test_traceback_on_request(self):
        r = run_py("-m", "pioweave", "build", "square_wave", str(self.tmp_path / "o"), "--chip", "rp2040", "--tx", "zz", env={"PIOWEAVE_TRACEBACK": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Traceback", r.stderr)

    def test_a_module_is_not_imported_before_the_chip_is_checked(self):
        d = self.tmp_path / "w"
        d.mkdir()
        (d / "evil.py").write_text("open('RAN_AT_IMPORT', 'w').close()\nclass X: pass\n")
        r = run_py("-m", "pioweave", "build", "evil:X", str(self.tmp_path / "o"), cwd=str(d))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertFalse((d / "RAN_AT_IMPORT").exists())

    def test_cycles_have_a_ceiling_and_an_override(self):
        code, _, err = cli("emulate", "square_wave", "--chip", "rp2040", "--cycles", "1000001")
        self.assertEqual(code, 2)
        self.assertIn("--max-cycles", err)
        code, out, err = cli("emulate", "square_wave", "--chip", "rp2040", "--cycles", "1000001", "--max-cycles", "2000000")
        self.assertEqual(code, 0, err)

    def test_build_cycles_have_a_ceiling(self):
        code, _, err = cli("build", "square_wave", str(self.tmp_path / "o"), "--chip", "rp2040", "--cycles", "10000000000")
        self.assertEqual(code, 2)
        self.assertIn("--max-cycles", err)
        self.assertFalse((self.tmp_path / "o").exists())

    def test_sim_options_ceiling(self):
        from pioweave.project import PioProjectError, SimOptions
        with self.assertRaisesRegex(PioProjectError, "max-cycles"):
            SimOptions(cycles=2_000_000)
        self.assertEqual(SimOptions(cycles=2_000_000, max_cycles=3_000_000).cycles, 2_000_000)

class SymlinkTests(support.TestCase):
    """S-05: a project is not written through a symbolic link."""

    def test_a_linked_file_is_refused_and_the_victim_is_untouched(self):
        victim = self.tmp_path / "victim.txt"
        victim.write_text("precious user data\n")
        proj = self.tmp_path / "o" / "project"
        proj.mkdir(parents=True)
        os.symlink(str(victim), str(proj / "main.c"))
        code, out, err = cli("build", "square_wave", str(self.tmp_path / "o"), "--chip", "rp2040", "--project", "pico-c")
        self.assertEqual(code, 2, out + err)
        self.assertIn("symbolic link", err)
        self.assertEqual(victim.read_text(), "precious user data\n")
        self.assertFalse((proj / "square_wave.pio").exists())

    def test_a_linked_block_file_is_refused(self):
        victim = self.tmp_path / "victim.txt"
        victim.write_text("x")
        proj = self.tmp_path / "o" / "project"
        proj.mkdir(parents=True)
        os.symlink(str(victim), str(proj / "README.md"))
        code, _, err = cli("build", "ring", str(self.tmp_path / "o"))
        self.assertEqual(code, 2)
        self.assertEqual(victim.read_text(), "x")

    def test_a_build_dir_that_is_a_file_is_a_message(self):
        (self.tmp_path / "o").mkdir()
        (self.tmp_path / "o" / "project").write_text("x")
        code, _, err = cli("build", "square_wave", str(self.tmp_path / "o"), "--chip", "rp2040")
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("error:"), err)

    def test_a_rebuild_into_the_same_directory_still_works(self):
        for _ in range(2):
            code, _, err = cli("build", "square_wave", str(self.tmp_path / "o"), "--chip", "rp2040")
            self.assertEqual(code, 0, err)

@support.cases
class PioasmTests(support.TestCase):
    """S-17: a PIOASM that is not an executable file, or a pioasm that fails, is a PioProjectError."""

    def micropython(self, pioasm_env):
        from pioweave.designs import SquareWave
        from pioweave.platform import RP2040PioPlatform
        d = SquareWave()
        old = os.environ.get("PIOASM")
        os.environ["PIOASM"] = pioasm_env
        try:
            with self.assertRaises(Exception) as cm:
                RP2040PioPlatform().build(d, "sq", str(self.tmp_path / "o"), project="micropython", sim=None)
        finally:
            if old is None:
                os.environ.pop("PIOASM")
            else:
                os.environ["PIOASM"] = old
        return cm.exception

    def test_a_directory_and_a_data_file_are_not_pioasm(self):
        from pioweave.project import find_pioasm
        (self.tmp_path / "data").write_text("x")
        for bad in (str(self.tmp_path), str(self.tmp_path / "data")):
            old = os.environ.get("PIOASM")
            os.environ["PIOASM"] = bad
            try:
                self.assertNotEqual(find_pioasm(), os.path.abspath(bad))
            finally:
                if old is None:
                    os.environ.pop("PIOASM")
                else:
                    os.environ["PIOASM"] = old

    def test_a_fake_that_writes_nothing(self):
        from pioweave.project import PioProjectError
        fake = self.tmp_path / "fakepioasm"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        e = self.micropython(str(fake))
        self.assertIsInstance(e, PioProjectError)
        self.assertIn("no output", str(e))

    def test_a_pioasm_that_hangs_is_stopped(self):
        from unittest import mock
        from pioweave import project
        from pioweave.project import PioProjectError
        fake = self.tmp_path / "slowpioasm"
        fake.write_text("#!/bin/sh\nsleep 30\n")
        fake.chmod(0o755)
        with mock.patch.object(project, "PIOASM_TIMEOUT", 1):
            e = self.micropython(str(fake))
        self.assertIsInstance(e, PioProjectError)
        self.assertIn("did not finish", str(e))

    def test_a_pioasm_that_cannot_start(self):
        from unittest import mock
        from pioweave import project
        from pioweave.project import PioProjectError
        fake = self.tmp_path / "bad"
        fake.write_text("#!/nonexistent/interpreter\n")
        fake.chmod(0o755)
        e = self.micropython(str(fake))
        self.assertIsInstance(e, PioProjectError)
        self.assertIn("cannot be run", str(e))


if __name__ == "__main__":
    import unittest
    unittest.main()
