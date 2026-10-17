# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../../LICENSE)
"""pioweave: write programs for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 in Python or in Amaranth.

  pioweave.builder   a Python builder for PIO programs, one method per instruction (needs no Amaranth)
  pioweave           Amaranth platforms for the PIO blocks, the FSM-to-PIO compiler, the project writers (this module's public names, below)
  pioweave.emulator  an emulator for the PIO blocks: four state machines, shared instruction memory, FIFOs, IRQ flags, pins (needs no Amaranth)
  pioweave.boards    board definitions with named pins: Raspberry Pi Pico, Pico 2, WeAct Studio RP2350B core, Waveshare Core2350B

A design is an Amaranth module written in a restricted style (one FSM, one state = one PIO instruction); `platform.build(design, name, dir,
project="pio" | "pico-c" | "micropython")` compiles it to PIO instructions and writes the project. Several such designs can run as the state machines of one block
(`Block`, `Machine`, `BlockSim`, pioweave.block). Nothing has run on a chip. See the README and docs/.

The public names load on first use (PEP 562, as amaranth.vendor does), so `import pioweave.builder` does not import Amaranth. `import pioweave` checks the version of the installed
Amaranth from its package metadata (`pioweave._gate`), without importing it; the compiler, project and block modules check ``amaranth.__version__`` again when they are imported.
"""
import importlib
import importlib.metadata

from . import _gate

# The version comes from the package metadata, which comes from the git tag (pdm-backend, source = "scm"), as in amaranth-soc.
try:
    __version__ = importlib.metadata.version(__package__)
except importlib.metadata.PackageNotFoundError:     # not installed with pip (running from a source tree)
    __version__ = "unknown"

_gate.check_installed()       # the version gate: reads the metadata of the installed Amaranth, does not import it (UnsupportedAmaranth, unless PIOWEAVE_ALLOW_UNTESTED=1)

_NAMES = {
    "UnsupportedAmaranth": "_compat", "check_version": "_compat",
    "Compiled": "compile", "PioCompileError": "compile", "compile_design": "compile",
    "PLATFORMS": "platform", "RP2040PioPlatform": "platform", "RP2350PioPlatform": "platform", "RP2350APioPlatform": "platform", "RP2350BPioPlatform": "platform",
    "RP2354PioPlatform": "platform", "RP2354APioPlatform": "platform", "RP2354BPioPlatform": "platform",
    "PioDesign": "designs",
    "Block": "block", "BlockError": "block", "BlockSim": "block", "Machine": "block",
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
