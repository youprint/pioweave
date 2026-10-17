"""The generator API of the emulator (pioweave.emulator.trace): `emulate`, `State`, `count_cycles`, `pin_history`, `waveform_text`, `plot_waveform`. Needs no other tool; the matplotlib
test is skipped when matplotlib is missing."""
import dataclasses
import importlib.util
import sys
import unittest
from unittest import mock

from pioweave.builder import NOT_OSRE, PINS, X, X_DEC, Program
from pioweave.emulator import EmulationError, State, count_cycles, emulate, pin_history, plot_waveform, run_case, waveform_text

from . import support


def blink():
    p = Program("blink")
    with p.wrapped():
        p.set(PINS, 1, delay=1)
        p.set(PINS, 0, delay=1)
    return p


@support.cases
class EmulateTests(support.TestCase):
    def test_quick_start_counts_x_down_through_zero(self):
        # set x, 9 ; jmp x-- 1 ; wait 1 gpio 0: X runs 9, 8, ... 0 and the last jmp x-- decrements past zero to 0xFFFFFFFF (RP2040 3.4.2.2 p.323: the decrement always happens)
        pairs = list(emulate([0xE029, 0x0041, 0x2080], stop_when=lambda s: s.x == 0xFFFFFFFF))
        self.assertEqual([(b.x, a.x) for b, a in pairs], [(0, 9)] + [(v, v - 1) for v in range(9, 0, -1)] + [(0, 0xFFFFFFFF)])
        self.assertEqual([a.clock for _, a in pairs], list(range(1, 12)))
        self.assertEqual(pairs[0][0].clock, 0)

    def test_stop_when_can_look_at_the_next_opcode(self):
        words = [0xE021, 0xE042, 0xA042, 0xE063]                # set x, 1 ; set y, 2 ; nop ; set pins... stop before the nop
        steps = list(emulate(words, stop_when=lambda s: s.next_opcode == 0xA042))
        self.assertEqual((len(steps), steps[-1][1].pc, steps[-1][1].next_opcode), (2, 2, 0xA042))

    def test_states_are_frozen_comparable_and_printable(self):
        a = State(x=1)
        self.assertEqual(a, State(x=1))
        self.assertNotEqual(a, State(x=2))
        self.assertEqual(a.replace(y=5).y, 5)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            a.x = 2
        self.assertIn("x=1", repr(a))
        self.assertEqual(State().osr_count, 32)               # nothing left to shift out at reset (RP2040 3.5.4 p.338)

    def test_per_instruction_folds_delay_and_stall_cycles_into_the_step(self):
        p = Program("t")
        p.set(X, 1, delay=3)                                  # 4 cycles
        p.wait_gpio(1, 0)                                     # stalls until the pin is high
        p.nop()
        pairs = list(emulate(p, cycles=9, stim={0: "0000000111"}, sync_cycles=0))
        # set x: cycles 0 to 3 (one executed, three delay); wait: stalled in 4, 5, 6 and complete in 7; nop in 8
        self.assertEqual([(b.clock, a.clock) for b, a in pairs], [(0, 4), (4, 8), (8, 9)])
        self.assertEqual([a.kind for _, a in pairs], ["delay", "executed", "executed"])

    def test_per_cycle_yields_one_pair_per_system_clock_cycle(self):
        states = [a for _, a in emulate(blink(), cycles=4, per="cycle", set_base=0, set_count=1, dirs=1)]
        self.assertEqual([(a.clock, a.kind, a.pin(0)) for a in states], [(1, "executed", 1), (2, "delay", 1), (3, "executed", 0), (4, "delay", 0)])
        self.assertEqual([a.opcode is not None for a in states], [True, False, True, False][:1] + [True, True, True])

    def test_idle_cycles_of_a_clock_divider_are_reported(self):
        kinds = [a.kind for _, a in emulate(blink(), cycles=6, per="cycle", clkdiv=3, set_base=0, set_count=1, dirs=1)]
        self.assertEqual(kinds, ["idle", "idle", "executed", "idle", "idle", "delay"])

    def test_stop_when_sees_the_state_before_the_step_and_a_missing_stop_is_refused(self):
        seen = []
        list(emulate(blink(), stop_when=lambda s: seen.append(s.clock) or s.clock >= 4, per="cycle", set_base=0, set_count=1))
        self.assertEqual(seen, [0, 1, 2, 3, 4])
        with self.assertRaises(ValueError):
            list(emulate(blink()))
        with self.assertRaises(ValueError):
            list(emulate(blink(), cycles=3, per="step"))

    def test_max_cycles_stops_a_program_stuck_on_a_wait(self):
        p = Program("stuck")
        p.wait_gpio(1, 5)
        pairs = list(emulate(p, stop_when=lambda s: False, max_cycles=7))
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][1].clock, 7)
        self.assertEqual(pairs[0][1].kind, "stalled")

    def test_a_word_list_with_side_set_and_a_wrap_range(self):
        # nop side 1 ; nop side 0 ; set x, 5: wrap from 1 back to 1 (wrap_target 1, wrap_top 1): the third word never runs
        words = [0xA042 | 0x1000, 0xA042, 0xE025]
        steps = list(emulate(words, cycles=5, per="cycle", side=(1, False, False), sideset_base=0, dirs=1, wrap_target=1, wrap_top=1))
        self.assertEqual([a.pc for _, a in steps], [1, 1, 1, 1, 1])
        self.assertEqual(steps[0][1].pin(0), 1)
        self.assertEqual(steps[1][1].pin(0), 0)
        self.assertEqual(steps[-1][1].x, 0)

    def test_programs_wrap_to_the_start_by_default(self):
        steps = [a for _, a in emulate([0xE021, 0xE042], cycles=5, per="cycle")]           # set x, 1 ; set y, 2
        self.assertEqual([a.pc for a in steps], [1, 0, 1, 0, 1])

    def test_input_source_drives_pins_the_block_does_not_drive(self):
        # mov x, pins (0xA020) reads pin 0 as driven by the outside; the source is called before every cycle with the state at that moment
        calls = []

        def source(state):
            calls.append(state.clock)
            return 1 - (state.clock % 2)

        steps = [a for _, a in emulate([0xA020], cycles=4, per="cycle", input_source=source)]
        self.assertEqual([a.x & 1 for a in steps], [1, 0, 1, 0])
        self.assertEqual(calls, [0, 1, 2, 3])
        # a pin the state machine drives as an output is not overwritten by the outside (RP2040 3.5.6: the IN data comes from the pad)
        steps = [a for _, a in emulate([0xE001, 0xA020], cycles=4, per="cycle", input_source=lambda s: 0, set_base=0, set_count=1, dirs=1)]
        self.assertEqual(steps[-1].x & 1, 1)

    def test_stim_waveforms_as_in_run_case(self):
        steps = [a for _, a in emulate([0xA020], cycles=4, per="cycle", stim={0: "0110"})]
        self.assertEqual([a.x & 1 for a in steps], [0, 1, 1, 0])

    def test_initial_state_sets_registers_fifos_and_counters(self):
        start = State(x=7, y=9, osr=0xFF, osr_count=0, tx=(1, 2), rx=(5,), pc=1, clock=100)
        [(before, after)] = list(emulate([0xE021, 0xA027, 0xE042], cycles=1, initial_state=start))        # mov x, osr at pc 1
        self.assertEqual((before.x, before.y, before.tx, before.rx, before.pc, before.clock), (7, 9, (1, 2), (5,), 1, 100))
        self.assertEqual((after.x, after.pc, after.clock), (0xFF, 2, 101))

    def test_autopull_and_autopush_show_in_the_fifo_tuples_and_stalls(self):
        # out x, 16 ; in x, 16 with autopull at 16 bits and autopush at 32 bits (RP2040 3.5.4 p.338-339): the first OUT finds the OSR empty (count 32) and stalls while the
        # autopull refills it; the second OUT shifts out 0x5678 and refills the OSR from the second word at once; the 32nd bit shifted in pushes
        steps = [a for _, a in emulate([0x6030, 0x4030], cycles=6, per="cycle", tx=[0x12345678, 0x9ABCDEF0], autopull=True, autopush=True, pull_thresh=16, push_thresh=32)]
        self.assertEqual((steps[0].kind, steps[0].tx, steps[0].osr), ("stalled", (0x9ABCDEF0,), 0x12345678))
        self.assertEqual((steps[1].kind, steps[1].x, steps[1].tx, steps[1].osr_count), ("executed", 0x5678, (), 0))
        self.assertEqual(steps[2].isr, 0x56780000)
        self.assertEqual((steps[3].x, steps[4].isr, steps[4].rx, steps[4].isr_count), (0xDEF0, 0, (0xDEF05678,), 0))

    def test_undefined_programs_raise(self):
        with self.assertRaises(EmulationError):
            list(emulate([0xA004], cycles=1))            # mov with the reserved source 100

    def test_count_cycles(self):
        p = Program("delays")
        p.set(X, 2, delay=3)
        _ = p.label("loop")
        p.jmp("loop", when=X_DEC, delay=1)
        self.assertEqual(count_cycles(p, lambda s: s.x == 0xFFFFFFFF), 10)
        self.assertEqual(count_cycles(p, lambda s: s.clock >= 0), 0)
        with self.assertRaises(EmulationError):
            count_cycles(p, lambda s: False, max_cycles=50)

    def test_pin_history_and_waveform_text(self):
        hist = pin_history(blink(), 8, pins=[0, 1], set_base=0, set_count=1, dirs=3)
        self.assertEqual(hist, {0: [1, 1, 0, 0, 1, 1, 0, 0], 1: [0] * 8})
        self.assertEqual(waveform_text(hist, names={0: "clk", 1: "cs"}), "clk |##__##__|\ncs  |________|")
        self.assertEqual(waveform_text({0: [1, 0]}, scale=2, high="^", low="."), "pin0 |^^..|")
        res = run_case(blink(), 8, set_base=0, set_count=1, dirs=1)
        self.assertEqual(waveform_text(res), "pin0 |##__##__|")

    @unittest.skipUnless(importlib.util.find_spec("matplotlib"), "matplotlib is not installed")
    def test_plot_waveform_draws_with_matplotlib(self):
        import matplotlib
        matplotlib.use("Agg")
        ax = plot_waveform(pin_history(blink(), 8, pins=[0], set_base=0, set_count=1, dirs=1), names={0: "clk"})
        self.assertEqual([t.get_text() for t in ax.get_yticklabels()], ["clk"])

    def test_plot_waveform_without_matplotlib_says_so(self):
        with mock.patch.dict(sys.modules, {"matplotlib": None, "matplotlib.pyplot": None}):
            with self.assertRaisesRegex(ImportError, "needs matplotlib"):
                plot_waveform({0: [0, 1]})
