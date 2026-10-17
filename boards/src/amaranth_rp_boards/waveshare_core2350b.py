# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see LICENSE)
from amaranth_rp import RP2350BPioPlatform

from .resources import sdk_resources

__all__ = ["WaveshareCore2350BPlatform"]


class WaveshareCore2350BPlatform(RP2350BPioPlatform):
    """Waveshare Core2350B (RP2350B, QFN-80 with 48 GPIOs, 16 MB flash). The LED is GPIO 39. Pins from the Pico SDK header `waveshare_core2350b.h`; chip, 48 GPIOs and 16 MB flash also stated on waveshare.com/core2350b.htm (read 2026-10-17), which does not give the LED GPIO or the default UART/I2C/SPI pins (they are on the page's wiki image) [verify on the board]."""
    sdk_board = "waveshare_core2350b"
    resources = sdk_resources(48, led=39, uart=(0, 1), i2c=(4, 5), spi=(18, 19, 16, 17))
