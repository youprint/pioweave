# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""Project scaffolding for ``python -m pioweave new NAME``: a directory with a design module, a unittest file that compiles it and runs it in the emulator, a README.md and a
``bitweave.toml`` with a ``[pio.NAME]`` entry.

The templates are small, complete designs; ``TEMPLATES`` maps a template name to its description. Needs no Amaranth to import; the generated project needs it (or, for
``builder-square``, nothing but pioweave).
"""
import keyword
import os
import re
import sys

from ._validate import RESERVED_NAMES, MAX_NAME_LENGTH

__all__ = ["TEMPLATES", "ScaffoldError", "scaffold", "template_names"]


class ScaffoldError(Exception):
    """The project cannot be created (bad name, unknown template, the directory is not empty)."""


_HEADER = "# %s: a pioweave project created by `python -m pioweave new`. Nothing here has run on a chip.\n"

_BLINK = '''"""@NAME@: a square wave on GPIO 0, two cycles high and two cycles low (an Amaranth FSM; one state is one PIO instruction)."""
from amaranth.hdl import Module

from pioweave import PioDesign, RP2040PioPlatform, SimOptions


class @CLASS@(PioDesign):
    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", 0)          # use a board (pioweave.boards) to ask for "led" instead of a GPIO number
        with m.FSM():
            with m.State("HIGH"):
                m.d.sync += pin.o.eq(1)
                m.next = "HIGH_HOLD"
            with m.State("HIGH_HOLD"):                    # no effect: becomes the delay of the instruction before
                m.next = "LOW"
            with m.State("LOW"):
                m.d.sync += pin.o.eq(0)
                m.next = "LOW_HOLD"
            with m.State("LOW_HOLD"):
                m.next = "HIGH"
        return m


CHIP = "rp2040"
CYCLES = 24


def factory():
    return @CLASS@(), RP2040PioPlatform()


if __name__ == "__main__":
    design, platform = factory()
    platform.build(design, "@NAME@", "build/@NAME@", project="pio", sim=SimOptions(factory=factory, cycles=CYCLES))
'''

_BLINK_TEST = '''import unittest

from pioweave import compile_design
from pioweave.emulator import run_compiled

import @MODULE@


class Tests(unittest.TestCase):
    def setUp(self):
        self.compiled = compile_design(*@MODULE@.factory(), "@NAME@")

    def test_program(self):
        self.assertEqual(len(self.compiled.program.words()), 2)       # set pins 1 [1]; set pins 0 [1]

    def test_waveform(self):
        result = run_compiled(self.compiled, 16)
        self.assertEqual(result.wave(self.compiled.pin(0)), "1100" * 4)
        self.assertEqual(result.executed, 8)


if __name__ == "__main__":
    unittest.main()
'''

_UART_TX = '''"""@NAME@: an 8N1 UART transmitter on GPIO 0, 2 clock cycles per bit. One word from the TX FIFO is one frame (the low 8 bits, least significant first)."""
from amaranth.hdl import Module

from pioweave import PioDesign, RP2040PioPlatform, SimOptions


class @CLASS@(PioDesign):
    def elaborate(self, platform):
        m = Module()
        tx = platform.buffer(m, "o", "gpio", 0)
        sh = platform.shifter(m, out_shift="right")       # the OSR shifts right: bit 0 goes out first
        x = platform.scratch("x")
        with m.FSM():
            with m.State("PULL"):
                m.d.sync += tx.o.eq(1)                    # idle level, set by side-set while waiting for a word
                with sh.pull(m):
                    m.next = "PULL_HOLD"
            with m.State("PULL_HOLD"):                    # no effect: the delay of the pull
                m.next = "START"
            with m.State("START"):
                m.d.sync += [tx.o.eq(0), x.eq(7)]         # start bit and the bit counter
                m.next = "START_HOLD"
            with m.State("START_HOLD"):                   # no effect: the delay of START, so the start bit lasts 2 cycles like the other bits
                m.next = "BIT"
            with m.State("BIT"):
                sh.out(m, tx)                             # out pins, 1
                m.next = "LOOP"
            with m.State("LOOP"):
                m.d.sync += x.eq(x - 1)                   # jmp x-- : eight bits
                with m.If(x != 0):
                    m.next = "BIT"
                with m.Else():
                    m.next = "PULL"
        return m


CHIP = "rp2040"
CYCLES = 60
TX = (0x55, 0xC3)


def factory():
    return @CLASS@(), RP2040PioPlatform(sm_clock_hz=16_000_000)


if __name__ == "__main__":
    design, platform = factory()
    platform.build(design, "@NAME@", "build/@NAME@", project="pico-c", sim=SimOptions(factory=factory, cycles=CYCLES, tx=TX))
'''

_UART_TX_TEST = '''import unittest

from pioweave import compile_design
from pioweave.emulator import run_compiled

import @MODULE@


def frame(byte):
    """The line levels of one 8N1 frame at 2 cycles per bit."""
    bits = "0" + "".join(str((byte >> k) & 1) for k in range(8)) + "1"
    return "".join(b * 2 for b in bits)


class Tests(unittest.TestCase):
    def test_frames_appear_on_the_pin(self):
        compiled = compile_design(*@MODULE@.factory(), "@NAME@")
        result = run_compiled(compiled, @MODULE@.CYCLES, tx=@MODULE@.TX)
        wave = result.wave(compiled.pin(0))
        for byte in @MODULE@.TX:
            self.assertIn(frame(byte), wave, hex(byte))
        self.assertEqual(result.tx_left, 0)           # both words were taken from the TX FIFO


if __name__ == "__main__":
    unittest.main()
'''

_UART_RX = '''"""@NAME@: an 8N1 UART receiver on GPIO 4, 8 clock cycles per bit. It waits for the start bit, samples the middle of every bit and pushes one word with the byte in bits 31..24."""
from amaranth.hdl import Module

from pioweave import PioDesign, RP2040PioPlatform, SimOptions


class @CLASS@(PioDesign):
    def elaborate(self, platform):
        m = Module()
        rx = platform.buffer(m, "i", "gpio", 4)
        sh = platform.shifter(m, in_shift="right")        # the ISR shifts right: after 8 bits the byte is in bits 31..24
        x = platform.scratch("x")
        with m.FSM():
            with m.State("WAIT_START"):
                with m.If(~rx.i):
                    m.next = "SETX"
            with m.State("SETX"):
                m.d.sync += x.eq(7)
                m.next = "H1"
            for k in range(1, 11):                        # ten cycles: the first sample falls in the middle of the first data bit
                with m.State("H%d" % k):
                    m.next = "H%d" % (k + 1) if k < 10 else "BIT"
            with m.State("BIT"):
                sh.in_(m, rx)                             # in pins, 1 [6]: the six states below are its delay, a bit lasts 8 cycles
                m.next = "B1"
            for k in range(1, 7):
                with m.State("B%d" % k):
                    m.next = "B%d" % (k + 1) if k < 6 else "LOOP"
            with m.State("LOOP"):
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "BIT"
                with m.Else():
                    m.next = "PUSH"
            with m.State("PUSH"):
                with sh.push(m):
                    m.next = "WAIT_START"
        return m


CHIP = "rp2040"
BYTES = (0x5A, 0xC3)


def line(bytes_, cycles_per_bit=8):
    """The input waveform of GPIO 4: idle high, then each byte as an 8N1 frame."""
    out = "1" * 6
    for b in bytes_:
        bits = "0" + "".join(str((b >> k) & 1) for k in range(8)) + "1"
        out += "".join(c * cycles_per_bit for c in bits) + "1" * 4
    return out


STIM = {4: line(BYTES)}
CYCLES = len(STIM[4]) + 20


def factory():
    return @CLASS@(), RP2040PioPlatform()


if __name__ == "__main__":
    design, platform = factory()
    platform.build(design, "@NAME@", "build/@NAME@", project="pio", sim=SimOptions(factory=factory, cycles=CYCLES, stim=STIM))
'''

_UART_RX_TEST = '''import unittest

from pioweave import compile_design
from pioweave.emulator import run_compiled

import @MODULE@


class Tests(unittest.TestCase):
    def test_bytes_are_received(self):
        compiled = compile_design(*@MODULE@.factory(), "@NAME@")
        result = run_compiled(compiled, @MODULE@.CYCLES, @MODULE@.STIM)
        self.assertEqual([w >> 24 for _, w in result.rx], list(@MODULE@.BYTES))


if __name__ == "__main__":
    unittest.main()
'''

_SQUARE = '''"""@NAME@: a square wave on GPIO 0 written with the Python builder (pioweave.builder; needs no Amaranth). HIGH and LOW are state machine cycles, 1 to 32 each."""
import os
import sys

from pioweave.builder import PINS, Program, write_pio

CHIP = "rp2040"
HIGH = 5
LOW = 3
CYCLES = 48


def program():
    p = Program("@NAME@", chip=CHIP)
    with p.wrapped():
        p.set(PINS, 1, delay=HIGH - 1)       # one set takes 1 cycle, the delay adds the rest
        p.set(PINS, 0, delay=LOW - 1)
    return p


if __name__ == "__main__":
    # the generator protocol of bitweave: write OUTDIR/@NAME@.pio and print an EMIT line
    outdir = sys.argv[1] if len(sys.argv) > 1 else "build"
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "@NAME@.pio")
    write_pio([program()], path)
    print('EMIT {"path": "%s"}' % path.replace("\\\\", "/"))
'''

_SQUARE_TEST = '''import unittest

from pioweave.emulator import run_case

import @MODULE@


class Tests(unittest.TestCase):
    def test_levels_last_as_long_as_asked(self):
        result = run_case(@MODULE@.program(), @MODULE@.CYCLES, set_base=0, set_count=1, dirs=1)
        period = @MODULE@.HIGH + @MODULE@.LOW
        self.assertEqual(result.wave(0)[:period * 2], ("1" * @MODULE@.HIGH + "0" * @MODULE@.LOW) * 2)
        self.assertEqual(result.executed, 2 * @MODULE@.CYCLES // period)      # two instructions per period, one per call of set

    def test_program_is_two_words(self):
        self.assertEqual(len(@MODULE@.program().words()), 2)


if __name__ == "__main__":
    unittest.main()
'''

TEMPLATES = {
    "blink": ("a square wave on GPIO 0 as an Amaranth FSM (RP2040)", _BLINK, _BLINK_TEST, "amaranth"),
    "uart-tx": ("an 8N1 UART transmitter, 2 cycles per bit, as an Amaranth FSM (RP2040)", _UART_TX, _UART_TX_TEST, "amaranth"),
    "uart-rx": ("an 8N1 UART receiver, 8 cycles per bit, as an Amaranth FSM (RP2040)", _UART_RX, _UART_RX_TEST, "amaranth"),
    "builder-square": ("a square wave with chosen high and low times, written with the Python builder (no Amaranth)", _SQUARE, _SQUARE_TEST, "builder"),
}
"""template name -> (description, design module source, test source, kind); ``@NAME@``, ``@MODULE@`` and ``@CLASS@`` are replaced."""

_GENERATOR = {"blink": (24, []), "uart-tx": (60, ["--tx", "0x55,0xC3"]), "uart-rx": (100, []), "builder-square": (48, [])}
"""template -> (cycles of the reference simulation, extra arguments of ``pioweave build``)"""

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")

#: Modules a project module must not shadow (the project directory is first on `sys.path`): the standard library (3.10 and later know the list; for 3.9 the common ones) and the
#: packages the project imports.
_SHADOW = frozenset(getattr(sys, "stdlib_module_names", ())) | frozenset((
    "abc", "argparse", "ast", "asyncio", "collections", "contextlib", "copy", "dataclasses", "enum", "functools", "importlib", "io", "itertools", "json", "math", "os", "pathlib",
    "random", "re", "shutil", "string", "subprocess", "sys", "tempfile", "test", "time", "types", "typing", "unittest", "warnings")) | frozenset(("pioweave", "amaranth", "test"))


def template_names():
    """The names of the templates.

    Returns
    -------
    list of str
        The template names in a fixed order.
    """
    return list(TEMPLATES)


def _fill(text, name, module, cls):
    return text.replace("@NAME@", name).replace("@MODULE@", module).replace("@CLASS@", cls)


def scaffold(name, template="blink", parent="."):
    """Create the project directory ``parent/name``.

    Parameters
    ----------
    name : str
        The project name: a letter, then letters, digits, ``_`` and ``-``. The design module is the name with ``-`` replaced by ``_`` (lower case), the PIO program has the
        module name.
    template : str
        One of `template_names`.
    parent : str
        The directory in which the project directory is created.

    Returns
    -------
    list of str
        The paths of the files written.

    Raises
    ------
    ScaffoldError
        If the name is not valid, the template is unknown or the directory exists and is not empty.

    Examples
    --------
    >>> import tempfile, os
    >>> from pioweave.scaffold import scaffold
    >>> with tempfile.TemporaryDirectory() as d:
    ...     sorted(os.path.basename(p) for p in scaffold("my-blink", "blink", d))
    ['README.md', 'bitweave.toml', 'my_blink.py', 'test_my_blink.py']
    """
    if template not in TEMPLATES:
        raise ScaffoldError("unknown template %r; available: %s\n  hint: `python -m pioweave new --list` shows them" % (template, ", ".join(TEMPLATES)))
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ScaffoldError("invalid project name %r: use a letter, then letters, digits, '_' or '-'\n  hint: for example `python -m pioweave new my-blink`" % (name,))
    module = name.replace("-", "_").lower()
    if len(name) > MAX_NAME_LENGTH:
        raise ScaffoldError("invalid project name %r: longer than %d characters\n  hint: choose a shorter name" % (name, MAX_NAME_LENGTH))
    if module in RESERVED_NAMES or keyword.iskeyword(module) or module in _SHADOW:
        raise ScaffoldError("invalid project name %r: the module %s.py would be a keyword or would shadow a standard library or project module\n"
                            "  hint: choose another name, for example `python -m pioweave new my-%s`" % (name, module, module))
    cls = "".join(part.capitalize() for part in re.split(r"[_-]+", name) if part) or "Design"
    if not cls[0].isalpha():
        cls = "Design" + cls
    dest = os.path.join(parent, name)
    if os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
        raise ScaffoldError("%s exists and is not an empty directory\n  hint: choose another name, or remove the directory first" % dest)
    description, design, test, kind = TEMPLATES[template]
    sim_cycles, extra = _GENERATOR[template]
    if kind == "amaranth":
        args = ["{root}/.bitweave-tools/bin/python", "-m", "pioweave", "build", "%s:%s" % (module, cls), "{outdir}", "--chip", "rp2040", "--name", module, "--cycles", str(sim_cycles)] + extra
        generator = "[%s]" % ", ".join('"%s"' % x for x in args)
        run_note = "python -m pioweave build %s:%s build --chip rp2040 --cycles %d %s     # the project is written to build/project" % (module, cls, sim_cycles, " ".join(extra))
    else:
        generator = '["{root}/.bitweave-tools/bin/python", "{root}/%s.py", "{outdir}"]' % module
        run_note = "python %s.py build" % module
    files = {
        module + ".py": (_HEADER % name) + _fill(design, module, module, cls),
        "test_" + module + ".py": (_HEADER % name) + _fill('"""Tests of %s: compile it and run it in the emulator (python -m unittest)."""\n' % module, module, module, cls) + _fill(test, module, module, cls),
        "bitweave.toml": ("# bitweave configuration of %s. Run bitweave from this directory: the generator imports %s from the current directory [verify].\n"
                          "[pio.%s]\ngenerator = %s\nchip = \"rp2040\"\n\n[[pio.%s.sim]]\nname = \"runs\"\ncycles = %d\n" % (name, module, module, generator, module, sim_cycles)),
        "README.md": ("# %s\n\nCreated by `python -m pioweave new %s --template %s`: %s.\n\n## Files\n\n- `%s.py`: the design\n- `test_%s.py`: unit tests that compile the design and run it in the "
                      "emulator\n- `bitweave.toml`: a bitweave entry `[pio.%s]` whose generator runs this project\n\n## Use\n\n    python -m unittest              # the tests (need pioweave%s)\n    %s\n\n"
                      "Edit the design, change the expected values in the tests, and run them again. Nothing has run on a chip; the emulator is a model.\n"
                      % (name, name, template, description, module, module, module, " and Amaranth" if kind == "amaranth" else "", run_note)),
    }
    written = []
    try:
        os.makedirs(dest, exist_ok=True)
        for fname, text in files.items():
            path = os.path.join(dest, fname)
            with open(path, "w", newline="\n") as fh:
                fh.write(text)
            written.append(path)
    except OSError as e:
        raise ScaffoldError("cannot write %s: %s" % (dest, e)) from None
    return written
