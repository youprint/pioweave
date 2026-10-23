# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""The PIO clock divider at design time: which INT and FRAC to write for a state machine clock or a bit rate, and what the quantisation costs.

The divider of a state machine is a 16-bit integer part and an 8-bit fraction in 1/256 steps (RP2040 datasheet 3.5.5 and the register SMx_CLKDIV; RP2350 11.5.5);
a stored INT of 0 means 65536, and a fraction with it is not allowed (Pico SDK ``sm_config_set_clkdiv_int_frac8`` refuses ``INT == 0`` with ``FRAC != 0``). The divider is
``round(divider * 256) / 256``, the same quantisation the emulator applies (``pioweave.emulator.core``, ``_clock_enable``), so the numbers here and a simulation agree.
With a fraction other than 0 the state machine clock is not a regular clock: periods are ``int_part`` or ``int_part + 1`` system clock cycles (first-order delta-sigma,
RP2040 3.5.5 p.339-340), so an edge can move by one system clock cycle. This module needs no Amaranth and no hardware.

>>> from pioweave.clock import pio_clkdiv
>>> c = pio_clkdiv(125_000_000, 4_000)
>>> c.int_part, c.frac, c.divider
(31250, 0, 31250.0)
"""
import dataclasses
import math

from .builder.program import PioError

__all__ = ["ClkDiv", "pio_clkdiv", "pio_bitrate_div", "DEFAULT_SYS_HZ", "MIN_DIVIDER", "MAX_DIVIDER"]

#: The system clock the Pico SDK configures by default: 125 MHz on the RP2040 and 150 MHz on the RP2350 family (pico-sdk ``hardware/clocks.h``, the comment block "Default PLL
#: configuration": PLL SYS 12 MHz crystal x 125 = 1500 MHz, divided by 6 and 2 on the RP2040 and by 5 and 2 on the RP2350; read 2026-10-23). It is the SDK default, not the
#: limit of the chip, and a board or program may change it: pass ``sys_hz`` for any other value [verify against the datasheet of the chip before relying on it].
DEFAULT_SYS_HZ = {"rp2040": 125_000_000, "rp2350": 150_000_000, "rp2350a": 150_000_000, "rp2350b": 150_000_000,
                  "rp2354": 150_000_000, "rp2354a": 150_000_000, "rp2354b": 150_000_000}

MIN_DIVIDER = 1.0
MAX_DIVIDER = 65536.0
_STEPS = 256            # the fraction is 8 bits: 1/256 steps


@dataclasses.dataclass(frozen=True)
class ClkDiv:
    """The result of `pio_clkdiv`.

    Attributes
    ----------
    int_part : int
        The value for the INT field (16 bits). 0 stands for a divider of 65536.
    frac : int
        The value for the FRAC field (0 to 255, in 1/256).
    divider : float
        The divider the chip applies, ``round(requested * 256) / 256`` (65536.0 when ``int_part`` is 0).
    sm_hz : float
        The mean state machine clock, ``sys_hz / divider``.
    error_ppm : float
        ``(sm_hz - target_hz) / target_hz * 1e6``; negative when the clock is too slow.
    period_jitter_cycles : int
        Peak-to-peak jitter of a state machine clock edge in system clock cycles: 0 when ``frac`` is 0, otherwise 1.
    notes : tuple of str
        Things to know about this divider (a fraction in use, a divider of 1, the INT 0 encoding).
    """
    int_part: int
    frac: int
    divider: float
    sm_hz: float
    error_ppm: float
    period_jitter_cycles: int
    notes: tuple


def _positive(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise PioError("%s must be a finite number above 0, not %r" % (what, value))


def pio_clkdiv(sys_hz, target_hz, *, chip="rp2040", max_error_ppm=None):
    """Quantise the divider for a state machine clock like the chip does.

    Parameters
    ----------
    sys_hz : int, float or None
        The system clock in Hz; None takes the Pico SDK default of `chip` (`DEFAULT_SYS_HZ`).
    target_hz : int or float
        The state machine clock wanted, in Hz.
    chip : str
        A chip name of `DEFAULT_SYS_HZ`; it only selects the default `sys_hz`.
    max_error_ppm : int, float or None
        If given, an error larger than this (in absolute value) raises `PioError`.

    Returns
    -------
    ClkDiv

    Raises
    ------
    PioError
        For an unknown chip, a `sys_hz` or `target_hz` that is not a finite number above 0, a divider below 1 or above 65536 (the state machine cannot run faster than the
        system clock or slower than 1/65536 of it), or an error above `max_error_ppm`.
    """
    if chip not in DEFAULT_SYS_HZ:
        raise PioError("unknown chip %r; use one of %s" % (chip, ", ".join(sorted(DEFAULT_SYS_HZ))))
    if sys_hz is None:
        sys_hz = DEFAULT_SYS_HZ[chip]
    _positive(sys_hz, "the system clock")
    _positive(target_hz, "the target clock")
    if max_error_ppm is not None and (isinstance(max_error_ppm, bool) or not isinstance(max_error_ppm, (int, float)) or not max_error_ppm >= 0):
        raise PioError("max_error_ppm must be a number of at least 0, not %r" % (max_error_ppm,))
    ideal = sys_hz / target_hz
    if ideal < MIN_DIVIDER:
        raise PioError("the divider %.6g is below 1: %g Hz is above the system clock of %g Hz\n  hint: raise the system clock or lower the target" % (ideal, target_hz, sys_hz))
    steps = int(round(ideal * _STEPS))          # the emulator's quantisation (_clock_enable), so that both agree
    if steps > int(MAX_DIVIDER) * _STEPS:
        raise PioError("the divider %.6g is above 65536: %g Hz is too slow for a system clock of %g Hz\n  hint: the slowest state machine clock is sys_hz / 65536 = %g Hz"
                       % (ideal, target_hz, sys_hz, sys_hz / MAX_DIVIDER))
    divider = steps / _STEPS
    int_part, frac = (steps >> 8) & 0xFFFF, steps & 0xFF       # 65536 is stored as INT = 0
    sm_hz = sys_hz / divider
    error_ppm = (sm_hz - target_hz) / target_hz * 1e6
    if max_error_ppm is not None and abs(error_ppm) > max_error_ppm:
        raise PioError("the best divider %.8g gives %.6g Hz, %.1f ppm from %g Hz, above the limit of %g ppm" % (divider, sm_hz, error_ppm, target_hz, max_error_ppm))
    notes = []
    if frac:
        notes.append("fractional divider: periods of %d and %d system clock cycles, edges move by up to 1 system clock cycle (%.3g ns at %g Hz)"
                     % ((steps >> 8), (steps >> 8) + 1, 1e9 / sys_hz, sys_hz))
    if divider == 1.0:
        notes.append("divider 1: the state machine runs on every system clock cycle")
    if int_part == 0:
        notes.append("INT is written as 0, which the chip reads as 65536")
    return ClkDiv(int_part, frac, divider, sm_hz, error_ppm, 1 if frac else 0, tuple(notes))


def pio_bitrate_div(sys_hz, bit_rate, cycles_per_bit, **kw):
    """The divider for a bit rate: `pio_clkdiv` with a state machine clock of ``bit_rate * cycles_per_bit``.

    Parameters
    ----------
    sys_hz : int, float or None
        The system clock in Hz (None: the default of the chip).
    bit_rate : int or float
        Bits per second.
    cycles_per_bit : int or float
        State machine clock cycles the program spends per bit (for example 8 for a UART that samples 8 times per bit).
    **kw
        `chip` and `max_error_ppm` of `pio_clkdiv`.

    Returns
    -------
    ClkDiv
        Its ``sm_hz`` is the mean state machine clock; the bit rate reached is ``sm_hz / cycles_per_bit``.
    """
    _positive(bit_rate, "the bit rate")
    _positive(cycles_per_bit, "cycles per bit")
    return pio_clkdiv(sys_hz, bit_rate * cycles_per_bit, **kw)
