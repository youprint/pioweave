# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see LICENSE)
"""Helpers that turn the default pins of a Pico SDK board header into named pioweave resources (one resource per pin: pioweave requests plain pin groups)."""
from amaranth.build import Pins, Resource

__all__ = ["sdk_resources"]


def sdk_resources(chip_gpios, *, led=None, button=None, uart=None, i2c=None, spi=None):
    """`led`: GPIO of the default LED (resource `led`, number 0). `button`: GPIO of the user button (resource `button`, number 0). `uart`: (tx, rx) GPIOs, resources `uart_tx` and `uart_rx`. `i2c`: (sda, scl), resources `i2c_sda`
    and `i2c_scl`. `spi`: (sck, tx, rx, csn), resources `spi_sck`, `spi_tx`, `spi_rx` and `spi_csn`. All keep the board's GPIO numbers; the full `gpio` list of the chip stays
    available next to them.

    Parameters
    ----------
    chip_gpios : int
        The number of GPIOs of the chip; resources ``gpio`` 0 to ``chip_gpios - 1`` are always created.
    led : int or None
        The GPIO of the default LED.
    button : int or None
        The GPIO of the user button.
    uart : tuple of int or None
        ``(tx, rx)``.
    i2c : tuple of int or None
        ``(sda, scl)``.
    spi : tuple of int or None
        ``(sck, tx, rx, csn)``.

    Returns
    -------
    list of amaranth.build.Resource
        The resources, for the ``resources`` attribute of a board platform.

    Examples
    --------
    >>> from pioweave.boards.resources import sdk_resources
    >>> [(r.name, r.number) for r in sdk_resources(3, led=1, uart=(0, 1))]
    [('gpio', 0), ('gpio', 1), ('gpio', 2), ('led', 0), ('uart_tx', 0), ('uart_rx', 0)]
    """
    def one(name, gpio):
        return Resource(name, 0, Pins(str(gpio), dir="io"))
    out = [Resource("gpio", n, Pins(str(n), dir="io")) for n in range(chip_gpios)]
    if led is not None:
        out.append(one("led", led))
    if button is not None:
        out.append(one("button", button))
    for names, group in ((("uart_tx", "uart_rx"), uart), (("i2c_sda", "i2c_scl"), i2c), (("spi_sck", "spi_tx", "spi_rx", "spi_csn"), spi)):
        if group is not None:
            out += [one(n, g) for n, g in zip(names, group)]
    return out
