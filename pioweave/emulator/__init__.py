# Copyright (c) 2026 youprint. MIT licence (see ../../LICENSE.txt)
"""An emulator for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 (docs/emulator.rst): `PioBlock` with four state machines, the shared instruction memory, the IRQ
flags and the pins; `run_case` runs one state machine on a test case; `run_compiled` runs a program compiled from an Amaranth design; `emulate` yields ``(before, after)`` state
snapshots for unit tests, with `count_cycles`, `pin_history`, `waveform_text` and `plot_waveform` built on it. Needs no Amaranth."""
from .core import Config, EmulationError, Fifo, Loaded, PioBlock, StateMachine
from .run import Result, run_case, run_compiled, to_vcd
from .trace import State, count_cycles, emulate, pin_history, plot_waveform, waveform_text

__all__ = ["Config", "EmulationError", "Fifo", "Loaded", "PioBlock", "StateMachine", "Result", "run_case", "run_compiled", "to_vcd", "State", "emulate", "count_cycles",
           "pin_history", "waveform_text", "plot_waveform"]
