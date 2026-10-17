"""Behaviours that another PIO emulator (rp2040-pio-emulator, Apache-2.0) documents as supported, or lists as limitations, written fresh for this emulator from the datasheets
(docs/design-notes/emulator-comparison.rst). Expected values are worked out from RP2040 chapter 3 and RP2350 chapter 11; none of it was copied from that project's code or tests."""
from pioweave.builder import ISR, NULL, PINDIRS, PINS, X, X_DEC, Program
from pioweave.emulator import PioBlock, emulate, run_case

from . import support


def prog(chip="rp2040", **kw):
    return Program("t", chip=chip, **kw)


@support.cases
class ParityTests(support.TestCase):
    # ----------------------------------------------------------------------------- pin sets wrap after GPIO 31 and IN is relative to IN_BASE
    def test_set_out_and_side_set_ranges_wrap_after_gpio_31(self):
        # RP2040 3.5.6 p.341: the mapping "continues for COUNT bits, wrapping after GPIO31"
        p = prog(side_set=2)
        p.set(PINS, 0b1010, side=0b01)
        p.set(PINDIRS, 0b1111, side=0b00)
        r = run_case(p, 2, set_base=30, set_count=4, sideset_base=31, record=True)
        self.assertEqual(r.dirs[1] & 0xC0000003, 0xC0000003)                      # set pindirs on 30, 31, 0, 1; side 0 on 31, 0 leaves the directions to SET
        q = prog()
        q.pull()
        q.out(PINS, 4)
        r = run_case(q, 2, tx=[0b1011], out_base=29, out_count=4, dirs=0xFFFFFFFF)
        self.assertEqual((r.pads[1] >> 29) & 7, 0b011)
        self.assertEqual(r.pads[1] & 1, 1)

    def test_side_set_on_high_gpios(self):
        p = prog(side_set=1)
        p.nop(side=1)
        r = run_case(p, 1, sideset_base=20, dirs=1 << 20)
        self.assertEqual(r.pads[0], 1 << 20)

    def test_in_pins_are_rotated_by_in_base_and_wrap(self):
        # RP2040 3.5.6.2 p.342: the IN bus is a right-rotate of the GPIO levels by IN_BASE
        p = prog()
        p.in_(PINS, 4)
        r = run_case(p, 1, stim={30: "1", 31: "0", 0: "1", 1: "1"}, in_base=30, in_shift="left")
        self.assertEqual(r.isr, 0b1101)                                           # bit 0 = pin 30, bit 1 = pin 31, bit 2 = pin 0, bit 3 = pin 1

    # ----------------------------------------------------------------------------- thresholds of PULL IFEMPTY and PUSH IFFULL
    def test_pull_ifempty_and_push_iffull_use_the_thresholds(self):
        # RP2040 3.4.6.2 p.327 / 3.4.7.2 p.328: IfFull / IfEmpty compare with PUSH_THRESH / PULL_THRESH, the thresholds of autopush and autopull
        p = prog()
        p.in_(X, 8)
        p.push(iffull=True, block=False)                 # 8 bits shifted in, threshold 16: does nothing
        p.in_(X, 8)
        p.push(iffull=True, block=False)                 # 16 bits: pushes
        r = run_case(p, 4, x=0xAB, push_thresh=16, drain=False)
        self.assertEqual(r.rx_left, [0xABAB0000])
        q = prog()
        q.pull(block=False)
        q.out(X, 8)
        q.pull(ifempty=True, block=False)                # 8 bits out, threshold 16: no pull
        q.out(X, 8)
        q.pull(ifempty=True, block=False)                # 16 bits out: pulls the second word
        r = run_case(q, 5, tx=[0x1111, 0x2222], pull_thresh=16)
        self.assertEqual((r.osr, r.tx_left), (0x2222, 0))

    # ----------------------------------------------------------------------------- stalls and delays
    def test_a_stalled_instruction_keeps_the_pc_and_costs_one_cycle_per_retry(self):
        p = prog()
        p.wait_gpio(1, 4)
        p.set(X, 1)
        r = run_case(p, 6, stim={4: "000011"}, sync_cycles=0, record=True)
        self.assertEqual([row["sms"][0]["pc"] for row in r.trace], [0, 0, 0, 0, 1, 0])           # pc after each cycle: stalls keep it
        self.assertEqual(r.stall_cycles, 4)

    def test_instruction_cost_is_one_plus_delay_and_a_taken_jmp_delay_applies(self):
        # RP2040 3.4.1 p.322 and 3.4.2.2 p.323: delay cycles on a JMP always take effect, taken or not
        p = prog()
        p.set(X, 1)
        _ = p.label("l")
        p.jmp("l", when=X_DEC, delay=2)
        steps = list(emulate(p, cycles=10, per="instruction"))
        self.assertEqual([a.clock - b.clock for b, a in steps][:4], [1, 3, 3, 1])                    # set x; jmp x-- taken (x 1 -> 0); jmp x-- not taken (x 0 -> -1), both with their 2 delay cycles; then the wrap to set x

    # ----------------------------------------------------------------------------- several state machines in one block
    def test_two_state_machines_synchronise_through_an_irq_flag(self):
        # the other emulator runs one state machine; here two: SM0 raises flag 2 after a delay, SM1 waits for it, clears it and sets a pin
        a, b = prog(), prog()
        a.irq(2, delay=5)
        a.jmp(0)
        b.wait_irq(1, 2)
        b.set(PINS, 1)
        block = PioBlock(sync_cycles=0)
        block.sm[0].configure(program=block.load(a)).enable()
        block.sm[1].configure(program=block.load(b), set_base=0, set_count=1).enable()
        block.dirs = 1
        block.recording = True
        block.run(4)
        self.assertEqual([row["pads"] & 1 for row in block.trace], [0, 0, 1, 1])                # flag raised at the end of cycle 0, seen and cleared in cycle 1, pin set in cycle 2

    # ----------------------------------------------------------------------------- shift registers
    def test_shift_directions_of_in_and_out(self):
        # RP2040 3.4.4.2 p.326: IN first shifts the ISR to make room, then copies the data into the gap; OUT writes the low (right shift) or high (left shift) bits
        p = prog()
        p.in_(X, 4)
        p.in_(X, 4)
        r = run_case(p, 2, x=0xA, in_shift="left")
        self.assertEqual(r.isr, 0xAA)
        r = run_case(p, 2, x=0xA, in_shift="right")
        self.assertEqual(r.isr, 0xAA000000)
        q = prog()
        q.pull()
        q.out(X, 4)
        r = run_case(q, 2, tx=[0x12345678], out_shift="left")
        self.assertEqual((r.x, r.osr), (0x1, 0x23456780))
        r = run_case(q, 2, tx=[0x12345678], out_shift="right")
        self.assertEqual((r.x, r.osr), (0x8, 0x01234567))

    def test_out_and_mov_to_isr_set_the_counters_and_null_is_zero(self):
        # RP2040 3.5.4 p.338: OUT ISR, n sets the ISR shift counter to n; MOV ISR / MOV OSR clear the counters
        p = prog()
        p.pull()
        p.out(ISR, 5)
        r = run_case(p, 2, tx=[0b10110], record=True)
        self.assertEqual((r.isr, r.trace[-1]["sms"][0]["isr_count"], r.trace[-1]["sms"][0]["osr_count"]), (0b10110, 5, 5))
        q = prog()
        q.mov(ISR, NULL)
        r = run_case(q, 1)
        self.assertEqual(r.isr, 0)
