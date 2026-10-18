# Copyright (c) 2026 youprint. MIT licence (see ../../LICENSE.txt)
from pioweave import RP2350APioPlatform

from .resources import sdk_resources

__all__ = ["Pico2Platform"]


class Pico2Platform(RP2350APioPlatform):
    """Raspberry Pi Pico 2 (RP2350A, 4 MB flash). The LED is GPIO 25. Pins from the Pico SDK header `pico2.h` [verify on the board].

    Attributes
    ----------
    sdk_board : str
        The Pico SDK board name (``PICO_BOARD``): ``"pico2"``.
    resources : list of amaranth.build.Resource
        ``gpio`` 0 to 29, and the named pins ``led``, ``i2c_scl``, ``i2c_sda``, ``spi_csn``, ``spi_rx``, ``spi_sck``, ``spi_tx``, ``uart_rx``, ``uart_tx`` (from `sdk_resources`).

    Examples
    --------
    >>> from pioweave.boards.pico2 import Pico2Platform
    >>> Pico2Platform.sdk_board
    'pico2'
    >>> sorted({r.name for r in Pico2Platform.resources} - {"gpio"})
    ['i2c_scl', 'i2c_sda', 'led', 'spi_csn', 'spi_rx', 'spi_sck', 'spi_tx', 'uart_rx', 'uart_tx']
    """
    sdk_board = "pico2"
    resources = sdk_resources(30, led=25, uart=(0, 1), i2c=(4, 5), spi=(18, 19, 16, 17))
