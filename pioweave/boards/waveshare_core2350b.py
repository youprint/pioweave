# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see LICENSE)
from pioweave import RP2350BPioPlatform

from .resources import sdk_resources

__all__ = ["WaveshareCore2350BPlatform"]


class WaveshareCore2350BPlatform(RP2350BPioPlatform):
    """Waveshare Core2350B (RP2350B QFN-80, 48 GPIOs, 16 MB flash W25Q128JVSIQ, a PSRAM chip). Schematic (files.waveshare.com/wiki/Core2350B0/Core2350B.pdf, read 2026-10-17): the red LED
    is on GPIO 39 (active high, 470 ohm to ground), GPIO 47 is the PSRAM chip select (net PSRAM_CS), BOOTSEL is the only key, and no default UART, I2C or SPI pins are drawn; those
    (UART 0/1, I2C 4/5, SPI 18/19/16/17) come from the Pico SDK header `waveshare_core2350b.h` [verify on the board]. The product page waveshare.com/core2350b.htm states the chip,
    48 GPIOs and 16 MB flash. A program should keep GPIO 47 free when it uses the PSRAM.

    Attributes
    ----------
    sdk_board : str
        The Pico SDK board name (``PICO_BOARD``): ``"waveshare_core2350b"``.
    resources : list of amaranth.build.Resource
        ``gpio`` 0 to 47, and the named pins ``led``, ``i2c_scl``, ``i2c_sda``, ``spi_csn``, ``spi_rx``, ``spi_sck``, ``spi_tx``, ``uart_rx``, ``uart_tx`` (from `sdk_resources`).

    Examples
    --------
    >>> from pioweave.boards.waveshare_core2350b import WaveshareCore2350BPlatform
    >>> WaveshareCore2350BPlatform.sdk_board
    'waveshare_core2350b'
    >>> sorted({r.name for r in WaveshareCore2350BPlatform.resources} - {"gpio"})
    ['i2c_scl', 'i2c_sda', 'led', 'spi_csn', 'spi_rx', 'spi_sck', 'spi_tx', 'uart_rx', 'uart_tx']
    """
    sdk_board = "waveshare_core2350b"
    resources = sdk_resources(48, led=39, uart=(0, 1), i2c=(4, 5), spi=(18, 19, 16, 17))
