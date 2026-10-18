# Copyright (c) 2026 youprint. MIT licence (see ../../LICENSE.txt)
"""Board definitions for pioweave, one module per board (as amaranth-boards does): `from pioweave.boards.pico import PicoPlatform`.

Pin numbers come from the board headers of the Pico SDK (``src/boards/include/boards/*.h``), read 2026-10-17; the tests compare them with the headers when an SDK checkout
is available. Nothing here has run on a board.
"""
__all__ = []
from .. import __version__  # noqa: E402  (from the package metadata, which comes from the git tag)
