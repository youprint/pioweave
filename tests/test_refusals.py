# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""What the Amaranth-to-PIO compiler refuses, and why each design below is outside the subset because mapping it would change its cycle
behaviour or needs hardware PIO does not have. Every refusal must name the reason; none may be approximated."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from amaranth.build import Pins, Resource  # noqa: E402
from amaranth.hdl import Elaboratable, Module, Signal  # noqa: E402

from pioweave.block import Block, BlockError, BlockSim, Machine, block_run, emulate_block  # noqa: E402
from pioweave.compile import PioCompileError, compile_design  # noqa: E402
from pioweave.platform import PLATFORMS, RP2040PioPlatform, RP2350BPioPlatform, RP2350PioPlatform  # noqa: E402

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

    def test_side_set_must_agree_in_both_branches_of_a_test(self):
        # COR-44: one PIO instruction applies one side-set value, so the two branches of a test (jmp pin) cannot drive the side-set pin differently
        def body(m, pl):
            side = pl.buffer(m, "o", "gpio", 3)
            pl.pio_role(side, "side")
            pin = pl.buffer(m, "i", "gpio", 4)

            def a():
                with m.If(pin.i):
                    m.d.sync += side.o.eq(1)
                    m.next = "B"
                with m.Else():
                    m.d.sync += side.o.eq(0)
                    m.next = "A"
            fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
        self.refuse(body, "side-set value differs between the two branches of the test")

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

    # ----------------------------------------------------------------------------- the version gate and the FSM state names (pioweave/_compat.py)
    def test_version_gate(self):
        from pioweave._compat import UnsupportedAmaranth, check_version
        for ok in ("0.5.1", "0.5.9", "0.5.10", "0.6.0.dev140"):
            self.assertTrue(check_version(ok, allow=False))
        for bad in ("0.5.0", "0.5.11", "0.4.5", "0.6.0", "0.6.0.dev141", "1.0.0", "nonsense", "0.5", "0.5.1rc1"):
            with self.assertRaisesRegex(UnsupportedAmaranth, "tested with Amaranth"):
                check_version(bad, allow=False)
            with self.assertWarns(UserWarning):                     # the override lets it through with an UntestedAmaranthWarning (audit S-13)
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
        for dest, why in (("pc", "computed jump"), ("exec", "executed instruction")):
            with self.assertRaisesRegex(ValueError, "out %s is not supported.*%s" % (dest, why)):
                sh.out(m, dest, 4)
            with self.assertRaisesRegex(ValueError, "mov %s is not supported.*%s" % (dest, why)):
                sh.mov(m, dest, "x")
        with self.assertRaisesRegex(ValueError, "out pindirs needs the direction register of a pin group as the destination, not the string.*platform.directions"):
            sh.out(m, "pindirs", 4)
        with self.assertRaisesRegex(ValueError, "mov pindirs needs the direction register of a pin group as the destination, not the string.*platform.directions"):
            sh.mov(m, "pindirs", "x")
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


def dir_body(states, chip=RP2350PioPlatform, shifter=True, init=0, **config):
    """A design with a bidirectional bus (GPIO 4 to 6, `bus`, with a direction register), a second one (GPIO 10, `aux`, also with a register), an output (GPIO 0) and an input
    (GPIO 8); `states` gets (m, sh, bus, d, aux, da, out, inp, x) and returns [(name, fn)]."""
    def body(m, pl):
        pl.add_resources([Resource("bus", 0, Pins("4 5 6", dir="io")), Resource("aux", 0, Pins("10", dir="io"))])
        sh = pl.shifter(m, **config) if shifter else None
        bus = pl.buffer(m, "io", "bus", 0)
        d = pl.directions(bus, init=init)
        aux = pl.buffer(m, "io", "aux", 0)
        da = pl.directions(aux)
        out = pl.buffer(m, "o", "gpio", 0)
        inp = pl.buffer(m, "i", "gpio", 8)
        x = pl.scratch("x")
        fsm_states(m, states(m, sh, bus, d, aux, da, out, inp, x))
    return body


@support.cases
class PinDirectionRefusalTests(RefusalCase):
    """Pin directions (design note 2f): what the helpers and the compiler refuse."""

    def test_the_direction_register_is_checked_when_the_design_is_written(self):
        pl = RP2350PioPlatform()
        pl.add_resources([Resource("bus", 0, Pins("4 5 6", dir="io"))])
        m = spare_module()
        bus = pl.buffer(m, "io", "bus", 0)
        bus._MustUse__silence = True
        for bad in (8, -1, 2.5, True, "1"):
            with self.assertRaisesRegex(ValueError, "initial direction of 3 pin"):
                pl.directions(bus, init=bad)
        d = pl.directions(bus, init=5)
        self.assertIs(pl.directions(bus, init=5), d)
        with self.assertRaisesRegex(ValueError, "already exists with init 5, not 2"):
            pl.directions(bus, init=2)
        self.assertEqual((len(d), d.init), (3, 5))
        inp = pl.buffer(m, "i", "gpio", 8)
        inp._MustUse__silence = True
        with self.assertRaisesRegex(ValueError, 'needs a buffer that can drive the pins'):
            pl.directions(inp)
        with self.assertRaisesRegex(ValueError, "was not requested from this platform"):
            pl.directions(object())

    def test_out_and_mov_need_the_register_of_a_group_of_this_platform(self):
        m, sh = shifter_for_misuse(RP2350PioPlatform())
        with self.assertRaisesRegex(ValueError, "out pindirs needs the direction register"):
            sh.out(m, Signal(3), 3)
        with self.assertRaisesRegex(ValueError, "mov pindirs needs the direction register"):
            sh.mov(m, Signal(3), "x")

    def test_mov_pindirs_needs_pio_version_1(self):
        pl = RP2040PioPlatform()
        pl.add_resources([Resource("bus", 0, Pins("4 5 6", dir="io"))])
        m, sh = shifter_for_misuse(pl)
        bus = pl.buffer(m, "io", "bus", 0)
        bus._MustUse__silence = True
        d = pl.directions(bus)
        with self.assertRaisesRegex(ValueError, "mov pindirs needs PIO version 1.*out pindirs or set pindirs"):
            sh.mov(m, d, "x")
        sh.out(m, d, 3)           # out pindirs exists on both versions

    def test_set_pindirs_cannot_be_followed_by_a_jump(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                m.d.sync += d.eq(3)
                m.next = "C"                           # skips B: the jump would be a second instruction
            return [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))]
        self.refuse(dir_body(states, shifter=False), "changes pin directions and then jumps to another state needs two PIO instructions")

    def test_a_level_assigned_with_set_pindirs_is_the_side_set(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                m.d.sync += [d.eq(3), out.o.eq(1)]
                m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        c = compile_body(dir_body(states, shifter=False), chip=RP2350PioPlatform)
        self.assertIn("set pindirs, 3 side 1", c.program.pio_text())

    def test_set_pins_and_set_pindirs_must_address_one_group(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                m.d.sync += out.o.eq(1)               # set pins on GPIO 0
                m.next = "B"

            def b():
                m.d.sync += d.eq(2)                    # set pindirs on the bus: another SET group
                m.next = "A"
            return [("A", a), ("B", b)]
        self.refuse(dir_body(states, shifter=False), "`set pins` drives gpio_0 but `set pindirs` changes bus_0; PIO has one SET group")

    def test_set_pindirs_on_two_groups(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                m.d.sync += d.eq(2)
                m.next = "B"

            def b():
                m.d.sync += da.eq(1)
                m.next = "A"
            return [("A", a), ("B", b)]
        self.refuse(dir_body(states, shifter=False), "`set pindirs` is used on more than one pin group")

    def test_the_side_set_writes_levels_or_directions_not_both(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                m.d.sync += [d.eq(2), x.eq(1)]         # directions as the side-set of `set x`
                m.next = "B"

            def b():
                m.d.sync += out.o.eq(1)
                m.d.sync += x.eq(2)                    # a level as the side-set
                m.next = "A"
            return [("A", a), ("B", b)]
        self.refuse(dir_body(states, shifter=False), "writes either pin levels .* or pin directions .*, not both")

    def test_the_side_set_directions_of_two_groups(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                m.d.sync += [d.eq(2), x.eq(1)]
                m.next = "B"

            def b():
                m.d.sync += [da.eq(1), x.eq(2)]
                m.next = "A"
            return [("A", a), ("B", b)]
        self.refuse(dir_body(states, shifter=False), "writes the directions of more than one pin group")

    def test_directions_of_two_groups_in_one_state(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                m.d.sync += [d.eq(2), da.eq(1)]
                m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(dir_body(states, shifter=False), "directions of two pin groups needs two PIO instructions|more than one pin group")

    def test_a_direction_register_takes_constants_or_the_helper_bits_only(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                m.d.sync += d.eq(x[:3])
                m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(dir_body(states, shifter=False), "direction register of bus_0 can only be assigned a constant")

    def test_the_direction_register_cannot_be_tested_or_sliced(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                with m.If(d[0]):
                    m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(dir_body(states, shifter=False), "not an input pin")

        def states2(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                m.d.sync += d[0].eq(1)
                m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(dir_body(states2, shifter=False), "parts of a signal")

    def test_out_pindirs_and_out_pins_share_one_out_group(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                sh.out(m, d, 3)
                m.next = "B"

            def b():
                sh.out(m, out, 1)                      # out pins on another group
                m.next = "A"
            return [("A", a), ("B", b)]
        self.refuse(dir_body(states), "`out pins` drives more than one pin group")

    def test_out_pindirs_cannot_share_a_state_with_another_operation(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                sh.out(m, d, 3)
                m.d.sync += x.eq(1)
                m.next = "B"
            return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
        self.refuse(dir_body(states), "statements on the shifter registers|needs two PIO instructions")

    def test_a_state_that_stalls_needs_the_with_form_for_out_pindirs_with_autopull(self):
        def states(m, sh, bus, d, aux, da, out, inp, x):
            def a():
                sh.out(m, d, 3)
                m.next = "A"
            return [("A", a)]
        self.refuse(dir_body(states, autopull=True), "this helper call can stall")

    def test_pc_and_exec_stay_refused(self):
        m, sh = shifter_for_misuse(RP2350PioPlatform())
        for dest in ("pc", "exec"):
            with self.assertRaises(ValueError):
                sh.out(m, dest, 4)
            with self.assertRaises(ValueError):
                sh.mov(m, dest, "x")

    def test_a_group_with_a_register_must_fit_the_gpio_window(self):
        def body(m, pl):
            pl.add_resources([Resource("far", 0, Pins("40", dir="io"))])
            far = pl.buffer(m, "io", "far", 0)
            pl.directions(far)
            out = pl.buffer(m, "o", "gpio", 2)
            fsm_states(m, [("A", lambda: (m.d.sync.__iadd__(out.o.eq(1)), setattr(m, "next", "A")))])
        self.refuse(body, "do not fit one 32-GPIO window", chip=PLATFORMS["rp2350b"])


# ----------------------------------------------------------------------------- blocks with several state machines (pioweave/block.py)
class _Long(Elaboratable):
    """A design of `n` states that each load X with a different value, so none is folded into a delay: `n` instructions."""

    def __init__(self, n=2, gpio=0):
        self._MustUse__silence = True
        self.n, self.gpio = n, gpio

    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", self.gpio)
        x = platform.scratch("x")

        def state(i):
            def f():
                m.d.sync += x.eq(i % 7) if i else pin.o.eq(1)
                m.next = "S%d" % ((i + 1) % self.n)
            return f
        fsm_states(m, [("S%d" % i, state(i)) for i in range(self.n)])
        return m


class _Rel(Elaboratable):
    def __init__(self):
        self._MustUse__silence = True

    def elaborate(self, platform):
        m = Module()
        sh = platform.shifter(m)
        fsm_states(m, [("A", lambda: (sh.irq(m, 1, "set", rel=True), setattr(m, "next", "A")))])
        return m


class _TwoOps(Elaboratable):
    def __init__(self):
        self._MustUse__silence = True

    def elaborate(self, platform):
        m = Module()
        x, y = platform.scratch("x"), platform.scratch("y")
        fsm_states(m, [("A", lambda: (m.d.sync.__iadd__([x.eq(1), y.eq(2)]), setattr(m, "next", "A")))])
        return m


class BlockRefusalTests(support.TestCase):
    def refuse(self, block, match):
        with self.assertRaisesRegex(BlockError, match):
            block.compile()

    def test_more_than_four_machines(self):
        with self.assertRaisesRegex(BlockError, "has 4 state machines; 5 were given"):
            Block([Machine("m%d" % k, lambda k=k: _Long(2, k)) for k in range(5)])

    def test_no_machine_at_all(self):
        with self.assertRaisesRegex(BlockError, "at least one state machine"):
            Block([])

    def test_machine_names_must_be_different_identifiers(self):
        with self.assertRaisesRegex(BlockError, "must be different.*a"):
            Block([Machine("a", _Long), Machine("b", _Long), Machine("a", _Long)])
        for bad in ("", "1a", "a-b", "a b", 3):
            with self.assertRaisesRegex(BlockError, "C identifier"):
                Machine(bad, _Long)

    def test_a_design_instance_is_refused_because_it_is_elaborated_more_than_once(self):
        with self.assertRaisesRegex(BlockError, "not an instance"):
            Machine("a", _Long(2))

    def test_only_machines_are_accepted(self):
        with self.assertRaisesRegex(BlockError, "Machine"):
            Block([("a", _Long)])

    def test_the_programs_together_must_fit_the_32_word_memory(self):
        block = Block([Machine("a", lambda: _Long(12)), Machine("b", lambda: _Long(12, 1)), Machine("c", lambda: _Long(12, 2))])
        self.refuse(block, r"36 instructions together \(a 12, b 12, c 12\) but the instruction memory of a PIO block has 32 words")
        Block([Machine("a", lambda: _Long(12)), Machine("b", lambda: _Long(12, 1)), Machine("c", lambda: _Long(8, 2))]).compile()        # 32 is allowed

    def test_an_error_in_one_design_names_the_machine(self):
        block = Block([Machine("fine", _Long), Machine("broken", _TwoOps)])
        self.refuse(block, "^machine broken: ")

    def test_irq_rel_is_refused_because_the_flag_depends_on_the_machine_number(self):
        self.refuse(Block([Machine("a", _Long), Machine("b", _Rel)]), "machine b uses `irq ... rel`")

    def test_machines_that_need_different_gpio_windows(self):
        from pioweave.designs import HighPins, SquareWave
        self.refuse(Block([Machine("high", HighPins), Machine("low", SquareWave)], RP2350BPioPlatform), r"different GPIO windows.*machine high fits only GPIO base 16.*machine low fits only GPIO base 0")

    def test_a_machine_whose_own_pins_do_not_fit_a_window(self):
        from pioweave.designs import SquareWave

        class Far(Elaboratable):
            def __init__(self):
                self._MustUse__silence = True

            def elaborate(self, platform):
                m = Module()
                a, b = platform.buffer(m, "o", "gpio", 5), platform.buffer(m, "o", "gpio", 40)
                fsm_states(m, [("A", lambda: (m.d.sync.__iadd__([a.o.eq(1), b.o.eq(1)]), setattr(m, "next", "A")))])
                return m
        self.refuse(Block([Machine("ok", SquareWave), Machine("far", Far)], RP2350BPioPlatform), "machine far: ")

    def test_a_shared_gpio_written_by_a_stalling_instruction(self):
        from pioweave.designs import SquareWave, UartTx
        # UartTx drives GPIO 0 by side-set in the `pull block` state, which stalls; SquareWave also writes GPIO 0
        self.refuse(Block([Machine("tx", UartTx), Machine("sq", SquareWave)]),
                    r"GPIO 0 is written by machines tx and sq; machine tx writes it in state PULL with an instruction that can stall")
        # the same pin with set pins only (no stall) is allowed, and so is an UartTx on a pin of its own
        Block([Machine("sq", SquareWave), Machine("sq2", SquareWave)]).compile()

    def test_the_simulation_names_machines_that_exist(self):
        from pioweave.designs import SquareWave
        block = Block([Machine("sq", SquareWave)])
        for key in ("tx", "tx_feed", "drain"):
            with self.assertRaisesRegex(BlockError, "BlockSim.%s names a machine that is not in the block: nobody" % key):
                block_run(block, BlockSim(**{key: {"nobody": ()}}))
        with self.assertRaisesRegex(BlockError, "BlockSim.tx names a machine"):
            emulate_block(block.compile(), BlockSim(tx={"nobody": ()}))

    def test_one_clock_for_the_block_and_no_block_without_a_name_to_build(self):
        from pioweave.designs import SquareWave
        with self.assertRaisesRegex(BlockError, "give the project name"):
            Block([Machine("sq", SquareWave)]).build()
