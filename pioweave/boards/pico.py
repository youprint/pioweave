# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see LICENSE)
from pioweave import RP2040PioPlatform

from .resources import sdk_resources

__all__ = ["PicoPlatform"]


class PicoPlatform(RP2040PioPlatform):
    """Raspberry Pi Pico (RP2040, 2 MB flash). The LED is GPIO 25. Pins from the Pico SDK header `pico.h`; LED and flash size also on raspberrypi.com/documentation/microcontrollers/pico-series.html (read 2026-10-17) [verify on the board].

    Attributes
    ----------
    sdk_board : str
        The Pico SDK board name (``PICO_BOARD``): ``"pico"``.
    resources : list of amaranth.build.Resource
        ``gpio`` 0 to 29, and the named pins ``led``, ``i2c_scl``, ``i2c_sda``, ``spi_csn``, ``spi_rx``, ``spi_sck``, ``spi_tx``, ``uart_rx``, ``uart_tx`` (from `sdk_resources`).

    Examples
    --------
    >>> from pioweave.boards.pico import PicoPlatform
    >>> PicoPlatform.sdk_board
    'pico'
    >>> sorted({r.name for r in PicoPlatform.resources} - {"gpio"})
    ['i2c_scl', 'i2c_sda', 'led', 'spi_csn', 'spi_rx', 'spi_sck', 'spi_tx', 'uart_rx', 'uart_tx']
    """
    sdk_board = "pico"
    resources = sdk_resources(30, led=25, uart=(0, 1), i2c=(4, 5), spi=(18, 19, 16, 17))
