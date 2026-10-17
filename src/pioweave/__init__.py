# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../../LICENSE)
"""pioweave: write programs for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 in Python or in Amaranth.

  pioweave.builder   a Python builder for PIO programs, one method per instruction (needs no Amaranth)
  pioweave           Amaranth platforms for the PIO blocks, the FSM-to-PIO compiler, the project writers (this module's public names, below)
  pioweave.boards    board definitions with named pins: Raspberry Pi Pico, Pico 2, WeAct Studio RP2350B core, Waveshare Core2350B

A design is an Amaranth module written in a restricted style (one FSM, one state = one PIO instruction); `platform.build(design, name, dir,
project="pio" | "pico-c" | "micropython")` compiles it to PIO instructions and writes the project. Nothing has run on a chip. See the README and docs/.

The public names load on first use (PEP 562, as amaranth.vendor does), so `import pioweave.builder` does not import Amaranth.
"""
import importlib

__version__ = "0.1.0"

_NAMES = {
    "UnsupportedAmaranth": "_compat", "check_version": "_compat",
    "Compiled": "compile", "PioCompileError": "compile", "compile_design": "compile",
    "PLATFORMS": "platform", "RP2040PioPlatform": "platform", "RP2350PioPlatform": "platform", "RP2350APioPlatform": "platform", "RP2350BPioPlatform": "platform",
    "RP2354PioPlatform": "platform", "RP2354APioPlatform": "platform", "RP2354BPioPlatform": "platform",
    "PioDesign": "designs",
    "PioProjectError": "project", "SimOptions": "project", "amaranth_run": "project", "amaranth_waves": "project", "find_pioasm": "project", "write_project": "project",
}

__all__ = sorted(_NAMES)


def __getattr__(name):
    module = _NAMES.get(name)
    if module is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    value = getattr(importlib.import_module("." + module, __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals()) + __all__)
