# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Example PIO programs written in Amaranth, compiled by pio_compile.py for the platforms of platform_rp.py.

Each one is an ordinary Amaranth Elaboratable: one FSM, pins from platform.buffer() (an amaranth.lib.io.Buffer on a requested pin group), scratch registers from platform.scratch(). One FSM state
is one PIO instruction (one clock cycle); states without effects that follow an instruction become its delay. The comments give the cycle count.
`PROGRAMS` maps a name to (design class, platform name, simulation stimulus, cycles) for the project writers and the tests.
"""
from amaranth.hdl import Elaboratable, Module


class SquareWave(Elaboratable):
    """A square wave on GPIO 0: 2 cycles high, 2 cycles low (period 4 cycles)."""

    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", 0)
        with m.FSM():
            with m.State("HIGH"):
                m.d.sync += pin.o.eq(1)
                m.next = "HIGH_HOLD"
            with m.State("HIGH_HOLD"):          # no effect: folded into the delay of the instruction before it
                m.next = "LOW"
            with m.State("LOW"):
                m.d.sync += pin.o.eq(0)
                m.next = "LOW_HOLD"
            with m.State("LOW_HOLD"):
                m.next = "HIGH"
        return m


class PulseTrain(Elaboratable):
    """Three pulses of 2 cycles high / 2 low on GPIO 0, then 4 idle cycles, repeated; X counts the pulses (one `jmp x--` per pulse)."""

    def elaborate(self, platform):
        m = Module()
        pin = platform.buffer(m, "o", "gpio", 0)
        x = platform.scratch("x")
        with m.FSM():
            with m.State("LOAD"):
                m.d.sync += x.eq(2)             # 3 passes: x = 2, 1, 0
                m.next = "ON"
            with m.State("ON"):
                m.d.sync += pin.o.eq(1)
                m.next = "ON_HOLD"
            with m.State("ON_HOLD"):
                m.next = "OFF"
            with m.State("OFF"):
                m.d.sync += pin.o.eq(0)
                m.next = "OFF_HOLD"
            with m.State("OFF_HOLD"):
                m.next = "COUNT"
            with m.State("COUNT"):              # jmp x-- ON: loop while x was not zero, decrementing it
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "ON"
                with m.Else():
                    m.next = "IDLE"
            with m.State("IDLE"):
                m.next = "IDLE2"
            with m.State("IDLE2"):
                m.next = "IDLE3"
            with m.State("IDLE3"):
                m.next = "LOAD"
        return m


class ButtonPulse(Elaboratable):
    """Waits for GPIO 4 to go high, drives GPIO 0 high for 3 cycles, waits for GPIO 4 to go low again (`wait`), and repeats."""

    def elaborate(self, platform):
        m = Module()
        out = platform.buffer(m, "o", "gpio", 0)
        button = platform.buffer(m, "i", "gpio", 4)
        with m.FSM():
            with m.State("WAIT_HIGH"):          # stays here until the pin is high: wait 1 gpio 4
                with m.If(button.i):
                    m.next = "PULSE"
            with m.State("PULSE"):
                m.d.sync += out.o.eq(1)
                m.next = "PULSE_1"
            with m.State("PULSE_1"):
                m.next = "PULSE_2"
            with m.State("PULSE_2"):
                m.next = "END"
            with m.State("END"):
                m.d.sync += out.o.eq(0)
                m.next = "WAIT_LOW"
            with m.State("WAIT_LOW"):           # wait 0 gpio 4
                with m.If(~button.i):
                    m.next = "WAIT_HIGH"
        return m


class ClockedBits(Elaboratable):
    """A clock on GPIO 1 (side-set) and a data pin on GPIO 0 (set pins): the data pin gets 1, 0, 1 and the clock toggles with every bit."""

    def elaborate(self, platform):
        m = Module()
        data = platform.buffer(m, "o", "gpio", 0)
        clk = platform.buffer(m, "o", "gpio", 1)
        platform.pio_role(clk, "side")
        with m.FSM():
            with m.State("B1"):
                m.d.sync += data.o.eq(1)
                m.next = "C1"
            with m.State("C1"):                 # a clock edge in a state of its own: side-set carries it with a `nop`
                m.d.sync += clk.o.eq(1)
                m.next = "B2"
            with m.State("B2"):
                m.d.sync += data.o.eq(0)
                m.d.sync += clk.o.eq(0)         # data (set) and clock (side-set) in the same cycle
                m.next = "C2"
            with m.State("C2"):
                m.d.sync += clk.o.eq(1)
                m.next = "B3"
            with m.State("B3"):
                m.d.sync += data.o.eq(1)
                m.d.sync += clk.o.eq(0)
                m.next = "C3"
            with m.State("C3"):
                m.d.sync += clk.o.eq(1)
                m.next = "B1"
        return m


class PinBranch(Elaboratable):
    """GPIO 4 selects the pattern on GPIO 0: when it is high the output pulses twice (jmp pin), otherwise once."""

    def elaborate(self, platform):
        m = Module()
        out = platform.buffer(m, "o", "gpio", 0)
        sel = platform.buffer(m, "i", "gpio", 4)
        with m.FSM():
            with m.State("START"):
                with m.If(sel.i):                # jmp pin TWICE
                    m.next = "TWICE"
                with m.Else():
                    m.next = "ONCE"
            with m.State("ONCE"):
                m.d.sync += out.o.eq(1)
                m.next = "ONCE_OFF"
            with m.State("ONCE_OFF"):
                m.d.sync += out.o.eq(0)
                m.next = "GAP"
            with m.State("GAP"):
                m.next = "GAP2"
            with m.State("GAP2"):
                m.next = "START"                 # a jump back to the first state
            with m.State("TWICE"):
                m.d.sync += out.o.eq(1)
                m.next = "TWICE_OFF"
            with m.State("TWICE_OFF"):
                m.d.sync += out.o.eq(0)
                m.next = "TWICE_ON"
            with m.State("TWICE_ON"):
                m.d.sync += out.o.eq(1)
                m.next = "TWICE_END"
            with m.State("TWICE_END"):
                m.d.sync += out.o.eq(0)
                m.next = "START"
        return m


class HighPins(Elaboratable):
    """For the 48-GPIO parts (RP2350B, RP2354B): a pulse on GPIO 40 when GPIO 44 is high, a clock on GPIO 41 (side-set). The PIO window is GPIO 16 to 47,
    so the compiler picks GPIO base 16; `wait gpio 44` keeps the real number and the SDK flips bit 4 when it loads the program."""

    def elaborate(self, platform):
        m = Module()
        out = platform.buffer(m, "o", "gpio", 40)
        clk = platform.buffer(m, "o", "gpio", 41)
        platform.pio_role(clk, "side")
        trig = platform.buffer(m, "i", "gpio", 44)
        with m.FSM():
            with m.State("IDLE"):
                m.d.sync += clk.o.eq(0)
                with m.If(trig.i):
                    m.next = "UP"
            with m.State("UP"):
                m.d.sync += out.o.eq(1)
                m.d.sync += clk.o.eq(1)
                m.next = "UP_HOLD"
            with m.State("UP_HOLD"):
                m.next = "DOWN"
            with m.State("DOWN"):
                m.d.sync += out.o.eq(0)
                m.d.sync += clk.o.eq(0)
                m.next = "WAIT_LOW"
            with m.State("WAIT_LOW"):
                with m.If(~trig.i):
                    m.next = "IDLE"
        return m


PROGRAMS = {
    "square_wave": (SquareWave, "rp2040", {}, 24),
    "pulse_train": (PulseTrain, "rp2040", {}, 60),
    "button_pulse": (ButtonPulse, "rp2350", {4: "000011111100000111000011111111"}, 60),
    "clocked_bits": (ClockedBits, "rp2040", {}, 30),
    "pin_branch": (PinBranch, "rp2350", {4: "0000000000000000000011111111111111111111"}, 60),
    "high_pins": (HighPins, "rp2350b", {44: "000011110000011111000000111100"}, 50),
}
