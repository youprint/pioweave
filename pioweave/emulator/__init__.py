# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../../LICENSE.txt)
"""An emulator for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 (docs/emulator.md): `PioBlock` with four state machines, the shared instruction memory, the IRQ
flags and the pins; `run_case` runs one state machine on a test case; `run_compiled` runs a program compiled from an Amaranth design. Needs no Amaranth."""
from .core import Config, EmulationError, Fifo, Loaded, PioBlock, StateMachine
from .run import Result, run_case, run_compiled, to_vcd

__all__ = ["Config", "EmulationError", "Fifo", "Loaded", "PioBlock", "StateMachine", "Result", "run_case", "run_compiled", "to_vcd"]
