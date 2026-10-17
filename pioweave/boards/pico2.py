# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see LICENSE)
from pioweave import RP2350APioPlatform

from .resources import sdk_resources

__all__ = ["Pico2Platform"]


class Pico2Platform(RP2350APioPlatform):
    """Raspberry Pi Pico 2 (RP2350A, 4 MB flash). The LED is GPIO 25. Pins from the Pico SDK header `pico2.h` [verify on the board]."""
    sdk_board = "pico2"
    resources = sdk_resources(30, led=25, uart=(0, 1), i2c=(4, 5), spi=(18, 19, 16, 17))
