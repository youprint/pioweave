# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""Input checks shared by the command line, the project writers and the block writers. No Amaranth is imported, so the builder may use it.

Every check raises an explicit exception (never an ``assert``, which ``python -O`` removes). The functions take the exception class to raise, so each entry point keeps its own
error type (`pioweave.project.PioProjectError`, `pioweave.block.BlockError`, `ValueError`).
"""
import keyword
import math
import re

__all__ = ["UsageError", "parse_stim", "parse_tx", "parse_tx_feed", "check_name", "check_stim", "check_cycles", "check_words", "check_tx_feed", "check_clock_hz", "RESERVED_NAMES", "MAX_NAME_LENGTH", "DEFAULT_MAX_CYCLES"]

MAX_NAME_LENGTH = 64
"""The longest project or program name (it becomes a file name and, with ``build_`` and a suffix, must fit the file system's limit)."""

DEFAULT_MAX_CYCLES = 1_000_000
"""The default ceiling of the length of a simulation or an emulation, in clock cycles (raise it with ``--max-cycles``)."""

_PIOASM = ("define", "program", "origin", "side_set", "wrap_target", "wrap", "lang_opt", "word", "clock_div", "fifo", "mov_status", "pio_version", "jmp", "wait", "in", "out",
           "push", "pull", "mov", "irq", "set", "nop", "public")
_C = ("auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else", "enum", "extern", "float", "for", "goto", "if", "inline", "int", "long", "register",
      "restrict", "return", "short", "signed", "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned", "void", "volatile", "while", "bool", "true", "false", "NULL")
RESERVED_NAMES = frozenset(_PIOASM) | frozenset(_C) | frozenset(keyword.kwlist)
"""Names refused as a project name: pioasm directives and instructions, C keywords, and Python keywords."""

class UsageError(ValueError):
    """A command line value that is not valid; the command line prints it as ``error: ...`` and exits with status 2."""


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WAVE = re.compile(r"[01]+")
MAX_GPIO = 47            # the RP2350B has GPIO 0 to 47
MAX_WORD = 0xFFFFFFFF


def check_name(name, error=ValueError, what="name"):
    """Check a project, program or machine name.

    A name is pasted into file names, ``bitweave.toml``, ``CMakeLists.txt``, ``main.c`` and a shell script name, so it must be a plain identifier: ASCII letters, digits and
    underscore, not starting with a digit, at most `MAX_NAME_LENGTH` characters, and not a word in `RESERVED_NAMES`.

    Parameters
    ----------
    name : object
        The candidate.
    error : type
        The exception class to raise.
    what : str
        What the name is called in the message.

    Returns
    -------
    str
        `name`, unchanged.

    Raises
    ------
    Exception
        An instance of `error` if the name is not valid.

    Examples
    --------
    >>> from pioweave._validate import check_name
    >>> check_name("square_wave")
    'square_wave'
    >>> check_name("my-prog")  # doctest: +ELLIPSIS
    Traceback (most recent call last):
      ...
    ValueError: invalid name 'my-prog': ...
    """
    if not isinstance(name, str) or not _IDENT.fullmatch(name):
        raise error("invalid %s %r: use ASCII letters, digits and '_', not starting with a digit (it becomes a file name and a C symbol)\n"
                    "  hint: for example --name my_prog instead of my-prog" % (what, name))
    if len(name) > MAX_NAME_LENGTH:
        raise error("invalid %s %r: longer than %d characters\n  hint: choose a shorter name" % (what, name, MAX_NAME_LENGTH))
    if name in RESERVED_NAMES:
        raise error("invalid %s %r: it is a keyword of pioasm, C or Python\n  hint: choose another name, for example %s_prog" % (what, name, name))
    return name


def _int(value, what, error):
    if isinstance(value, bool) or not isinstance(value, int):
        raise error("%s must be an integer, not %r" % (what, value))
    return value


def check_cycles(cycles, error=ValueError, maximum=None, what="cycles"):
    """Check the length of a simulation.

    Parameters
    ----------
    cycles : int
        The number of clock cycles.
    error : type
        The exception class to raise.
    maximum : int or None
        The ceiling; None means `DEFAULT_MAX_CYCLES`. Pass a larger value (the ``--max-cycles`` option) to run longer.
    what : str
        What the number is called in the message.

    Returns
    -------
    int
        `cycles`.

    Examples
    --------
    >>> from pioweave._validate import check_cycles
    >>> check_cycles(64)
    64
    >>> check_cycles(10**9)  # doctest: +ELLIPSIS
    Traceback (most recent call last):
      ...
    ValueError: cycles 1000000000 is more than the limit 1000000; ...
    """
    limit = DEFAULT_MAX_CYCLES if maximum is None else maximum
    _int(cycles, what, error)
    if cycles < 1:
        raise error("%s must be at least 1, not %d" % (what, cycles))
    if cycles > limit:
        raise error("%s %d is more than the limit %d; the run would take a long time and much memory\n  hint: raise the limit with --max-cycles N if you mean it" % (what, cycles, limit))
    return cycles


def check_stim(stim, error=ValueError):
    """Check input waveforms ``{gpio: "0101..."}``: the GPIO is an integer from 0 to 47, the waveform a non-empty string of 0 and 1.

    Parameters
    ----------
    stim : dict or None
        The waveforms.
    error : type
        The exception class to raise.

    Returns
    -------
    dict
        `stim` (``{}`` for None).
    """
    if stim is None:
        return {}
    if not isinstance(stim, dict):
        raise error("stim must be a dict {gpio: waveform}, not %r" % (stim,))
    for g, w in stim.items():
        _int(g, "stim GPIO", error)
        if not 0 <= g <= MAX_GPIO:
            raise error("stim GPIO %d is outside 0 to %d" % (g, MAX_GPIO))
        if not isinstance(w, str) or not _WAVE.fullmatch(w):
            raise error("the waveform of GPIO %d must be a non-empty string of 0 and 1, not %r\n  hint: --stim 0=0011" % (g, w))
    return stim


def check_words(words, error=ValueError, what="tx"):
    """Check TX words: integers from 0 to 2**32 - 1.

    Parameters
    ----------
    words : iterable of int
        The words.
    error : type
        The exception class to raise.
    what : str
        What they are called in the message.

    Returns
    -------
    tuple of int
        The words.
    """
    out = tuple(words)
    for w in out:
        _int(w, what + " word", error)
        if not 0 <= w <= MAX_WORD:
            raise error("%s word %d does not fit 32 bits (0 to %d)" % (what, w, MAX_WORD))
    return out


def check_tx_feed(feed, error=ValueError):
    """Check ``(cycle, word)`` pairs: a cycle of at least 0 and a 32-bit word.

    Parameters
    ----------
    feed : iterable of tuple
        The pairs.
    error : type
        The exception class to raise.

    Returns
    -------
    tuple of tuple
        The pairs.
    """
    out = tuple(feed)
    for item in out:
        try:
            c, w = item
        except (TypeError, ValueError):
            raise error("a tx_feed entry is a (cycle, word) pair, not %r" % (item,)) from None
        _int(c, "tx_feed cycle", error)
        if c < 0:
            raise error("tx_feed cycle %d is negative" % c)
        check_words([w], error, "tx_feed")
    return out


def check_clock_hz(hz, error=ValueError):
    """Check a state machine clock: None, or a finite number above 0.

    Parameters
    ----------
    hz : int, float or None
        The frequency in Hz.
    error : type
        The exception class to raise.

    Returns
    -------
    int, float or None
        `hz`.
    """
    if hz is None:
        return None
    if isinstance(hz, bool) or not isinstance(hz, (int, float)) or not math.isfinite(hz) or hz <= 0:
        raise error("the state machine clock must be a finite number of Hz above 0, not %r" % (hz,))
    return hz


def parse_stim(item):
    """Parse a ``--stim`` argument ``GPIO=WAVE``.

    Parameters
    ----------
    item : str
        For example ``"4=000111"``.

    Returns
    -------
    tuple
        ``(gpio, waveform)``.

    Raises
    ------
    UsageError
        If it is not that form, the GPIO is not 0 to 47 or the waveform is not a non-empty string of 0 and 1.

    Examples
    --------
    >>> from pioweave._validate import parse_stim
    >>> parse_stim("4=0011")
    (4, '0011')
    """
    g, sep, w = item.partition("=")
    try:
        if not sep:
            raise ValueError
        gpio = int(g)
    except ValueError:
        raise UsageError("--stim expects GPIO=WAVE, for example --stim 4=0011, not %r" % item) from None
    check_stim({gpio: w}, UsageError)
    return gpio, w


def parse_tx(text):
    """Parse a ``--tx`` argument: words separated by commas, decimal or ``0x...``.

    Parameters
    ----------
    text : str
        For example ``"0x55,0xC3"``.

    Returns
    -------
    list of int
        The words (each 0 to 2**32 - 1).

    Raises
    ------
    UsageError
        If a word is not a number or does not fit 32 bits.

    Examples
    --------
    >>> from pioweave._validate import parse_tx
    >>> parse_tx("0x55,3")
    [85, 3]
    """
    try:
        words = [int(w, 0) for w in text.split(",")]
    except ValueError:
        raise UsageError("--tx expects words separated by commas (decimal or 0x...), not %r" % text) from None
    return list(check_words(words, UsageError, "--tx"))


def parse_tx_feed(item):
    """Parse a ``--tx-feed`` argument ``CYCLE=WORD``.

    Parameters
    ----------
    item : str
        For example ``"5=0x41"``.

    Returns
    -------
    tuple
        ``(cycle, word)``.

    Raises
    ------
    UsageError
        If it is not that form or a value is out of range.

    Examples
    --------
    >>> from pioweave._validate import parse_tx_feed
    >>> parse_tx_feed("5=0x41")
    (5, 65)
    """
    c, sep, w = item.partition("=")
    try:
        if not sep:
            raise ValueError
        cycle, word = int(c), int(w, 0)
    except ValueError:
        raise UsageError("--tx-feed expects CYCLE=WORD, for example --tx-feed 5=0x41, not %r" % item) from None
    return check_tx_feed([(cycle, word)], UsageError)[0]
