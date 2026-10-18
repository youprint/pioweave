# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""The `hint:` lines appended to the most common error messages: every hint follows the original text, which stays as it was (other tests match it)."""
from amaranth.hdl import Elaboratable, Module

from pioweave._compat import UnsupportedAmaranth, check_version
from pioweave.builder import PINS, Program, PioError
from pioweave.compile import PioCompileError
from pioweave.platform import RP2040PioPlatform
from pioweave.project import PioProjectError

from . import support
from .test_refusals import compile_body, fsm_states


def builder_error(fn):
    try:
        fn()
    except PioError as e:
        return str(e)
    raise AssertionError("no PioError")


def too_long():
    p = Program("p")
    for _ in range(33):
        p.nop()


def undefined_label():
    p = Program("p")
    p.jmp("nowhere")
    p.words()


def mandatory_side():
    Program("p", side_set=1).nop()


def stray_side():
    Program("p").nop(side=1)


def big_delay():
    Program("p", side_set=2).nop(side=0, delay=8)


def v1_only():
    Program("p", chip="rp2040").wait_jmppin(1, 0)


def empty():
    Program("p").words()


def late_directive():
    p = Program("p")
    p.nop()
    p.clock_divider(2)


def duplicate_label():
    p = Program("p")
    p.label("a")
    p.nop()
    p.label("a")


def label_at_end():
    p = Program("p")
    p.nop()
    p.label("end")
    p.words()


BUILDER = [
    (too_long, "more than 32 instructions", "hint: split the program"),
    (undefined_label, "jmp to undefined label", "hint: define the label with p.label"),
    (mandatory_side, "mandatory side-set", "hint: give side= on every instruction, or create the Program with side_opt=True"),
    (stray_side, "side= given but the program has side_set=0", "hint: create the Program with side_set=N"),
    (big_delay, "delay", "hint: the delay and the side-set share 5 bits"),
    (v1_only, "needs PIO version 1", "hint: use chip='rp2350'"),
    (empty, "has no instructions", "hint: add at least one instruction"),
    (late_directive, "must come before the first instruction", "hint: move this directive"),
    (duplicate_label, "defined twice", "hint: give every label a unique name"),
    (label_at_end, "points past the last instruction", "hint: add an instruction after the label"),
]


@support.cases
class BuilderHintTests(support.TestCase):
    @support.parametrize("fn,original,hint", BUILDER, ids=[b[0].__name__ for b in BUILDER])
    def test_hint_follows_the_original_message(self, fn, original, hint):
        text = builder_error(fn)
        self.assertRegex(text, original)
        self.assertIn(hint, text)
        self.assertLess(text.index(original.split()[0]), text.index("hint:"))     # the hint comes after the original text
        self.assertEqual(text.count("hint:"), 1)

    def test_a_message_without_a_hint_is_unchanged(self):
        text = builder_error(lambda: Program("p").set(PINS, 32))
        self.assertEqual(text, "set value must be an integer from 0 to 31, got 32")

    def test_old_patterns_still_match(self):
        with self.assertRaisesRegex(PioError, r"jmp to undefined label 'nowhere'$|jmp to undefined label 'nowhere'\n"):
            undefined_label()


def two_fsms(m, pl):
    with m.FSM():
        with m.State("A"):
            m.next = "A"
    with m.FSM():
        with m.State("B"):
            m.next = "B"


def comb(m, pl):
    pin = pl.buffer(m, "o", "gpio", 0)
    m.d.comb += pin.o.eq(1)
    fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])


def sets_pin_and_register(m, pl):
    pin = pl.buffer(m, "o", "gpio", 0)
    pl.pio_role(pin, "set")
    x = pl.scratch("x")

    def s0():
        m.d.sync += [pin.o.eq(1), x.eq(1)]
        m.next = "A"
    fsm_states(m, [("A", s0)])


def two_input_pins(m, pl):
    p, q = pl.buffer(m, "i", "gpio", 4), pl.buffer(m, "i", "gpio", 5)

    def a():
        with m.If(p.i & q.i):
            m.next = "C"
        with m.Else():
            m.next = "B"
    fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])


COMPILER = [
    (two_fsms, "expected exactly one m.FSM", "hint: write the whole design as one `with m.FSM():` block"),
    (comb, "combinational logic", "hint: assign with m.d.sync"),
    (sets_pin_and_register, "sets pins and also changes a register", "hint: split the state in two"),
    (two_input_pins, "one PIO instruction", "hint: compare X or Y with 0, X with Y, or test one input pin"),
]


@support.cases
class CompilerHintTests(support.TestCase):
    @support.parametrize("body,original,hint", COMPILER, ids=[c[0].__name__ for c in COMPILER])
    def test_hint_follows_the_original_message(self, body, original, hint):
        with self.assertRaises(PioCompileError) as cm:
            compile_body(body)
        text = str(cm.exception)
        self.assertRegex(text, original)
        self.assertIn(hint, text)
        self.assertLess(text.index(original.split()[0]), text.index("hint:"))

    def test_builder_hint_survives_the_compiler(self):
        # the 33rd instruction of a design is a PioError inside the compiler, which re-raises it as PioCompileError with its text, hint included
        def body(m, pl):
            pin = pl.buffer(m, "o", "gpio", 0)
            with m.FSM():
                for k in range(34):
                    with m.State("S%d" % k):
                        m.d.sync += pin.o.eq(k & 1)
                        m.next = "S%d" % ((k + 1) % 34)
        with self.assertRaisesRegex(PioCompileError, "more than 32 instructions") as cm:
            compile_body(body)
        self.assertIn("hint: split the program", str(cm.exception))


class OtherHintTests(support.TestCase):
    def test_version_gate(self):
        with self.assertRaisesRegex(UnsupportedAmaranth, "tested with Amaranth") as cm:
            check_version("0.4.0", allow=False)
        self.assertIn("Set PIOWEAVE_ALLOW_UNTESTED=1 to try anyway.", str(cm.exception))
        self.assertIn("hint: install a tested version", str(cm.exception))

    def test_project_kind(self):
        class D(Elaboratable):
            def elaborate(self, platform):
                m = Module()
                pin = platform.buffer(m, "o", "gpio", 0)
                with m.FSM():
                    with m.State("A"):
                        m.d.sync += pin.o.eq(1)
                        m.next = "A"
                return m
        with self.assertRaisesRegex(PioProjectError, "project must be 'pio', 'pico-c' or 'micropython'") as cm:
            RP2040PioPlatform().build(D(), "t", str(self.tmp_path / "o"), project="vhdl")
        self.assertIn("hint: pass project=", str(cm.exception))
