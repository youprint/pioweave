# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""What the Amaranth-to-PIO compiler refuses, and why each design below is outside the subset because mapping it would change its cycle
behaviour or needs hardware PIO does not have. Every refusal must name the reason; none may be approximated."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))

from amaranth.build import Pins, Resource  # noqa: E402
from amaranth.hdl import Elaboratable, Module, Signal  # noqa: E402

from pioweave.compile import PioCompileError, compile_design  # noqa: E402
from pioweave.platform import RP2040PioPlatform, RP2350PioPlatform  # noqa: E402


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


def refuse(body, match, **kw):
    with pytest.raises(PioCompileError, match=match):
        compile_body(body, **kw)


def test_two_register_operations_in_one_state():
    def body(m, pl):
        x, y = pl.scratch("x"), pl.scratch("y")

        def s0():
            m.d.sync += [x.eq(1), y.eq(2)]
            m.next = "A"
        fsm_states(m, [("A", s0)])
    refuse(body, "two register operations")


def test_set_pins_and_register_in_one_state():
    def body(m, pl):
        pin = pl.buffer(m, "o", "gpio", 0)
        pl.pio_role(pin, "set")
        x = pl.scratch("x")

        def s0():
            m.d.sync += [pin.o.eq(1), x.eq(1)]
            m.next = "A"
        fsm_states(m, [("A", s0)])
    refuse(body, "sets pins and also changes a register")


def test_pin_change_together_with_a_jump():
    def body(m, pl):
        pin = pl.buffer(m, "o", "gpio", 0)
        pl.pio_role(pin, "set")

        def a():
            m.d.sync += pin.o.eq(1)
            m.next = "C"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
    refuse(body, "changes a pin and then jumps")


def test_tests_that_pio_cannot_do():
    def cmp_const(m, pl):
        x = pl.scratch("x")

        def a():
            with m.If(x == 5):
                m.next = "C"
            with m.Else():
                m.next = "B"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
    refuse(cmp_const, "compare X or Y with 0")

    def compound(m, pl):
        p, q = pl.buffer(m, "i", "gpio", 4), pl.buffer(m, "i", "gpio", 5)

        def a():
            with m.If(p.i & q.i):
                m.next = "C"
            with m.Else():
                m.next = "B"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
    refuse(compound, "one PIO instruction")

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
    refuse(elif_chain, "more than one test")

    def other_signal(m, pl):
        s = Signal()

        def a():
            with m.If(s):
                m.next = "C"
            with m.Else():
                m.next = "B"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
    refuse(other_signal, "not an input pin or a scratch register")

    def low_pin_jump(m, pl):
        p = pl.buffer(m, "i", "gpio", 4)

        def a():
            with m.If(~p.i):
                m.next = "C"
            with m.Else():
                m.next = "B"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
    refuse(low_pin_jump, "high but not on one that is low")

    def x_eq_y(m, pl):
        x, y = pl.scratch("x"), pl.scratch("y")

        def a():
            with m.If(x == y):
                m.next = "C"
            with m.Else():
                m.next = "B"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
    refuse(x_eq_y, "no PIO jump")

    def both_branches_jump(m, pl):
        x = pl.scratch("x")

        def a():
            with m.If(x == 0):
                m.next = "C"
            with m.Else():
                m.next = "A"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
    refuse(both_branches_jump, "continue with the next state on one branch")

    def multibit_switch(m, pl):
        x = pl.scratch("x")

        def a():
            with m.Switch(x[0:2]):
                with m.Case(1):
                    m.next = "C"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
    refuse(multibit_switch, "multi-bit")


def test_decrement_rules():
    def nonzero_without_decrement(m, pl):
        x = pl.scratch("x")

        def a():
            with m.If(x != 0):
                m.next = "A"
            with m.Else():
                m.next = "B"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
    refuse(nonzero_without_decrement, "always decrements")

    def decrement_in_one_branch(m, pl):
        x = pl.scratch("x")

        def a():
            with m.If(x != 0):
                m.d.sync += x.eq(x - 1)
                m.next = "A"
            with m.Else():
                m.next = "B"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
    refuse(decrement_in_one_branch, "register changes differ")

    def decrement_with_zero_test(m, pl):
        x = pl.scratch("x")

        def a():
            m.d.sync += x.eq(x - 1)
            with m.If(x == 0):
                m.next = "A"
            with m.Else():
                m.next = "B"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
    refuse(decrement_with_zero_test, "does not decrement")


def test_wait_rules():
    def wait_with_register(m, pl):
        p = pl.buffer(m, "i", "gpio", 4)
        x = pl.scratch("x")

        def a():
            m.d.sync += x.eq(1)
            with m.If(p.i):
                m.next = "B"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
    refuse(wait_with_register, "cannot change registers")

    def wait_not_to_next(m, pl):
        p = pl.buffer(m, "i", "gpio", 4)

        def a():
            with m.If(p.i):
                m.next = "C"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A")), ("C", lambda: setattr(m, "next", "A"))])
    refuse(wait_not_to_next, "continue with the next state")


def test_assignments_that_need_hardware_pio_does_not_have():
    def two_set_pins(m, pl):
        a, b = pl.buffer(m, "o", "gpio", 0), pl.buffer(m, "o", "gpio", 1)

        def s0():
            m.d.sync += a.o.eq(1)
            m.next = "B"

        def s1():
            m.d.sync += b.o.eq(1)
            m.next = "A"
        fsm_states(m, [("A", s0), ("B", s1)])
    refuse(two_set_pins, "more than one output signal is driven by `set pins`")

    def big_constant(m, pl):
        x = pl.scratch("x")

        def a():
            m.d.sync += x.eq(40)
            m.next = "A"
        fsm_states(m, [("A", a)])
    refuse(big_constant, "0..31")

    def unknown_signal(m, pl):
        s = Signal()

        def a():
            m.d.sync += s.eq(1)
            m.next = "A"
        fsm_states(m, [("A", a)])
    refuse(unknown_signal, "not a pin from platform.request")

    def input_assigned(m, pl):
        p = pl.buffer(m, "i", "gpio", 4)

        def a():
            m.d.sync += p.i.eq(1)
            m.next = "A"
        fsm_states(m, [("A", a)])
    refuse(input_assigned, "input pin")

    def pin_from_register(m, pl):
        p = pl.buffer(m, "o", "gpio", 0)
        x = pl.scratch("x")

        def a():
            m.d.sync += p.o.eq(x[0])
            m.next = "A"
        fsm_states(m, [("A", a)])
    refuse(pin_from_register, "can only be assigned a constant")

    def arithmetic(m, pl):
        x, y = pl.scratch("x"), pl.scratch("y")

        def a():
            m.d.sync += x.eq(y + 1)
            m.next = "A"
        fsm_states(m, [("A", a)])
    refuse(arithmetic, "not something one PIO instruction computes")

    def mov_from_unknown(m, pl):
        x = pl.scratch("x")
        s = Signal(32)

        def a():
            m.d.sync += x.eq(s)
            m.next = "A"
        fsm_states(m, [("A", a)])
    refuse(mov_from_unknown, "constant, the other scratch register")

    def part_assignment(m, pl):
        x = pl.scratch("x")

        def a():
            m.d.sync += x[0].eq(1)
            m.next = "A"
        fsm_states(m, [("A", a)])
    refuse(part_assignment, "parts of a signal")

    def nonconsecutive(m, pl):
        p = pl.buffer(m, "o", "odd", 0)

        def a():
            m.d.sync += p.o.eq(1)
            m.next = "A"
        fsm_states(m, [("A", a)])
    refuse(nonconsecutive, "not consecutive", extra=[Resource("odd", 0, Pins("0 2", dir="o"))])

    def too_wide_set(m, pl):
        p = pl.buffer(m, "o", "wide", 0)

        def a():
            m.d.sync += p.o.eq(1)
            m.next = "A"
        fsm_states(m, [("A", a)])
    refuse(too_wide_set, "at most 5", extra=[Resource("wide", 0, Pins("0 1 2 3 4 5", dir="o"))])

    def too_wide_side(m, pl):
        p = pl.buffer(m, "o", "wide", 0)
        pl.pio_role(p, "side")
        x = pl.scratch("x")

        def a():
            m.d.sync += [p.o.eq(1), x.eq(1)]
            m.next = "A"
        fsm_states(m, [("A", a)])
    refuse(too_wide_side, "at most 4", extra=[Resource("wide", 0, Pins("0 1 2 3 4", dir="o"))])


def test_structure_that_is_not_one_fsm():
    refuse(lambda m, pl: m.d.comb.__iadd__([]), "exactly one m.FSM")

    def two_fsms(m, pl):
        with m.FSM(name="a"):
            with m.State("A"):
                m.next = "A"
        with m.FSM(name="b"):
            with m.State("B"):
                m.next = "B"
    refuse(two_fsms, "exactly one")

    def submodule(m, pl):
        m.submodules.s = Module()
        fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
    refuse(submodule, "submodules")

    def comb_logic(m, pl):
        s = Signal()
        m.d.comb += s.eq(1)
        fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
    refuse(comb_logic, "combinational logic")

    def free_running(m, pl):
        x = pl.scratch("x")
        m.d.sync += x.eq(x + 1)
        fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
    refuse(free_running, "outside the FSM")

    def other_domain(m, pl):
        from amaranth.hdl import ClockDomain
        m.domains.slow = ClockDomain()
        s = Signal()
        m.d.slow += s.eq(1)
        fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
    refuse(other_domain, "clock domain")


def test_reset_values_and_size():
    def scratch_init(m, pl):
        pl._scratch["x"] = Signal(32, init=3, name="pio_x")
        fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
    refuse(scratch_init, "resets to 3")

    def pin_init(m, pl):
        p = pl.buffer(m, "o", "gpio", 0)
        p.o._init = 1
        fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
    refuse(pin_init, "resets to 1")

    def too_big(m, pl):
        x = pl.scratch("x")
        states = []
        for i in range(33):
            def s(i=i):
                m.d.sync += x.eq(i % 2)
                m.next = "S%d" % ((i + 1) % 33)
            states.append(("S%d" % i, s))
        fsm_states(m, states)
    refuse(too_big, "32 words")


def test_chip_gating():
    def mov_pindirs_like(m, pl):                      # PIO version 1 only instructions are not generated yet; a wait on a low pin is fine on both
        p = pl.buffer(m, "i", "gpio", 4)

        def a():
            with m.If(~p.i):
                m.next = "B"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
    for chip in (RP2040PioPlatform, RP2350PioPlatform):
        c = compile_body(mov_pindirs_like, chip=chip)
        assert "wait 0 gpio 4" in c.program.pio_text()


# ----------------------------------------------------------------------------- the data path
def data_body(states, shifts=("right", "right"), pins=("o",)):
    """A design with a shifter, the scratch registers and (optionally) an output and an input pin group; `states` gets (m, sh, x, y, out, inp) and returns [(name, fn)]."""
    def body(m, pl):
        sh = pl.shifter(m, out_shift=shifts[0], in_shift=shifts[1])
        x, y = pl.scratch("x"), pl.scratch("y")
        out = pl.buffer(m, "o", "gpio", 0)
        inp = pl.buffer(m, "i", "gpio", 8)
        fsm_states(m, states(m, sh, x, y, out, inp))
    return body


def test_blocking_pull_must_continue_with_the_next_state():
    def states(m, sh, x, y, out, inp):
        def a():
            with sh.pull(m):
                m.next = "C"
        return [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))]
    refuse(data_body(states), "blocking pull / push must continue with the next state")


def test_two_data_operations_in_one_state():
    def states(m, sh, x, y, out, inp):
        def a():
            sh.out(m, "x", 4)
            sh.in_(m, "y", 4)
            m.next = "A"
        return [("A", a)]
    refuse(data_body(states), "do not match")


def test_data_operation_and_register_or_pin_in_one_state():
    def states(m, sh, x, y, out, inp):
        def a():
            sh.out(m, "null", 4)
            m.d.sync += y.eq(3)
            m.next = "A"
        return [("A", a)]
    refuse(data_body(states), "needs two PIO instructions")


def test_data_operation_and_jump_in_one_state():
    def states(m, sh, x, y, out, inp):
        def a():
            sh.in_(m, "x", 4)
            m.next = "C"
        return [("A", a), ("B", lambda: setattr(m, "next", "C")), ("C", lambda: setattr(m, "next", "A"))]
    refuse(data_body(states), "then jumps to another state")


def test_data_operation_in_a_state_with_a_test():
    def states(m, sh, x, y, out, inp):
        def a():
            sh.out(m, "null", 4)
            with m.If(x != 0):
                m.next = "A"
            with m.Else():
                m.next = "B"
        return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
    refuse(data_body(states), "cannot be in a state with a test")


def test_shifter_registers_without_an_operation():
    def states(m, sh, x, y, out, inp):
        def a():
            m.d.sync += sh.osr.eq(5)
            m.next = "A"
        return [("A", a)]
    refuse(data_body(states), "without an operation")


def test_fifo_flag_tested_by_hand():
    def states(m, sh, x, y, out, inp):
        def a():
            with m.If(sh.fifo.tx_ready):
                m.next = "B"
        return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
    refuse(data_body(states), "belongs to `pull block`")


def test_fifo_flag_must_be_true_when_the_instruction_completes():
    def wrong(m, sh, x, y, out, inp):
        def a():
            with m.If(~sh.fifo.tx_ready):
                with sh.pull(m):
                    m.next = "B"
        return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
    refuse(data_body(wrong), "more than one test")


def test_push_uses_the_rx_flag_and_pull_the_tx_flag():
    def states(m, sh, x, y, out, inp):
        def a():
            with m.If(sh.fifo.rx_space):
                with sh.pull(m, block=False):
                    m.next = "B"
        return [("A", a), ("B", lambda: setattr(m, "next", "A"))]
    refuse(data_body(states), "belongs to `push block`")


def test_side_set_must_agree_while_waiting_and_completing():
    def body(m, pl):
        sh = pl.shifter(m)
        side = pl.buffer(m, "o", "gpio", 3)
        pl.pio_role(side, "side")

        def a():
            with sh.pull(m):
                m.d.sync += side.o.eq(1)          # only when the word arrives: PIO applies side-set in the first cycle of the instruction
                m.next = "B"
        fsm_states(m, [("A", a), ("B", lambda: setattr(m, "next", "A"))])
    refuse(body, "side-set value differs between waiting and completing")


def test_a_blocking_operation_needs_its_helper():
    def states(m, sh, x, y, out, inp):
        def a():
            sh.out(m, "x", 4)
            m.d.sync += sh.op.eq(3)           # a different operation marker than the statements belong to
            m.next = "A"
        return [("A", a)]
    refuse(data_body(states), "do not match")


def test_two_out_pin_groups():
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
    refuse(body, "more than one pin group")


def test_two_in_pin_groups():
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
    refuse(body, "reads 2 different pin groups")


def test_helper_misuse_is_an_error_when_the_design_is_written():
    pl = RP2040PioPlatform()
    m = Module()
    m._MustUse__silence = True
    sh = pl.shifter(m)
    sh.fifo._MustUse__silence = True
    for bad in (0, 33, 2.5, "3"):
        with pytest.raises(ValueError, match="bit count"):
            sh.out(m, "x", bad)
    with pytest.raises(ValueError, match="bit count"):
        sh.in_(m, "null", 0)
    spare = Module()
    spare._MustUse__silence = True
    with pytest.raises(ValueError, match="already called"):
        pl.shifter(spare)
    with pytest.raises(ValueError, match="right"):
        RP2040PioPlatform().shifter(spare, out_shift="up")
    pin = RP2040PioPlatform()
    m2 = Module()
    m2._MustUse__silence = True
    p = pin.buffer(m2, "i", "gpio", 0)
    p._MustUse__silence = True
    s2 = pin.shifter(m2)
    s2.fifo._MustUse__silence = True
    with pytest.raises(ValueError, match="bit count"):
        s2.in_(m2, p, 2)           # a 1-bit input cannot supply two bits


def test_the_fifo_model_is_the_only_submodule_allowed():
    from amaranth.hdl import Module as M

    def body(m, pl):
        pl.shifter(m)
        other = M()
        other._MustUse__silence = True
        m.submodules.other = other
        fsm_states(m, [("A", lambda: setattr(m, "next", "A"))])
    refuse(body, "submodules are not supported")


# ----------------------------------------------------------------------------- the version gate and the FSM state names (python/pioweave/src/pioweave/_compat.py)
def test_version_gate():
    from pioweave._compat import UnsupportedAmaranth, check_version
    for ok in ("0.5.0", "0.5.1", "0.5.9", "0.5.10", "0.6.0.dev140"):
        assert check_version(ok, allow=False)
    for bad in ("0.5.11", "0.4.5", "0.6.0", "0.6.0.dev141", "1.0.0", "nonsense", "0.5", "0.5.1rc1"):
        with pytest.raises(UnsupportedAmaranth, match="tested with Amaranth"):
            check_version(bad, allow=False)
        assert check_version(bad, allow=True) is False     # PIOWEAVE_ALLOW_UNTESTED=1 lets it through (reported as untested)


def test_fsm_state_names_for_both_ways_amaranth_stores_them():
    from pioweave._compat import fsm_state_names

    class Decoder:             # Amaranth 0.5.x: Signal.decoder is a function that fails for numbers that are not states
        decoder = staticmethod(lambda n: {0: "A/0", 1: "B/1"}[n])

        def __len__(self):
            return 2
    assert fsm_state_names(Decoder()) == {0: "A/0", 1: "B/1"}

    class Format:
        _variants = {0: "IDLE/0", 1: "RUN/1"}

    class Enumerated:          # Amaranth 0.6 development: the names are in the format object of the signal
        decoder = None
        _format = Format()

        def __len__(self):
            return 2
    assert fsm_state_names(Enumerated()) == {0: "IDLE/0", 1: "RUN/1"}
    assert fsm_state_names(Signal(2)) is None
    assert fsm_state_names(Signal(2, decoder=lambda n: "no slash")) is None
