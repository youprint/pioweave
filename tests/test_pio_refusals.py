# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""What the Amaranth-to-PIO compiler refuses, and why each design below is outside the subset because mapping it would change its cycle
behaviour or needs hardware PIO does not have. Every refusal must name the reason; none may be approximated."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.abspath(os.path.join(HERE, "..")), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from amaranth.build import Pins, Resource  # noqa: E402
from amaranth.hdl import Elaboratable, Module, Signal  # noqa: E402

from pio_compile import PioCompileError, compile_design  # noqa: E402
from platform_rp import RP2040PioPlatform, RP2350PioPlatform  # noqa: E402


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
