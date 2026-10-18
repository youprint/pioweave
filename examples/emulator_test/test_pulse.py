# Copyright (c) 2026 youprint. MIT licence (see ../../LICENSE.txt)
"""Unit tests of a PIO program in the emulator, with cycle counts.

    python -m unittest examples/emulator_test/test_pulse.py -v        (from the repository root)
    cd examples/emulator_test && python -m unittest -v

The program takes a count from the TX FIFO and makes a pulse on pin 0 that is high for count + 2 cycles. ``run_case`` runs it in the emulator for a number of cycles and returns
the pin levels after every cycle (``wave``), the words the host read, the registers and counters (``executed``, ``stall_cycles``, ``delay_cycles``). Nothing here needs a chip.
"""
import itertools
import unittest

from pioweave.builder import Program, PINS, X, X_DEC
from pioweave.emulator import run_case


def pulse_program():
    """pull a count, drive pin 0 high for count + 2 cycles (set, count + 1 passes of the jump), then low."""
    p = Program("pulse")
    p.pull()                       # blocks while the TX FIFO is empty
    p.out(X, 32)                   # the count into X
    p.set(PINS, 1)                 # cycle 1 of the pulse
    p.label("hold")
    p.jmp("hold", when=X_DEC)      # taken while X is not zero, then decrements: count + 1 executions
    p.set(PINS, 0)
    return p


def runs(wave):
    """(level, length) of every run in a waveform string."""
    return [(level, len(list(g))) for level, g in itertools.groupby(wave)]


class PulseTests(unittest.TestCase):
    def run_pulse(self, count, cycles):
        return run_case(pulse_program(), cycles, tx=[count], set_base=0, set_count=1, out_base=0, dirs=1)

    def test_pulse_length_is_count_plus_two(self):
        for count in (0, 1, 5, 20):
            with self.subTest(count=count):
                r = self.run_pulse(count, 60)
                # cycles 0 and 1: pull and out; the pin is low until the set at cycle 2
                high = [n for level, n in runs(r.wave(0)) if level == "1"]
                self.assertEqual(high, [count + 2])

    def test_instruction_count_matches(self):
        # pull, out, set, (count + 1) jumps, set: count + 5 instructions, one per cycle, and then the program wraps to the pull, which stalls
        r = self.run_pulse(7, 12)
        self.assertEqual(r.executed, 7 + 5)
        self.assertEqual(r.stall_cycles, 0)
        self.assertEqual(r.delay_cycles, 0)

    def test_an_empty_fifo_stalls_the_pull(self):
        r = run_case(pulse_program(), 10, set_base=0, set_count=1, out_base=0, dirs=1)
        self.assertEqual(r.wave(0), "0" * 10)
        self.assertEqual(r.stall_cycles, 10)        # the pull waits in every cycle
        self.assertEqual(r.executed, 0)

    def test_the_host_can_feed_the_fifo_later(self):
        r = run_case(pulse_program(), 30, tx_feed=[(10, 3)], set_base=0, set_count=1, out_base=0, dirs=1)
        self.assertEqual(r.stall_cycles, 10 + 12)   # 10 cycles before the word arrives, 12 more at the pull after the pulse (cycles 18 to 29)
        self.assertEqual(runs(r.wave(0))[:3], [("0", 12), ("1", 5), ("0", 13)])    # pull at cycle 10, out 11, set 12 ... 4 more passes


if __name__ == "__main__":
    unittest.main()
