# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../../LICENSE.txt)
"""A second opinion on a bitweave PIO simulation case: ``python -m pioweave.emulator.check [--max-cycles N] [--max-bytes N] PROGRAM_JSON CASE_JSON``.

`PROGRAM_JSON` is the file ``pioasm -o json`` writes. `CASE_JSON` is one simulation case of bitweave (a ``[[pio.NAME.sim]]`` table of bitweave.toml as a JSON object, the keys of
``PioSim`` in bitweave: ``cycles``, ``start_pc``, ``x``, ``y``, ``set_base``, ``fifo_join``, ``tx``, ``tx_feed``, ``pins_in``, ``expect`` and so on). The case runs in the emulator with
the semantics bitweave's simulator documents (one state machine, the 32-pin window, ``sync_cycles`` 0 unless the case says otherwise, the host drains the RX FIFO after every
cycle unless ``rx_drain`` is false) and ONE JSON object is printed on standard output:

``ok``
    True when the run did not end in an error and every expectation of the case (``expect``, ``expect_dirs``, ``expect_rx``, ``expect_rx_regs``, ``expect_tx_left``,
    ``expect_x``, ``expect_y``, ``expect_pc``, ``expect_irq``) holds.
``error``
    The message when the configuration is invalid or the program does something the hardware leaves undefined (``cycle N: ...``), else null. ``error_cycle`` is the cycle
    of a run-time error, else null.
``cycles``
    The number of cycles of the case.
``pads``, ``dirs``
    ``{pin: waveform}``, one character (0 or 1) per cycle run, for every pin in the ``expect`` and ``expect_dirs`` keys of the case and every pin the program drives.
``rx``, ``rx_left``, ``tx_left``
    The words the draining host read (``[{"cycle", "value"}]``), the words still in the RX FIFO, and the number of words left in the TX FIFO.
``final``
    ``{"x", "y", "pc", "isr", "osr", "irq_flags"}`` at the end (``pc`` relative to the program, as an unsigned 32-bit number).
``unsupported``
    The keys of the case this emulator does not know. They are not applied; a caller must not trust the result for them.
``mismatches``
    The first expectations that did not hold.

The exit status is 0 whenever a result was produced, even if the program errored (that is data, and so is a field of the case with the wrong shape: ``error`` says which), and 2
on a usage error (message on standard error): an input that is not JSON, is nested too deeply or is over ``--max-bytes`` (default 16 MiB), a case without an integer ``cycles`` or
with more cycles than ``--max-cycles`` (default 1,000,000). A run takes time and memory in proportion to the cycles.
"""
import json
import sys

from .._validate import DEFAULT_MAX_CYCLES
from .core import EmulationError, PioBlock

__all__ = ["check_case", "main", "SIMULATION_KEYS", "EXPECTATION_KEYS", "DEFAULT_MAX_CYCLES", "DEFAULT_MAX_BYTES"]

DEFAULT_MAX_BYTES = 16 * 1024 * 1024
"""The largest input file `main` reads (16 MiB); ``--max-bytes N`` raises it."""

#: The keys of a case that change what is simulated (all of them have a counterpart in the emulator).
SIMULATION_KEYS = frozenset((
    "program", "cycles", "start_pc", "x", "y", "in_base", "in_count", "out_base", "out_count", "set_base", "set_count", "sideset_base", "jmp_pin", "in_shift", "out_shift",
    "autopush", "autopull", "push_thresh", "pull_thresh", "fifo_join", "status_sel", "status_n", "tx", "tx_feed", "rx_drain", "rx_regs", "dirs", "pins_in", "irq_set",
    "irq_clear", "sync_cycles", "gpio_base", "sm"))
#: The keys that state what is expected (judged here, and again by the caller) and keys that do not change the result (``name``, ``trace``).
EXPECTATION_KEYS = frozenset(("expect", "expect_dirs", "expect_rx", "expect_rx_regs", "expect_tx_left", "expect_x", "expect_y", "expect_pc", "expect_irq", "name", "trace"))

_MASK32 = 0xFFFFFFFF


def _pin_of(key):
    text = str(key)
    for prefix in ("gpio", "GPIO"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    try:
        pin = int(text)
    except ValueError:
        pin = -1
    if not 0 <= pin < 32:
        raise ValueError("pin %r must be a number from 0 to 31" % (key,))
    return pin


def _program(programs, name):
    """The program called `name` (None: the first) of a pioasm JSON object."""
    items = programs.get("programs") if isinstance(programs, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("the pioasm JSON has no program")
    if name is None:
        if not isinstance(items[0], dict):
            raise ValueError("the pioasm JSON has no program")
        return items[0]
    for item in items:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    raise ValueError("program %r is not in the pioasm JSON" % (name,))


def _words(prog):
    out = []
    for ins in prog["instructions"]:
        out.append(int(ins["hex"], 16) & 0xFFFF)
    return out


def _wave(series, pin):
    return "".join("1" if (v >> pin) & 1 else "0" for v in series)


def _stim_at(stim, t):
    v = 0
    for pin, w in stim:
        if w[min(t, len(w) - 1)] == "1":
            v |= 1 << pin
    return v


def _expectations(case, pads, dirs, rx, rx_left, rx_regs, tx_left, final):
    """The mismatches of the run against the expectations of the case (the semantics of bitweave's simulator: ``.`` and ``x`` are do-not-care; ``expect_rx`` lists the words read
    by the host followed by the words left in the RX FIFO)."""
    found = []
    for what, key, series in (("pin", "expect", pads), ("dir", "expect_dirs", dirs)):
        for k, w in (case.get(key) or {}).items():
            pin = _pin_of(k)
            if not all(ch in "01.xX" for ch in w):
                raise ValueError("%s[%s] may contain only 0, 1, . and x" % (key, k))
            if len(w) > len(series):
                found.append("%s %d: expectation has %d cycles but the run has %d" % (what, pin, len(w), len(series)))
            for t, ch in enumerate(w[:len(series)]):
                if ch in "01" and ((series[t] >> pin) & 1) != int(ch):
                    found.append("%s %d cycle %d: expected %s got %d" % (what, pin, t, ch, (series[t] >> pin) & 1))
    if case.get("expect_rx") is not None:
        got = [v for _, v in rx] + list(rx_left)
        if got != list(case["expect_rx"]):
            found.append("rx words: expected %s got %s" % (list(case["expect_rx"]), got))
    if case.get("expect_rx_regs") is not None:
        want = list(case["expect_rx_regs"])
        if any(a != b for a, b in zip(want, rx_regs)) or len(want) > 4:
            found.append("rx fifo registers: expected %s got %s" % (want, list(rx_regs)))
    for name, key, got in (("tx_left", "expect_tx_left", tx_left), ("x", "expect_x", final["x"]), ("y", "expect_y", final["y"]), ("pc", "expect_pc", final["pc"]),
                           ("irq flags", "expect_irq", final["irq_flags"])):
        if case.get(key) is not None and case[key] != got:
            found.append("%s: expected %s got %s" % (name, case[key], got))
    return found


def check_case(programs, case, max_cycles=DEFAULT_MAX_CYCLES):
    """Run one bitweave simulation case in the emulator and describe the result.

    Parameters
    ----------
    programs : dict
        The JSON object ``pioasm -o json`` writes (a ``programs`` list with ``name``, ``pioVersion``, ``wrapTarget``, ``wrap``, ``origin``, ``sideset`` and ``instructions``).
    case : dict
        A simulation case with the keys of bitweave's ``PioSim``; keys with the value null are treated as absent.
    max_cycles : int
        The ceiling of the case's ``cycles`` (default 1,000,000): a run takes time and memory in proportion to it.

    Returns
    -------
    dict
        The result described in the module documentation: ``ok``, ``error``, ``error_cycle``, ``cycles``, ``pads``, ``dirs``, ``rx``, ``rx_left``, ``tx_left``, ``final``,
        ``unsupported``, ``mismatches``.

    Raises
    ------
    ValueError
        If the programs object has no program, or the case names a program that is not in it, or has no ``cycles``, or ``cycles`` is not an integer from 0 to `max_cycles`.

    Examples
    --------
    A program that sets pin 0 high, then low, with one delay cycle each (``set pins, 1 [1]`` and ``set pins, 0 [1]``):

    >>> from pioweave.emulator.check import check_case
    >>> programs = {"programs": [{"name": "square", "pioVersion": 0, "wrapTarget": 0, "wrap": 1, "origin": -1, "sideset": {"size": 0, "optional": False, "pindirs": False},
    ...                           "instructions": [{"hex": "e101"}, {"hex": "e100"}]}]}
    >>> r = check_case(programs, {"cycles": 8, "set_base": 0, "set_count": 1, "dirs": 1, "expect": {"0": "11xx11xx"}})
    >>> (r["ok"], r["error"], r["pads"]["0"], r["unsupported"])
    (True, None, '11001100', [])
    >>> check_case(programs, {"cycles": 8, "dirs": 1, "colour": "red", "expect": {"0": "1"}})["unsupported"]
    ['colour']
    """
    if isinstance(case, dict):
        case = {k: v for k, v in case.items() if v is not None}
    if not isinstance(case, dict) or "cycles" not in case:
        raise ValueError("the case needs a `cycles` key")
    unsupported = sorted(k for k in case if k not in SIMULATION_KEYS and k not in EXPECTATION_KEYS)
    prog = _program(programs, case.get("program"))
    try:
        cycles = int(case["cycles"])
        version = int(prog.get("pioVersion", 0))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("`cycles` must be an integer and pioVersion a number") from None
    if not 0 <= cycles <= max_cycles:
        raise ValueError("`cycles` is %d: it must be from 0 to %d (--max-cycles N raises the ceiling)" % (cycles, max_cycles))
    res = {"ok": False, "error": None, "error_cycle": None, "cycles": cycles, "pads": {}, "dirs": {}, "rx": [], "rx_left": [], "tx_left": 0,
           "final": {"x": 0, "y": 0, "pc": 0, "isr": 0, "osr": 0, "irq_flags": 0}, "unsupported": unsupported, "mismatches": []}
    pads, dirs, rx_log, rx_left, rx_regs, tx_left, final = [], [], [], [], [0, 0, 0, 0], 0, res["final"]
    try:
        words = _words(prog)
        origin = prog.get("origin", -1)
        load = origin if origin is not None and origin >= 0 else 0
        if load + len(words) > 32:
            raise ValueError("program %s has %d instructions and does not fit the 32-word instruction memory at offset %d" % (prog.get("name"), len(words), load))
        gpio_base = int(case.get("gpio_base", 0))
        if gpio_base != 0 and (gpio_base != 16 or version == 0):
            raise ValueError("gpio_base %d is not valid: 0, or 16 on PIO version 1 (RP2350 / RP2354 B variants)" % gpio_base)
        ranges = int(prog.get("usedGPIORanges", 0))
        if (gpio_base != 0 and ranges & 1) or (gpio_base == 0 and ranges & 4):
            raise ValueError("the program waits on GPIOs %s but gpio_base is %d" % ("32-47" if ranges & 4 else "0-15", gpio_base))
        side = prog.get("sideset") or {}
        block = PioBlock(version=version, gpio_base=gpio_base, sync_cycles=int(case.get("sync_cycles", 0)))
        loaded = block.load(words, offset=load, side=(int(side.get("size", 0)), bool(side.get("optional", False)), bool(side.get("pindirs", False))))
        loaded.wrap_target, loaded.wrap = int(prog.get("wrapTarget", 0)), int(prog.get("wrap", 0))
        sm = block.sm[int(case.get("sm", 0)) & 3]
        settings = {}
        for key in ("in_base", "out_base", "set_base", "jmp_pin"):
            if key in case:
                settings[key] = int(case[key]) & 31
        if "sideset_base" in case:
            settings["side_base"] = int(case["sideset_base"]) & 31
        for key in ("out_count", "autopush", "autopull", "push_thresh", "pull_thresh", "fifo_join", "status_sel", "status_n", "in_shift", "out_shift"):
            if key in case:
                settings[key] = case[key]
        # a case describes a state machine configured by the SDK, whose default configuration has SET_COUNT 0 (pio_sm_init writes PINCTRL from it); the register reset value is 5
        settings["set_count"] = int(case.get("set_count", 0))
        if not 0 <= settings["set_count"] <= 5:
            raise ValueError("set_count is at most 5")
        if "in_count" in case:
            if not 1 <= int(case["in_count"]) <= 32:
                raise ValueError("in_count must be 1..32")
            settings["in_count"] = int(case["in_count"])
        sm.configure(program=loaded, **settings)
        sm.enable()
        sm.set_pc(load + int(case.get("start_pc", 0)))
        sm.x, sm.y = int(case.get("x", 0)) & _MASK32, int(case.get("y", 0)) & _MASK32
        block.dirs = int(case.get("dirs", 0)) & _MASK32
        for i, w in enumerate(case.get("rx_regs") or []):
            if i < 4:
                sm.rx_regs[i] = int(w) & _MASK32
        for w in case.get("tx") or []:
            sm.put(int(w))
        stim = []
        for k, w in (case.get("pins_in") or {}).items():
            if not w or not all(ch in "01" for ch in w):
                raise ValueError("pins_in[%s] must be a non-empty string of 0 and 1" % k)
            stim.append((_pin_of(k), w))
        feeds = [(int(f["cycle"]), int(f["value"])) for f in case.get("tx_feed") or []]
        irq_clear = [(int(e["cycle"]), int(e["flag"])) for e in case.get("irq_clear") or []]
        irq_set = [(int(e["cycle"]), int(e["flag"])) for e in case.get("irq_set") or []]
        drain = bool(case.get("rx_drain", True))
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError) as e:
        res["error"] = "invalid case: %s" % (e,) if not isinstance(e, ValueError) else str(e)
        return res
    run_error = None
    for t in range(cycles):
        for c, w in feeds:
            if c == t:
                sm.put(w)
        block.irq_clear(sum(1 << (f & 7) for c, f in irq_clear if c == t))
        block.irq_force(sum(1 << (f & 7) for c, f in irq_set if c == t))
        block.ext = _stim_at(stim, t)
        try:
            block.step()
        except EmulationError as e:
            run_error = (t, str(e))
            break
        if drain:
            while len(sm.rx):
                rx_log.append((t, sm.get()))
        pads.append(block.pads())
        dirs.append(block.dirs)
    rx_left, tx_left, rx_regs = list(sm.rx.q), len(sm.tx), list(sm.rx_regs)
    final.update(x=sm.x, y=sm.y, pc=(sm.pc - loaded.offset) & _MASK32, isr=sm.isr, osr=sm.osr, irq_flags=block.irq)
    pins = set()
    for key in ("expect", "expect_dirs"):
        expected = case.get(key)
        for k in (expected if isinstance(expected, dict) else ()):
            try:
                pins.add(_pin_of(k))
            except ValueError:
                pass
    pins.update(p for p in range(32) if any((d >> p) & 1 for d in dirs))
    res["pads"] = {str(p): _wave(pads, p) for p in sorted(pins)}
    res["dirs"] = {str(p): _wave(dirs, p) for p in sorted(pins)}
    res["rx"] = [{"cycle": c, "value": v} for c, v in rx_log]
    res["rx_left"], res["tx_left"] = rx_left, tx_left
    if run_error:
        res["error_cycle"], res["error"] = run_error[0], "cycle %d: %s" % run_error
        return res
    try:
        res["mismatches"] = _expectations(case, pads, dirs, rx_log, rx_left, rx_regs, tx_left, final)[:8]
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError) as e:
        res["error"] = "invalid case: %s" % (e,) if not isinstance(e, ValueError) else str(e)
        return res
    res["ok"] = not res["mismatches"]
    return res


def _read_json(path, max_bytes):
    """Read a JSON file of at most `max_bytes`; raises ValueError (or OSError) with a message."""
    with open(path, "rb") as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("%d bytes or more: over the limit of %d (--max-bytes N raises it)" % (len(data), max_bytes))
    try:
        return json.loads(data.decode("utf-8"))
    except RecursionError:
        raise ValueError("the JSON is nested too deeply") from None


def _split_options(args):
    """The positional arguments and the numbers of ``--max-cycles`` and ``--max-bytes`` (``--max-cycles N`` or ``--max-cycles=N``); ValueError on a bad option."""
    limits, positional, k = {"--max-cycles": DEFAULT_MAX_CYCLES, "--max-bytes": DEFAULT_MAX_BYTES}, [], 0
    while k < len(args):
        a = args[k]
        key, eq, val = a.partition("=")
        if key in limits:
            if not eq:
                k += 1
                val = args[k] if k < len(args) else ""
            try:
                limits[key] = int(val)
            except ValueError:
                raise ValueError("%s expects a whole number, not %r" % (key, val)) from None
            if limits[key] < 1:
                raise ValueError("%s must be at least 1" % key)
        elif a.startswith("--"):
            raise ValueError("unknown option %s" % a)
        else:
            positional.append(a)
        k += 1
    return positional, limits["--max-cycles"], limits["--max-bytes"]


def main(argv=None):
    """The command line: ``python -m pioweave.emulator.check [--max-cycles N] [--max-bytes N] PROGRAM_JSON CASE_JSON``. Prints one JSON object (see the module documentation) and
    returns 0, or returns 2 on a usage error (wrong number of arguments, a file that cannot be read, is not JSON, is nested too deeply or is larger than ``--max-bytes``
    (default 16 MiB), a case without an integer ``cycles`` or with more than ``--max-cycles`` (default 1,000,000), a program name that is not in the file).

    Parameters
    ----------
    argv : list of str or None
        The arguments after the program name; None takes ``sys.argv[1:]``.

    Returns
    -------
    int
        The exit status.

    Examples
    --------
    >>> import contextlib, json, os, sys, tempfile
    >>> from pioweave.emulator.check import main
    >>> with tempfile.TemporaryDirectory() as d:
    ...     p, c = os.path.join(d, "p.json"), os.path.join(d, "c.json")
    ...     with open(p, "w") as fh:
    ...         _ = json.dump({"programs": [{"name": "w", "pioVersion": 0, "wrapTarget": 0, "wrap": 0, "instructions": [{"hex": "e03f"}]}]}, fh)
    ...     with open(c, "w") as fh:
    ...         _ = json.dump({"cycles": 2}, fh)
    ...     status = main([p, c])
    {"ok": true, "error": null, "error_cycle": null, "cycles": 2, "pads": {}, "dirs": {}, "rx": [], "rx_left": [], "tx_left": 0, "final": {"x": 31, "y": 0, "pc": 0, "isr": 0, "osr": 0, "irq_flags": 0}, "unsupported": [], "mismatches": []}
    >>> status
    0
    >>> with contextlib.redirect_stderr(sys.stdout):
    ...     main(["only-one-argument"])
    pioweave.emulator.check: usage: python -m pioweave.emulator.check [--max-cycles N] [--max-bytes N] PROGRAM_JSON CASE_JSON
    2
    """
    args = sys.argv[1:] if argv is None else list(argv)

    def usage(message):
        print("pioweave.emulator.check: " + message, file=sys.stderr)
        return 2

    try:
        args, max_cycles, max_bytes = _split_options(args)
    except ValueError as e:
        return usage(str(e))
    if len(args) != 2:
        return usage(_USAGE)
    loaded = []
    for path in args:
        try:
            loaded.append(_read_json(path, max_bytes))
        except (OSError, ValueError) as e:
            return usage("cannot read %s: %s" % (path, e))
    try:
        result = check_case(loaded[0], loaded[1], max_cycles)
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError) as e:
        return usage(str(e))
    print(json.dumps(result))
    return 0


_USAGE = "usage: python -m pioweave.emulator.check [--max-cycles N] [--max-bytes N] PROGRAM_JSON CASE_JSON"

if __name__ == "__main__":
    sys.exit(main())
