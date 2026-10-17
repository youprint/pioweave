# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see LICENSE)
from pioweave import RP2040PioPlatform

from .resources import sdk_resources

__all__ = ["PicoPlatform"]


class PicoPlatform(RP2040PioPlatform):
    """Raspberry Pi Pico (RP2040, 2 MB flash). The LED is GPIO 25. Pins from the Pico SDK header `pico.h`; LED and flash size also on raspberrypi.com/documentation/microcontrollers/pico-series.html (read 2026-10-17) [verify on the board]."""
    sdk_board = "pico"
    resources = sdk_resources(30, led=25, uart=(0, 1), i2c=(4, 5), spi=(18, 19, 16, 17))
