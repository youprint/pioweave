# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""amaranth-rp: Amaranth platforms for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 (and their A / B variants).

A design is an Amaranth Elaboratable written in a restricted style (one FSM, one state = one PIO instruction); `platform.build(design, name, dir,
project="pio" | "pico-c" | "micropython")` compiles it to PIO instructions and writes the project. Design note: docs/amaranth-pio.md. Nothing has run on a chip.
"""
from ._compat import UnsupportedAmaranth, check_version
from .compile import Compiled, PioCompileError, compile_design
from .platform import (PLATFORMS, RP2040PioPlatform, RP2350APioPlatform, RP2350BPioPlatform, RP2350PioPlatform, RP2354APioPlatform, RP2354BPioPlatform,
                       RP2354PioPlatform)
from .designs import PioDesign
from .project import PioProjectError, SimOptions, amaranth_run, amaranth_waves, find_pioasm, write_project

__all__ = ["UnsupportedAmaranth", "check_version", "Compiled", "PioCompileError", "compile_design", "PLATFORMS", "RP2040PioPlatform", "RP2350PioPlatform",
           "RP2350APioPlatform", "RP2350BPioPlatform", "RP2354PioPlatform", "RP2354APioPlatform", "RP2354BPioPlatform", "PioProjectError", "amaranth_waves",
           "find_pioasm", "write_project", "SimOptions", "amaranth_run", "PioDesign"]
__version__ = "0.1.0"
