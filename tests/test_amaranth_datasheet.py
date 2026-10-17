# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Directed tests of the Amaranth data-path model whose expected values are worked out by hand from the RP2040 datasheet, not taken from the emulator or from bitweave's simulator.

Why they exist (audit COR-06): the Amaranth data path was written "exactly as the emulator", so the many random Amaranth-versus-emulator agreements prove only that the two
copies match each other. These designs run the Amaranth reference simulation alone (`cosim.amaranth_run`, no other tool needed) and compare it with numbers derived from the
datasheet sentences quoted in each test. They cannot prove the model right where the datasheet is silent, and the model has not been compared with a chip.
"""
from . import cosim, support
from .test_compile import compile_factory, data_design


def words(run):
    return [w for _, w in run["rx"]]


@support.cases
class AmaranthDatasheetTests(support.TestCase):
    def test_autopush_shifts_right_and_pushes_when_the_threshold_is_reached(self):
        # RP2040 3.4.4 (IN) and 3.5.4.1 (autopush): with the ISR shifting right, the bits shifted in arrive at the top: first x = 10 (0xA), then x = 5 for four bits each;
        # after the first IN the ISR is 0xA0000000, after the second 0x5A000000 (the first nibble moved down by four, the new one on top); the count 8 reaches PUSH_THRESH 8,
        # so the second IN pushes 0x5A000000 and clears the ISR.
        def body(m, sh, pl, io):
            x = pl.scratch("x")
            with m.FSM():
                with m.State("S1"):
                    m.d.sync += x.eq(10)
                    m.next = "I1"
                with m.State("I1"):
                    with sh.in_(m, "x", 4):
                        m.next = "S2"
                with m.State("S2"):
                    m.d.sync += x.eq(5)
                    m.next = "I2"
                with m.State("I2"):
                    with sh.in_(m, "x", 4):
                        m.next = "S1"
        a = cosim.amaranth_run(data_design(body, autopush=True, push_thresh=8), 5)
        self.assertEqual(words(a), [0x5A000000])

    def test_push_iffull_pushes_when_the_count_equals_the_threshold(self):
        # RP2040 3.4.6 (PUSH): "IfFull: if 1, do nothing unless the total input shift count has reached its threshold, SHIFTCTRL_PUSH_THRESH". 8 bits (x = 31 -> low byte 0x1F)
        # shifted into a right-shifting ISR give 0x1F000000 with count 8 = the threshold: the push happens. With 7 bits the count is below the threshold: no push.
        def body_for(n):
            def body(m, sh, pl, io):
                x = pl.scratch("x")
                with m.FSM():
                    with m.State("SET"):
                        m.d.sync += x.eq(31)
                        m.next = "IN"
                    with m.State("IN"):
                        sh.in_(m, "x", n)
                        m.next = "PUSH"
                    with m.State("PUSH"):
                        with sh.push(m, iffull=True):
                            m.next = "SET"
            return body
        self.assertEqual(words(cosim.amaranth_run(data_design(body_for(8), push_thresh=8), 3)), [0x1F000000])
        self.assertEqual(words(cosim.amaranth_run(data_design(body_for(7), push_thresh=8), 3)), [])

    def test_the_input_shift_counter_saturates_at_32(self):
        # RP2040 3.5.4: "the output shift counter and the input shift counter ... each capable of holding values from 0 to 32 inclusive". After in x,32 and in x,8 the counter is
        # 32 (not 40, not 31), so `push iffull` with the default threshold 32 pushes; the ISR is 0x1F000000 (x = 31 shifted in last for 8 bits, the earlier word moved down).
        def body(m, sh, pl, io):
            x = pl.scratch("x")
            with m.FSM():
                with m.State("SET"):
                    m.d.sync += x.eq(31)
                    m.next = "IN32"
                with m.State("IN32"):
                    sh.in_(m, "x", 32)
                    m.next = "IN8"
                with m.State("IN8"):
                    sh.in_(m, "x", 8)
                    m.next = "PUSH"
                with m.State("PUSH"):
                    with sh.push(m, iffull=True):
                        m.next = "SET"
        self.assertEqual(words(cosim.amaranth_run(data_design(body), 4)), [0x1F000000])

    def test_wait_1_irq_clears_the_flag_and_wait_0_irq_does_not(self):
        # RP2040 3.4.9: "WAIT 1 IRQ ... the flag is cleared when the wait completes" (only for polarity 1); `wait 0 irq` changes nothing. The host raises flag 3 at the start of
        # cycle 2 (value 0x08 in the flag mask from then on).
        def waiting(polarity):
            def body(m, sh, pl, io):
                with m.FSM():
                    with m.State("W"):
                        with sh.wait_irq(m, 3, polarity):
                            m.next = "DONE"
                    with m.State("DONE"):
                        m.next = "DONE"
            return body
        up = cosim.amaranth_run(data_design(waiting(1)), 6, irq_set=[(2, 3)])["irq"]
        self.assertEqual(up, [0, 0, 0, 0, 0, 0])               # raised in cycle 2, passed and cleared by the wait in the same cycle
        zero = cosim.amaranth_run(data_design(waiting(0)), 6, irq_set=[(2, 3)])["irq"]
        self.assertEqual(zero, [0, 0, 8, 8, 8, 8])             # stalls on the raised flag and leaves it up

    def test_a_state_that_is_a_jump_target_is_not_folded_into_a_delay(self):
        # A: pin 1;  B: nothing;  C: pin 0, then B if the input is high else A.   B follows A, so it could become A's delay, but C jumps to it: it needs an instruction of its own.
        # By hand, input 0010000 (cycle 2 reads 1): A(0) B(1) C(2, jumps to B) B(3) C(4, input 0, goes to A) A(5) B(6) C(7) A(8) B(9) C(10)
        # pin: 1 1 0 0 0 1 1 0 1 1 0
        def body(m, sh, pl, io):
            with m.FSM():
                with m.State("A"):
                    m.d.sync += io["side"].o.eq(1)
                    m.next = "B"
                with m.State("B"):
                    m.next = "C"
                with m.State("C"):
                    m.d.sync += io["side"].o.eq(0)
                    with m.If(io["in"].i):
                        m.next = "B"
                    with m.Else():
                        m.next = "A"
        factory = data_design(body)
        text = compile_factory(factory).program.pio_text()
        self.assertIn("s_B:\n    nop\n", text, text)
        self.assertEqual(cosim.amaranth_run(factory, 11, {4: "0010000"})["waves"][1], "11000110110")
