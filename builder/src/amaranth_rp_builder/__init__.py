# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""amaranth-rp-builder: write PIO programs for the Raspberry Pi RP2040, RP2350 and RP2354 (and their A / B variants) in Python.

No dependencies. See program.py for the builder and docs/pio-dsl.md for the design note. The Amaranth platform and compiler that build on it are the
separate package amaranth-rp.
"""
from .program import (ALWAYS, CHIP_VERSION, CLEAR, EXEC, INVERT, ISR, MEMORY_WORDS, NONE, NOT_OSRE, NOT_X, NOT_Y, NULL, OSR, PC, PIN, PINDIRS, PINS, REVERSE, SET,
                      STATUS, WAIT, X, X_DEC, X_NE_Y, Y, Y_DEC, PioError, Program, write_pio)

__all__ = ["ALWAYS", "CHIP_VERSION", "CLEAR", "EXEC", "INVERT", "ISR", "MEMORY_WORDS", "NONE", "NOT_OSRE", "NOT_X", "NOT_Y", "NULL", "OSR", "PC", "PIN", "PINDIRS",
           "PINS", "REVERSE", "SET", "STATUS", "WAIT", "X", "X_DEC", "X_NE_Y", "Y", "Y_DEC", "PioError", "Program", "write_pio"]
__version__ = "0.1.0"
