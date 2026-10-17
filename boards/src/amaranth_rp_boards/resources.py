# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see LICENSE)
"""Helpers that turn the default pins of a Pico SDK board header into named amaranth-rp resources (one resource per pin: amaranth-rp requests plain pin groups)."""
from amaranth.build import Pins, Resource

__all__ = ["sdk_resources"]


def sdk_resources(chip_gpios, *, led=None, uart=None, i2c=None, spi=None):
    """`led`: GPIO of the default LED (resource `led`, number 0). `uart`: (tx, rx) GPIOs, resources `uart_tx` and `uart_rx`. `i2c`: (sda, scl), resources `i2c_sda`
    and `i2c_scl`. `spi`: (sck, tx, rx, csn), resources `spi_sck`, `spi_tx`, `spi_rx` and `spi_csn`. All keep the board's GPIO numbers; the full `gpio` list of the chip stays
    available next to them."""
    def one(name, gpio):
        return Resource(name, 0, Pins(str(gpio), dir="io"))
    out = [Resource("gpio", n, Pins(str(n), dir="io")) for n in range(chip_gpios)]
    if led is not None:
        out.append(one("led", led))
    for names, group in ((("uart_tx", "uart_rx"), uart), (("i2c_sda", "i2c_scl"), i2c), (("spi_sck", "spi_tx", "spi_rx", "spi_csn"), spi)):
        if group is not None:
            out += [one(n, g) for n, g in zip(names, group)]
    return out
