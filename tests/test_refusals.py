# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""What the Amaranth-to-PIO compiler refuses, and why each design below is outside the subset because mapping it would change its cycle
behaviour or needs hardware PIO does not have. Every refusal must name the reason; none may be approximated."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from amaranth.build import Pins, Resource  # noqa: E402
from amaranth.hdl import Elaboratable, Module, Signal  # noqa: E402

from pioweave.compile import PioCompileError, compile_design  # noqa: E402
from pioweave.platform import RP2040PioPlatform, RP2350PioPlatform  # noqa: E402

from . import support  # noqa: E402


class Dyn(Elaboratable):
    def __init__(self, body):
        self.body = body
        self._MustUse__silence = True     # designs that are refused (or fail to elaborate) are not an error to report

    def elaborate(self, platform):
        m = Module()
        self.body(m, platform)
        return m


def compile_body(body, chip=RP2040PioPlatform, extra=()):
    pl = chip()
    pl.add_resources(list(extra))
    return compile_design(Dyn(body), pl, "t")


def fsm_states(m, states):
    """states: list of (name, callable(m)); builds the FSM in that order."""
    with m.FSM():
        for name, fn in states:
            with m.State(name):
                fn()


def spare_module():
    """A module and platform shifter pieces nobody will elaborate (the helper-misuse tests call the helpers directly): silenced so that Amaranth reports nothing."""
    m = Module()
    m._MustUse__silence = True
    return m


def shifter_for_misuse(platform, **config):
    m = spare_module()
    sh = platform.shifter(m, **config)
    sh.fifo._MustUse__silence = True
    return m, sh


class RefusalCase(support.TestCase):
    def refuse(self, body, match, **kw):
        with self.assertRaisesRegex(PioCompileError, match):
            compile_body(body, **kw)


@support.cases
class RefusalTests(RefusalCase):
    def test_two_register_operations_in_one_state(self):
        def body(m, pl):
            x, y = pl.scratch("x"), pl.scratch("y")

            def s0():
                m.d.sync += [x.eq(1), y.eq(2)]
                m.next = "A"
            fsm_states(m, [("A", s0)])
        self.refuse(body, "two register operations")

    def test_set_pins_and_register_in_one_state(self):
        def body(m, pl):
            pin = pl.buffer(m, "o", "gpio", 0)
            pl.pio_role(pin, "set")
            x = pl.scratch("x")

            def s0():
                m.d.sync += [pin.o.eq(1), x.eq(1)]
                m.next = "A"
            fsm_states(m, [("A", s0)])
        self.refuse(body, "sets pins and also changes a register")

    def test_pin_change_together_with_a_jump(self):
        def body(m, pl):
            pin = pl.buffer(m, "o", "gpio", 0)
            pl.pio_role(pin, "set")

            def a():
                m.d.sync += pin.o.eq(1)
                m.next = "C"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
        self.refuse(body, "changes a pin and then jumps")

    def test_tests_that_pio_cannot_do(self):
        def cmp_const(m, pl):
            x = pl.scratch("x")

            def a():
                with m.If(x == 5):
                    m.next = "C"
                with m.Else():
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
        self.refuse(cmp_const, "compare X or Y with 0")

        def compound(m, pl):
            p, q = pl.buffer(m, "i", "gpio", 4), pl.buffer(m, "i", "gpio", 5)

            def a():
                with m.If(p.i & q.i):
                    m.next = "C"
                with m.Else():
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
        self.refuse(compound, "one PIO instruction")

        def elif_chain(m, pl):
            x, y = pl.scratch("x"), pl.scratch("y")

            def a():
                with m.If(x == 0):
                    m.next = "C"
                with m.Elif(y == 0):
                    m.next = "D"
                with m.Else():
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A")), ("D", lambda: setattr(m, "next", "A"))])
        self.refuse(elif_chain, "more than one test")

        def other_signal(m, pl):
            s = Signal()

            def a():
                with m.If(s):
                    m.next = "C"
                with m.Else():
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
        self.refuse(other_signal, "not an input pin or a scratch register")

        def low_pin_jump(m, pl):
            p = pl.buffer(m, "i", "gpio", 4)

            def a():
                with m.If(~p.i):
                    m.next = "C"
                with m.Else():
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
        self.refuse(low_pin_jump, "high but not on one that is low")

        def x_eq_y(m, pl):
            x, y = pl.scratch("x"), pl.scratch("y")

            def a():
                with m.If(x == y):
                    m.next = "C"
                with m.Else():
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
        self.refuse(x_eq_y, "no PIO jump")

        def both_branches_jump(m, pl):
            x = pl.scratch("x")

            def a():
                with m.If(x == 0):
                    m.next = "C"
                with m.Else():
                    m.next = "A"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
        self.refuse(both_branches_jump, "continue with the next state on one branch")

        def multibit_switch(m, pl):
            x = pl.scratch("x")

            def a():
                with m.Switch(x[0:2]):
                    with m.Case(1):
                        m.next = "C"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
        self.refuse(multibit_switch, "multi-bit")

    def test_decrement_rules(self):
        def nonzero_without_decrement(m, pl):
            x = pl.scratch("x")

            def a():
                with m.If(x != 0):
                    m.next = "A"
                with m.Else():
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        self.refuse(nonzero_without_decrement, "always decrements")

        def decrement_in_one_branch(m, pl):
            x = pl.scratch("x")

            def a():
                with m.If(x != 0):
                    m.d.sync += x.eq(x - 1)
                    m.next = "A"
                with m.Else():
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        self.refuse(decrement_in_one_branch, "register changes differ")

        def decrement_with_zero_test(m, pl):
            x = pl.scratch("x")

            def a():
                m.d.sync += x.eq(x - 1)
                with m.If(x == 0):
                    m.next = "A"
                with m.Else():
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        self.refuse(decrement_with_zero_test, "does not decrement")

    def test_wait_rules(self):
        def wait_with_register(m, pl):
            p = pl.buffer(m, "i", "gpio", 4)
            x = pl.scratch("x")

            def a():
                m.d.sync += x.eq(1)
                with m.If(p.i):
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        self.refuse(wait_with_register, "cannot change registers")

        def wait_not_to_next(m, pl):
            p = pl.buffer(m, "i", "gpio", 4)

            def a():
                with m.If(p.i):
                    m.next = "C"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
        self.refuse(wait_not_to_next, "continue with the next state")

    def test_assignments_that_need_hardware_pio_does_not_have(self):
        def two_set_pins(m, pl):
            a, b = pl.buffer(m, "o", "gpio", 0), pl.buffer(m, "o", "gpio", 1)

            def s0():
                m.d.sync += a.o.eq(1)
                m.next = "B"

            def s1():
                m.d.sync += b.o.eq(1)
                m.next = "A"
            fsm_states(m, [("A", s0), ("B", s1)])
        self.refuse(two_set_pins, "more than one output signal is driven by `set pins`")

        def big_constant(m, pl):
            x = pl.scratch("x")

            def a():
                m.d.sync += x.eq(40)
                m.next = "A"
            fsm_states(m, [("A", a)])
        self.refuse(big_constant, "0..31")

        def unknown_signal(m, pl):
            s = Signal()

            def a():
                m.d.sync += s.eq(1)
                m.next = "A"
            fsm_states(m, [("A", a)])
        self.refuse(unknown_signal, "not a pin from platform.request")

        def input_assigned(m, pl):
            p = pl.buffer(m, "i", "gpio", 4)

            def a():
                m.d.sync += p.i.eq(1)
                m.next = "A"
            fsm_states(m, [("A", a)])
        self.refuse(input_assigned, "input pin")

        def pin_from_register(m, pl):
            p = pl.buffer(m, "o", "gpio", 0)
            x = pl.scratch("x")

            def a():
                m.d.sync += p.o.eq(x[0])
                m.next = "A"
            fsm_states(m, [("A", a)])
        self.refuse(pin_from_register, "can only be assigned a constant")

        def arithmetic(m, pl):
            x, y = pl.scratch("x"), pl.scratch("y")

            def a():
                m.d.sync += x.eq(y + 1)
                m.next = "A"
            fsm_states(m, [("A", a)])
        self.refuse(arithmetic, "not something one PIO instruction computes")

        def mov_from_unknown(m, pl):
            x = pl.scratch("x")
            s = Signal(32)

            def a():
                m.d.sync += x.eq(s)
                m.next = "A"
            fsm_states(m, [("A", a)])
        self.refuse(mov_from_unknown, "constant, the other scratch register")

        def part_assignment(m, pl):
            x = pl.scratch("x")

            def a():
                m.d.sync += x[0].eq(1)
                m.next = "A"
            fsm_states(m, [("A", a)])
        self.refuse(part_assignment, "parts of a signal")

        def nonconsecutive(m, pl):
            p = pl.buffer(m, "o", "odd", 0)

            def a():
                m.d.sync += p.o.eq(1)
                m.next = "A"
            fsm_states(m, [("A", a)])
        self.refuse(nonconsecutive, "not consecutive", extra=[Resource("odd", 0, Pins("0 2", dir="o"))])

        def too_wide_set(m, pl):
            p = pl.buffer(m, "o", "wide", 0)

            def a():
                m.d.sync += p.o.eq(1)
                m.next = "A"
            fsm_states(m, [("A", a)])
        self.refuse(too_wide_set, "at most 5", extra=[Resource("wide", 0, Pins("0 1 2 3 4 5", dir="o"))])

        def too_wide_side(m, pl):
            p = pl.buffer(m, "o", "wide", 0)
            pl.pio_role(p, "side")
            x = pl.scratch("x")

            def a():
                m.d.sync += [p.o.eq(1), x.eq(1)]
                m.next = "A"
            fsm_states(m, [("A", a)])
        self.refuse(too_wide_side, "at most 4", extra=[Resource("wide", 0, Pins("0 1 2 3 4", dir="o"))])

    def test_structure_that_is_not_one_fsm(self):
        self.refuse(lambda m, pl: m.d.comb.__iadd__([]), "exactly one m.FSM")

        def two_fsms(m, pl):
            with m.FSM(name="a"):
                with m.State("A"):
                    m.next = "A"
            with m.FSM(name="b"):
                with m.State("B"):
                    m.next = "B"
        self.refuse(two_fsms, "exactly one")

        def submodule(m, pl):
            m.submodules.s = Module()
            fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
        self.refuse(submodule, "submodules")

        def comb_logic(m, pl):
            s = Signal()
            m.d.comb += s.eq(1)
            fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
        self.refuse(comb_logic, "combinational logic")

        def free_running(m, pl):
            x = pl.scratch("x")
            m.d.sync += x.eq(x + 1)
            fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
        self.refuse(free_running, "outside the FSM")

        def other_domain(m, pl):
            from amaranth.hdl import ClockDomain
            m.domains.slow = ClockDomain()
            s = Signal()
            m.d.slow += s.eq(1)
            fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
        self.refuse(other_domain, "clock domain")

    def test_reset_values_and_size(self):
        def scratch_init(m, pl):
            pl._scratch["x"] = Signal(32, init=3, name="pio_x")
            fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
        self.refuse(scratch_init, "resets to 3")

        def pin_init(m, pl):
            p = pl.buffer(m, "o", "gpio", 0)
            p.o._init = 1
            fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
        self.refuse(pin_init, "resets to 1")

        def too_big(m, pl):
            x = pl.scratch("x")
            states = []
            for i in range(33):
                def s(i=i):
                    m.d.sync += x.eq(i % 2)
                    m.next = "S%d" % ((i + 1) % 33)
                states.append(("S%d" % i, s))
            fsm_states(m, states)
        self.refuse(too_big, "32 words")

    def test_chip_gating(self):
        def mov_pindirs_like(m, pl):                      # PIO version 1 only instructions are not generated yet; a wait on a low pin is fine on both
            p = pl.buffer(m, "i", "gpio", 4)

            def a():
                with m.If(~p.i):
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        for chip in (RP2040PioPlatform, RP2350PioPlatform):
            c = compile_body(mov_pindirs_like, chip=chip)
            self.assertIn("wait 0 gpio 4", c.program.pio_text())


# ----------------------------------------------------------------------------- the data path
def data_body(states, shifts=("right", "right"), pins=("o",), **config):
    """A design with a shifter, the scratch registers and (optionally) an output and an input pin group; `states` gets (m, sh, x, y, out, inp) and returns [(name, fn)].
    `config` goes to ``platform.shifter`` (autopull and the like)."""
    def body(m, pl):
        sh = pl.shifter(m, out_shift=shifts[0], in_shift=shifts[1], **config)
        x, y = pl.scratch("x"), pl.scratch("y")
        out = pl.buffer(m, "o", "gpio", 0)
        inp = pl.buffer(m, "i", "gpio", 8)
        fsm_states(m, states(m, sh, x, y, out, inp))
    return body


@support.cases
class DataPathRefusalTests(RefusalCase):
    def test_blocking_pull_must_continue_with_the_next_state(self):
        def states(m, sh, x, y, out, inp):
            def a():
                with sh.pull(m):
                    m.next = "C"
            return [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states), "blocking pull / push must continue with the next state")

    def test_two_data_operations_in_one_state(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.out(m, "x", 4)
                sh.in_(m, "y", 4)
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states), "do not match")

    def test_data_operation_and_register_or_pin_in_one_state(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.out(m, "null", 4)
                m.d.sync += y.eq(3)
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states), "needs two PIO instructions")

    def test_data_operation_and_jump_in_one_state(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.in_(m, "x", 4)
                m.next = "C"
            return [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states), "then jumps to another state")

    def test_data_operation_in_a_state_with_a_test(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.out(m, "null", 4)
                with m.If(x != 0):
                    m.next = "A"
                with m.Else():
                    m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states), "cannot be in a state with a test")

    def test_shifter_registers_without_an_operation(self):
        def states(m, sh, x, y, out, inp):
            def a():
                m.d.sync += sh.osr.eq(5)
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states), "without an operation")

    def test_fifo_flag_tested_by_hand(self):
        def states(m, sh, x, y, out, inp):
            def a():
                with m.If(sh.fifo.tx_ready):
                    m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states), "belongs to `pull block`")

    def test_fifo_flag_must_be_true_when_the_instruction_completes(self):
        def wrong(m, sh, x, y, out, inp):
            def a():
                with m.If(~sh.fifo.tx_ready):
                    with sh.pull(m):
                        m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(wrong), "more than one test")

    def test_push_uses_the_rx_flag_and_pull_the_tx_flag(self):
        def states(m, sh, x, y, out, inp):
            def a():
                with m.If(sh.fifo.rx_space):
                    with sh.pull(m, block=False):
                        m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states), "belongs to `push block`")

    def test_side_set_must_agree_while_waiting_and_completing(self):
        def body(m, pl):
            sh = pl.shifter(m)
            side = pl.buffer(m, "o", "gpio", 3)
            pl.pio_role(side, "side")

            def a():
                with sh.pull(m):
                    m.d.sync += side.o.eq(1)          # only when the word arrives: PIO applies side-set in the first cycle of the instruction
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        self.refuse(body, "side-set value differs between waiting and completing")

    def test_a_blocking_operation_needs_its_helper(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.out(m, "x", 4)
                m.d.sync += sh.op.eq(3)           # a different operation marker than the statements belong to
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states), "do not match")

    def test_two_out_pin_groups(self):
        def body(m, pl):
            sh = pl.shifter(m)
            a_, b_ = pl.buffer(m, "o", "gpio", 0), pl.buffer(m, "o", "gpio", 1)

            def s0():
                sh.out(m, a_)
                m.next = "S1"

            def s1():
                sh.out(m, b_)
                m.next = "S0"
            fsm_states(m, [("S0", s0), ("S1", s1)])
        self.refuse(body, "more than one pin group")

    def test_two_in_pin_groups(self):
        def body(m, pl):
            sh = pl.shifter(m)
            a_, b_ = pl.buffer(m, "i", "gpio", 0), pl.buffer(m, "i", "gpio", 1)

            def s0():
                sh.in_(m, a_)
                m.next = "S1"

            def s1():
                sh.in_(m, b_)
                m.next = "S0"
            fsm_states(m, [("S0", s0), ("S1", s1)])
        self.refuse(body, "reads 2 different pin groups")

    def test_helper_misuse_is_an_error_when_the_design_is_written(self):
        pl = RP2040PioPlatform()
        m = Module()
        m._MustUse__silence = True
        sh = pl.shifter(m)
        sh.fifo._MustUse__silence = True
        for bad in (0, 33, 2.5, "3"):
            with self.assertRaisesRegex(ValueError, "bit count"):
                sh.out(m, "x", bad)
        with self.assertRaisesRegex(ValueError, "bit count"):
            sh.in_(m, "null", 0)
        spare = Module()
        spare._MustUse__silence = True
        with self.assertRaisesRegex(ValueError, "already called"):
            pl.shifter(spare)
        with self.assertRaisesRegex(ValueError, "right"):
            RP2040PioPlatform().shifter(spare, out_shift="up")
        pin = RP2040PioPlatform()
        m2 = Module()
        m2._MustUse__silence = True
        p = pin.buffer(m2, "i", "gpio", 0)
        p._MustUse__silence = True
        s2 = pin.shifter(m2)
        s2.fifo._MustUse__silence = True
        with self.assertRaisesRegex(ValueError, "bit count"):
            s2.in_(m2, p, 2)           # a 1-bit input cannot supply two bits

    def test_the_fifo_model_is_the_only_submodule_allowed(self):
        from amaranth.hdl import Module as M

        def body(m, pl):
            pl.shifter(m)
            other = M()
            other._MustUse__silence = True
            m.submodules.other = other
            fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
        self.refuse(body, "submodules are not supported")

    # ----------------------------------------------------------------------------- the version gate and the FSM state names (python/pioweave/src/pioweave/_compat.py)
    def test_version_gate(self):
        from pioweave._compat import UnsupportedAmaranth, check_version
        for ok in ("0.5.0", "0.5.1", "0.5.9", "0.5.10", "0.6.0.dev140"):
            self.assertTrue(check_version(ok, allow=False))
        for bad in ("0.5.11", "0.4.5", "0.6.0", "0.6.0.dev141", "1.0.0", "nonsense", "0.5", "0.5.1rc1"):
            with self.assertRaisesRegex(UnsupportedAmaranth, "tested with Amaranth"):
                check_version(bad, allow=False)
            self.assertIs(check_version(bad, allow=True), False)     # PIOWEAVE_ALLOW_UNTESTED=1 lets it through (reported as untested)

    def test_fsm_state_names_for_both_ways_amaranth_stores_them(self):
        from pioweave._compat import fsm_state_names

        class Decoder:             # Amaranth 0.5.x: Signal.decoder is a function that fails for numbers that are not states
            decoder = staticmethod(lambda n: {0: "A/0", 1: "B/1"}[n])

            def __len__(self):
                return 2
        self.assertEqual(fsm_state_names(Decoder()), {0: "A/0", 1: "B/1"})

        class Format:
            _variants = {0: "IDLE/0", 1: "RUN/1"}

        class Enumerated:          # Amaranth 0.6 development: the names are in the format object of the signal
            decoder = None
            _format = Format()

            def __len__(self):
                return 2
        self.assertEqual(fsm_state_names(Enumerated()), {0: "IDLE/0", 1: "RUN/1"})
        self.assertIsNone(fsm_state_names(Signal(2)))
        self.assertIsNone(fsm_state_names(Signal(2, decoder=lambda n: "no slash")))


@support.cases
class AutoRefusalTests(RefusalCase):
    """Autopull and autopush (design note 2b): the instructions can stall, so the helpers must be written with `with`."""

    def test_out_with_autopull_needs_the_with_form(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.out(m, "x", 4)                  # a plain call: the state would not stall while the OSR waits for the TX FIFO
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states, autopull=True, pull_thresh=8), r"test_refusals\.py:\d+: this helper call can stall")

    def test_in_with_autopush_needs_the_with_form(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.in_(m, "x", 4)
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states, autopush=True, push_thresh=8), "this helper call can stall")

    def test_plain_calls_stay_valid_without_the_automatic_transfer(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.out(m, "x", 4)
                m.next = "B"

            def b():
                with sh.in_(m, "x", 4):              # the with form is valid without autopush too: the body always runs
                    m.next = "A"
            return [("A", a), ("B", b)]
        compile_body(data_body(states))

    def test_autopull_out_must_continue_with_the_next_state(self):
        def states(m, sh, x, y, out, inp):
            def a():
                with sh.out(m, "x", 4):
                    m.next = "C"
            return [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states, autopull=True), "must continue with the next state")

    def test_autopush_in_must_continue_with_the_next_state(self):
        def states(m, sh, x, y, out, inp):
            def a():
                with sh.in_(m, "y", 4):
                    m.next = "C"
            return [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states, autopush=True), "must continue with the next state")

    def test_a_waiting_state_cannot_change_registers(self):
        def states(m, sh, x, y, out, inp):
            def a():
                with sh.out(m, "null", 4):
                    m.d.sync += y.eq(3)
                    m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states, autopull=True), "cannot change registers")

    def test_a_test_around_a_stalling_helper_is_a_second_test(self):
        def states(m, sh, x, y, out, inp):
            def a():
                with m.If(x != 0):
                    with sh.in_(m, "null", 4):
                        m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states, autopush=True), "more than one test")

    def test_side_set_must_agree_while_the_out_waits(self):
        def body(m, pl):
            sh = pl.shifter(m, autopull=True)
            side = pl.buffer(m, "o", "gpio", 3)
            pl.pio_role(side, "side")

            def a():
                with sh.out(m, "null", 4):
                    m.d.sync += side.o.eq(1)
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        self.refuse(body, "side-set value differs between waiting and completing")

    def test_a_flag_of_the_data_path_belongs_to_its_operation(self):
        def states(m, sh, x, y, out, inp):
            def a():
                with sh.out(m, "x", 4):
                    m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        body = data_body(states, autopull=True)

        def wrong(m, pl):                          # a hand-written test of the out flag around a pull
            sh = pl.shifter(m, autopull=True)
            flag = sh._flag(("out_go",), sh.osr_n < sh.pull_thresh, (5, 6, 7, 8), "body")

            def a():
                with m.If(flag):
                    with sh.pull(m, block=False):
                        m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        compile_body(body)
        self.refuse(wrong, "belongs to `out pins")

    def test_threshold_and_flag_arguments_are_checked_when_the_design_is_written(self):
        for bad in (0, 33, 2.5, "8", True, None):
            for key in ("pull_thresh", "push_thresh"):
                with self.assertRaisesRegex(ValueError, "from 1 to 32"):
                    RP2040PioPlatform().shifter(spare_module(), **{key: bad})


@support.cases
class ExtendedInstructionRefusalTests(RefusalCase):
    """pull ifempty, push iffull, mov with the OSR / ISR / pins / status, out isr, in isr / osr, jmp !osre (design note 2c)."""

    def test_unsupported_destinations_are_errors_when_the_design_is_written(self):
        m, sh = shifter_for_misuse(RP2350PioPlatform())
        for dest, why in (("pindirs", "no pin direction state"), ("pc", "computed jump"), ("exec", "executed instruction")):
            with self.assertRaisesRegex(ValueError, "out %s is not supported.*%s" % (dest, why)):
                sh.out(m, dest, 4)
            with self.assertRaisesRegex(ValueError, "mov %s is not supported.*%s" % (dest, why)):
                sh.mov(m, dest, "x")
        with self.assertRaisesRegex(ValueError, "destination of out"):
            sh.out(m, "z", 1)
        with self.assertRaisesRegex(ValueError, "source of in"):
            sh.in_(m, "pins", 1)
        with self.assertRaisesRegex(ValueError, "destination of mov"):
            sh.mov(m, "null", "x")
        with self.assertRaisesRegex(ValueError, "source of mov"):
            sh.mov(m, "x", "z")
        with self.assertRaisesRegex(ValueError, 'op is "none"'):
            sh.mov(m, "x", "y", "negate")

    def test_mov_status_needs_its_configuration(self):
        m, sh = shifter_for_misuse(RP2040PioPlatform())
        with self.assertRaisesRegex(ValueError, "mov status needs platform.shifter"):
            sh.mov(m, "x", "status")
        for bad in ("both", 3):
            with self.assertRaisesRegex(ValueError, "status is None"):
                RP2040PioPlatform().shifter(spare_module(), status=bad)
        with self.assertRaisesRegex(ValueError, 'status="irq" needs PIO version 1'):
            RP2040PioPlatform().shifter(spare_module(), status="irq")
        with self.assertRaisesRegex(ValueError, "status_n must be an integer from 0 to 7"):
            RP2350PioPlatform().shifter(spare_module(), status="irq", status_n=8)
        for bad in (-1, 16, 2.5, True):
            with self.assertRaisesRegex(ValueError, "status_n"):
                RP2040PioPlatform().shifter(spare_module(), status="tx", status_n=bad)

    def test_mov_from_pins_needs_pio_version_1(self):
        def body(m, pl):
            sh = pl.shifter(m)
            sh.fifo._MustUse__silence = True              # the ValueError below ends the elaboration, so nothing uses these
            m._MustUse__silence = True
            inp = pl.buffer(m, "i", "gpio", 4)
            inp._MustUse__silence = True

            def a():
                sh.mov(m, "x", inp)
                m.next = "A"
            fsm_states(m, [("A", a)])
        with self.assertRaisesRegex(ValueError, "PIO version 0"):
            compile_body(body, chip=RP2040PioPlatform)
        c = compile_body(body, chip=RP2350PioPlatform)
        self.assertIn("mov x, pins", c.program.pio_text())

    def test_jmp_not_osre_cannot_jump_while_the_osr_is_empty(self):
        def states(m, sh, x, y, out, inp):
            def a():
                with m.If(~sh.osr_not_empty):
                    m.next = "C"
                with m.Else():
                    m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states), "can jump while the OSR is not empty")

    def test_jmp_not_osre_does_not_decrement(self):
        def states(m, sh, x, y, out, inp):
            def a():
                m.d.sync += x.eq(x - 1)
                with m.If(sh.osr_not_empty):
                    m.next = "C"
                with m.Else():
                    m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states), "cannot change a register")

    def test_a_blocking_pull_ifempty_must_continue_with_the_next_state(self):
        def states(m, sh, x, y, out, inp):
            def a():
                with sh.pull(m, ifempty=True):
                    m.next = "C"
            return [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states), "must continue with the next state")

    def test_a_blocking_push_iffull_needs_with(self):
        def states(m, sh, x, y, out, inp):
            def a():
                with sh.push(m, iffull=True):
                    pass
                m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(data_body(states), "(belongs to|stalls while|must stay in the state)")

    def test_a_mov_and_another_operation_in_one_state(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.mov(m, "osr", "x")
                m.d.sync += y.eq(3)
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states), "needs two PIO instructions")

        def twice(m, sh, x, y, out, inp):
            def a():
                sh.mov(m, "osr", "x")
                sh.mov(m, "isr", "y")
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(twice), "do not match")

    def test_a_hand_written_operand_code_is_refused(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.mov(m, "x", "y")
                m.d.sync += sh.arg.eq(99)
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states), "operand code of mov")


@support.cases
class IrqRefusalTests(RefusalCase):
    """IRQ flags (design note 2d)."""

    def test_flag_numbers_modes_and_polarities_are_checked_when_the_design_is_written(self):
        m, sh = shifter_for_misuse(RP2040PioPlatform())
        for bad in (-1, 8, 2.5, "1", True):
            with self.assertRaisesRegex(ValueError, "from 0 to 7"):
                sh.irq(m, bad)
            with self.assertRaisesRegex(ValueError, "from 0 to 7"):
                sh.wait_irq(m, bad)
        with self.assertRaisesRegex(ValueError, 'mode of irq is "set"'):
            sh.irq(m, 1, "pulse")
        with self.assertRaisesRegex(ValueError, "polarity of wait irq"):
            sh.wait_irq(m, 1, polarity=2)
        sh.irq_model._MustUse__silence = True

    def test_irq_wait_needs_the_with_form(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.irq(m, 3, "wait")
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states), "this helper call can stall")

    def test_wait_irq_needs_the_with_form(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.wait_irq(m, 3)
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states), "this helper call can stall")

    def test_blocking_pull_and_push_without_with_are_refused_too(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.pull(m)
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states), "this helper call can stall")

    def test_irq_wait_and_wait_irq_must_continue_with_the_next_state(self):
        for make in (lambda m, sh: sh.irq(m, 3, "wait"), lambda m, sh: sh.wait_irq(m, 3)):
            def states(m, sh, x, y, out, inp, make=make):
                def a():
                    with make(m, sh):
                        m.next = "C"
                return [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))]
            self.refuse(data_body(states), "must continue with the next state")

    def test_irq_and_another_operation_in_one_state(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.irq(m, 1)
                m.d.sync += x.eq(2)
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states), "needs two PIO instructions")

        def two(m, sh, x, y, out, inp):
            def a():
                sh.irq(m, 1)
                sh.irq(m, 2, "clear")
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(two), "do not match")

    def test_a_hand_written_flag_toggle_is_refused(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.irq(m, 1)
                m.d.sync += sh.irq_model.clr_tog[1].eq(1)
                m.next = "A"
            return [("A", a)]
        self.refuse(data_body(states), "do not match")

    def test_wait_irq_flag_must_be_tested_through_its_helper(self):
        def states(m, sh, x, y, out, inp):
            def a():
                sh.irq(m, 1)
                m.next = "B"

            def b():
                with m.If(sh.irq_model.flag[1]):          # a hand-written test of the flag
                    m.next = "A"
            return [("A", a), ("B", b)]
        self.refuse(data_body(states), "the test uses signal")

    def test_a_side_set_value_must_agree_while_irq_wait_stalls(self):
        def body(m, pl):
            sh = pl.shifter(m)
            side = pl.buffer(m, "o", "gpio", 3)
            pl.pio_role(side, "side")

            def a():
                with sh.irq(m, 2, "wait"):
                    m.d.sync += side.o.eq(1)
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        self.refuse(body, "side-set value differs between waiting and completing")


@support.cases
class WaitPinAndJoinRefusalTests(RefusalCase):
    """`wait pin` on one bit of an input group and joined FIFOs (design note 2e)."""

    def bus(self, m, pl, n=3, name="bus", base=4):
        from amaranth.build import Pins, Resource
        pl.add_resources([Resource(name, 0, Pins(" ".join(str(base + k) for k in range(n)), dir="i"))])
        return pl.buffer(m, "i", name, 0)

    def test_a_bit_of_an_input_group_can_only_be_waited_for(self):
        def body(m, pl):
            bus = self.bus(m, pl)

            def a():
                with m.If(bus.i[1]):
                    m.next = "C"
                with m.Else():
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))])
        self.refuse(body, "can only be a wait")

    def test_wait_pin_must_continue_with_the_next_state(self):
        def body(m, pl):
            bus = self.bus(m, pl)

            def a():
                with m.If(bus.i[1]):
                    m.next = "C"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))])
        self.refuse(body, "must continue with the next state")

    def test_wait_pin_and_in_pins_share_one_group(self):
        def body(m, pl):
            bus = self.bus(m, pl)
            other = self.bus(m, pl, 2, "other", 10)
            sh = pl.shifter(m)

            def a():
                with m.If(bus.i[1]):
                    m.next = "B"

            def b():
                sh.in_(m, other, 2)
                m.next = "A"
            fsm_states(m, [("A", a), ("B", b)])
        self.refuse(body, "reads 2 different pin groups")

    def test_a_bit_of_something_that_is_not_an_input_pin(self):
        def body(m, pl):
            x = pl.scratch("x")

            def a():
                with m.If(x[3]):
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        self.refuse(body, "not an input pin")

    def test_compound_tests_on_bits_are_still_refused(self):
        def body(m, pl):
            bus = self.bus(m, pl)

            def a():
                with m.If(bus.i[0] & bus.i[1]):
                    m.next = "B"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        self.refuse(body, "cannot be done by one PIO instruction")

    def test_fifo_join_values(self):
        for bad in ("both", "txrx", None, 4):
            with self.assertRaisesRegex(ValueError, "fifo_join is"):
                RP2040PioPlatform().shifter(spare_module(), fifo_join=bad)
