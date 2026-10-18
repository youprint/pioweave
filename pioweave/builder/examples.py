# Copyright (c) 2026 youprint. MIT licence (see ../../LICENSE.txt)
"""Example PIO programs written with pioweave.builder (program.py). Each equals the hand-written .pio of the same name in
tests/pio_ref/ word for word (tests/test_program.py). Simulation cases for them are in examples/pio/bitweave.toml ([pio.*_py])."""
from .program import PINS, X, Program, X_DEC

__all__ = ["square", "uart_tx", "passthru", "PROGRAMS"]


def square(chip="rp2040"):
    """A square wave on one pin: 2 cycles high, 2 cycles low.

    Parameters
    ----------
    chip : str
        The chip name, as for `Program`.

    Returns
    -------
    Program
        The program, without a ``.pio_version`` line.

    Examples
    --------
    >>> from pioweave.builder.examples import square
    >>> [hex(w) for w in square().words()]
    ['0xe101', '0xe100']
    """
    p = Program("square", chip=chip, emit_version=False)
    with p.wrapped():
        p.set(PINS, 1, delay=1)
        p.set(PINS, 0, delay=1)
    return p


def uart_tx(chip="rp2350"):
    """8N1 UART transmitter, 8 cycles per bit; side-set drives the line (idle and stop bits high, start bit low).

    Parameters
    ----------
    chip : str
        The chip name, as for `Program`.

    Returns
    -------
    Program
        The program, without a ``.pio_version`` line.
    """
    p = Program("uart_tx", chip=chip, side_set=1, side_opt=True, emit_version=False)
    p.pull(side=1, delay=7)
    p.set(X, 7, side=0, delay=7)
    p.label("bitloop")
    p.out(PINS, 1)
    p.jmp("bitloop", when=X_DEC, delay=6)
    return p


def passthru(chip="rp2040"):
    """Autopull feeds the OSR from the TX FIFO, autopush sends the ISR to the RX FIFO: every word comes straight back.

    Parameters
    ----------
    chip : str
        The chip name, as for `Program`.

    Returns
    -------
    Program
        The program, without a ``.pio_version`` line.
    """
    p = Program("passthru", chip=chip, emit_version=False)
    with p.wrapped():
        p.out(X, 32)
        p.in_(X, 32)
    return p


PROGRAMS = {"square": square, "uart_tx": uart_tx, "passthru": passthru}
