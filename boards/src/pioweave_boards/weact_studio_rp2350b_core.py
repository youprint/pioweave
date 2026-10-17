# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see LICENSE)
from pioweave import RP2350BPioPlatform

from .resources import sdk_resources

__all__ = ["WeActStudioRP2350BCorePlatform"]


class WeActStudioRP2350BCorePlatform(RP2350BPioPlatform):
    """WeAct Studio RP2350B core board V1.0 (RP2350B QFN-80, 48 GPIOs, 41.4 mm x 41.1 mm; the schematic RP2350B_SCH.pdf shows the blue LED on GPIO 25 (active high, 5.1 kOhm
    to ground) and the user key on GPIO 23 (to ground, 5.1 kOhm pull-up). GPIO 0 is the chip select of the secondary flash / PSRAM (U7 through R14, 0 ohm) while that
    resistor is fitted, so a program that uses GPIO 0 should check the board. The default UART (GPIO 12, 13), I2C (8, 9) and SPI (18, 19, 16, 17) pins are not on the
    schematic: they come from the Pico SDK header `weact_studio_rp2350b_core.h` [verify on the board]."""
    sdk_board = "weact_studio_rp2350b_core"
    resources = sdk_resources(48, led=25, button=23, uart=(12, 13), i2c=(8, 9), spi=(18, 19, 16, 17))
