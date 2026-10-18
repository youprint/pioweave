"""Tests of the emulator facts that were [verify] and are now taken from the datasheets. Every test names the section and the printed page it follows: RP2040 datasheet (build-date
2025-02-20, build-version 3184e62-clean) and RP2350 datasheet (Release 8, build date 01/10/2026, build version 80d627281ed5). The expected values are worked out from those
sentences, not from another implementation. Nothing here has run on a chip."""
from pioweave.builder import (CLEAR, EXEC, NULL, PINDIRS, PINS, SET, STATUS, WAIT, X, X_DEC, Y, Program)
from pioweave.emulator import EmulationError, PioBlock, run_case

from . import support


def prog(chip="rp2040", **kw):
    return Program("t", chip=chip, **kw)


def word(build, chip="rp2040", **kw):
    """The 16-bit word of the single instruction `build(p)` adds."""
    p = prog(chip, **kw)
    build(p)
    for _ in range(8):                              # room for the target of a jmp
        p.nop(**({"side": 0} if kw.get("side_set") else {}))
    return p.words()[0] & 0xFFFF


def block_with(programs, version=0, **kw):
    """A block with one program per state machine, state machine i running programs[i] (None: not used); returns (block, loaded list)."""
    block = PioBlock(version=version, **kw)
    loaded = []
    for i, p in enumerate(programs):
        loaded.append(None if p is None else block.load(p))
    return block, loaded


@support.cases
class DatasheetTests(support.TestCase):
    # ----------------------------------------------------------------------------- reset values
    def test_reset_values_of_the_configuration(self):
        # RP2040 Table 383 p.377 and RP2350 Table 1053 p.857: OUT_SHIFTDIR = IN_SHIFTDIR = 1 (right), PULL_THRESH = PUSH_THRESH = 0 (= 32), no autopull / autopush, FJOIN 0;
        # Table 386 p.378 / Table 1056 p.859: SET_COUNT = 5, OUT_COUNT = 0; Table 381 p.376: CLKDIV_INT = 1; Table 376 p.374: INPUT_SYNC_BYPASS = 0 (synchronised)
        block = PioBlock()
        c = block.sm[0].cfg
        self.assertTrue(c.in_right and c.out_right and not c.autopush and not c.autopull)
        self.assertEqual((c.push_thresh, c.pull_thresh, c.set_count, c.out_count, c.clkdiv, c.fifo_join), (32, 32, 5, 0, 1.0, "none"))
        self.assertEqual((block.sync_bypass, block.sync_cycles), (0, 2))
        # the shift counters after reset (RP2040 3.5.4 p.338): ISR 0, OSR 32 (nothing left to shift out)
        self.assertEqual((block.sm[0].isr_count, block.sm[0].osr_count), (0, 32))

    def test_default_shift_directions_are_right(self):
        p = prog()
        p.pull()
        p.out(X, 4)                                 # right: the four least significant bits of the OSR
        p.in_(X, 4)                                 # right: data enters from the left, so x lands in ISR bits 31..28
        r = run_case(p, 3, tx=[0x12345678])
        self.assertEqual((r.x, r.isr), (0x8, 0x80000000))

    def test_set_count_resets_to_five(self):
        p = prog()
        p.set(PINS, 31)
        block = PioBlock()
        block.sm[0].configure(program=block.load(p)).enable()          # no set_count given
        block.dirs = 0xFFFFFFFF
        block.run(1)
        self.assertEqual(block.out_level, 31)

    # ----------------------------------------------------------------------------- output priority between state machines and inside one
    def test_per_pin_priority_highest_state_machine_levels_and_directions_apart(self):
        # RP2040 3.5.6.1 p.341-342: for each GPIO the write of the highest-numbered state machine wins, separately for levels and directions; a write by one state machine to
        # the level and by another to the direction of the same pin both happen; with no write the value does not change
        a, b, c = prog(), prog(), prog()
        a.set(PINS, 0b11)                           # SM0 writes the levels of pins 0 and 1
        b.set(PINS, 0b0)                            # SM1 writes the level of pin 1 only (set_count 1 at base 1)
        c.set(PINDIRS, 0b1)                         # SM2 writes the direction of pin 1
        block = PioBlock()
        block.sm[0].configure(program=block.load(a), set_base=0, set_count=2).enable()
        block.sm[1].configure(program=block.load(b), set_base=1, set_count=1).enable()
        block.sm[2].configure(program=block.load(c), set_base=1, set_count=1).enable()
        block.run(1)
        self.assertEqual(block.out_level & 3, 0b01)         # pin 0 from SM0, pin 1 from SM1 (SM1 > SM0)
        self.assertEqual(block.dirs & 3, 0b10)              # the direction of pin 1 from SM2; pin 0 unchanged
        block.run(1)                                        # nothing changes without writes: the program ran off the end, wrapping to the same writes, same result
        self.assertEqual((block.out_level & 3, block.dirs & 3), (0b01, 0b10))

    def test_higher_state_machine_wins_whatever_the_order_of_loading(self):
        low, high = prog(), prog()
        low.set(PINS, 1)
        high.set(PINS, 0)
        block = PioBlock()
        block.sm[3].configure(program=block.load(low), set_base=0, set_count=1).enable()       # SM3 writes 1
        block.sm[1].configure(program=block.load(high), set_base=0, set_count=1).enable()      # SM1 writes 0
        block.run(1)
        self.assertEqual(block.out_level & 1, 1)

    def test_side_set_beats_out_and_set_of_the_same_state_machine_and_levels_beat_nothing_on_directions(self):
        # RP2040 3.5.1 p.332 and 3.5.6 p.341: if side-set and an OUT or SET write the same pin simultaneously, the side-set data is used; with side-set on the directions it
        # is a separate write (levels and directions are independent)
        p = prog(side_set=1, side_pindirs=True)
        p.set(PINS, 1, side=0)                      # SET writes the level 1; side-set writes the direction 0 of the same pin
        block = PioBlock()
        block.sm[0].configure(program=block.load(p), set_base=0, set_count=1, side_base=0).enable()
        block.dirs = 1
        block.run(1)
        self.assertEqual((block.out_level & 1, block.dirs & 1), (1, 0))
        q = prog(side_set=1)
        q.set(PINS, 1, side=0)
        r = run_case(q, 1, set_base=0, set_count=1, sideset_base=0, dirs=1)
        self.assertEqual(r.wave(0), "0")

    # ----------------------------------------------------------------------------- the input synchroniser and the delays of the pin path
    def test_synchroniser_is_two_flops_and_bypassed_per_gpio(self):
        # RP2040 3.5.6.3 p.342 and INPUT_SYNC_BYPASS Table 376 p.374: "a standard 2-flipflop synchroniser" adds two cycles of latency, engaged by default (0 -> synchronised);
        # a 1 bit bypasses it for that GPIO
        p = prog()
        p.mov(X, PINS)
        block = PioBlock()
        sm = block.sm[0].configure(program=block.load(p)).enable()
        block.sync_bypass = 0b01                                       # pin 0 bypassed, pin 1 synchronised
        block.ext = 0b11
        block.recording = True
        block.run(4)
        self.assertEqual([row["sms"][0]["x"] for row in block.trace], [0b01, 0b01, 0b11, 0b11])

    def test_own_outputs_seen_after_two_or_four_cycles_in_datasheet_mode(self):
        # RP2040 3.5.6.1 p.342: "a state machine observing its own outputs is subject to ... when bypassing synchronisers, a two-cycle delay; when synchronisers are engaged,
        # a four-cycle delay" (the option loopback="datasheet"; the default keeps the convention of bitweave's simulator, one cycle)
        def seen(loopback, sync):
            p = prog()
            p.set(PINS, 1)
            p.wrap_target()
            p.mov(X, PINS)
            p.wrap()
            block = PioBlock(sync_cycles=sync, loopback=loopback)
            block.sm[0].configure(program=block.load(p), set_base=0, set_count=1).enable()
            block.dirs = 1
            block.recording = True
            block.run(7)
            return [row["sms"][0]["x"] & 1 for row in block.trace]
        self.assertEqual(seen("direct", 0), [0, 1, 1, 1, 1, 1, 1])
        self.assertEqual(seen("datasheet", 0), [0, 0, 1, 1, 1, 1, 1])     # set at t=0, first seen by the instruction at t=2
        self.assertEqual(seen("datasheet", 2), [0, 0, 0, 0, 1, 1, 1])     # first seen at t=4

    # ----------------------------------------------------------------------------- the fractional clock divider
    def test_fractional_divider_extends_some_periods_by_one(self):
        # RP2040 3.5.5 p.339-340, Figures 46 and 47: an integer divisor n gives one enable per n cycles; a fraction f keeps a running total and every time it wraps through 1
        # the next division period is n + 1: for 2.5 the periods alternate 2 and 3 (2.0 first, as the total starts at 0)
        sm = PioBlock().sm[0]
        sm.cfg.clkdiv = 2.5
        self.assertEqual("".join("T" if sm._clock_enable() else "." for _ in range(12)), ".T.T..T.T..T")
        sm = PioBlock().sm[0]
        sm.cfg.clkdiv = 3.0
        self.assertEqual("".join("T" if sm._clock_enable() else "." for _ in range(9)), "..T..T..T")
        sm = PioBlock().sm[0]
        sm.cfg.clkdiv = 3.25
        self.assertLessEqual(abs(sum(sm._clock_enable() for _ in range(1300)) - 400), 1)        # average rate 1 / 3.25

    def test_divisor_range(self):
        # RP2040 Table 381 p.376: sysclk / (INT + FRAC / 256), 1 to 65536 (INT = 0 encodes 65536)
        sm = PioBlock().sm[0]
        sm.configure(clkdiv=65536)
        sm.configure(clkdiv=1)
        for bad in (0.5, 0, 65537):
            with self.assertRaises(ValueError):
                sm.configure(clkdiv=bad)

    # ----------------------------------------------------------------------------- FIFO joins
    def test_both_join_bits_make_both_fifos_unavailable(self):
        # RP2040 3.5.3 p.335: "Setting both FJOIN_RX and FJOIN_TX makes both FIFOs unavailable"
        p = prog()
        p.pull(block=False)
        p.push(block=False)
        block = PioBlock()
        sm = block.sm[0].configure(program=block.load(p), fifo_join="both")
        self.assertEqual((sm.tx.depth, sm.rx.depth), (0, 0))
        self.assertFalse(sm.put(1))
        self.assertEqual(sm.tx_dropped, 1)

    def test_pushes_stall_with_a_tx_join_and_pulls_with_an_rx_join(self):
        # RP2040 3.5.3 p.334-335: joining to TX makes the RX FIFO unavailable and "any PUSH instruction will stall"; joined to RX, the TX FIFO is unavailable
        p = prog()
        p.push()
        r = run_case(p, 5, fifo_join="tx")
        self.assertEqual(r.stall_cycles, 5)
        q = prog()
        q.pull()
        r = run_case(q, 5, fifo_join="rx", tx=[1])           # the word cannot even be written: the TX FIFO has no storage
        self.assertEqual((r.stall_cycles, r.tx_dropped), (5, 1))

    def test_changing_the_join_flushes_both_fifos(self):
        # RP2040 Table 383 p.377: "FIFOs are flushed when this bit is changed"
        block = PioBlock()
        sm = block.sm[0]
        sm.put(1)
        sm.rx.push(2)
        sm.configure(fifo_join="tx")
        self.assertEqual((len(sm.tx), len(sm.rx)), (0, 0))
        sm.put(3)
        sm.configure(fifo_join="tx")                          # unchanged: kept
        self.assertEqual(len(sm.tx), 1)

    def test_rx_get_and_put_registers_on_version_1(self):
        # RP2350 11.4.8 p.809 / 11.4.9 p.810: the RX FIFO is disabled and its four registers are written with MOV rxfifo[], ISR (PUT) and read with MOV OSR, rxfifo[] (GET), indexed
        # by Y or by an immediate; PUSH and PULL-style RX use is undefined; a non-zero Index with IdxI clear is a reserved encoding
        p = prog("rp2350")
        p.fifo("putget")
        p.set(X, 5)
        p.in_(X, 32)
        p.set(Y, 3)
        p.mov_put(Y)                                  # rxfifo[3] = isr
        p.mov_get(3)                                  # osr = rxfifo[3]
        r = run_case(p, 5, version=1, fifo_join="rx_put_get")
        self.assertEqual((r.osr, r.isr), (5, 5))
        block = PioBlock(version=1)
        block.load([0x8011])                          # mov rxfifo[...], isr with IdxI = 0 and Index = 1
        block.sm[0].configure(program=block.load([0x0000], offset=5), fifo_join="rx_put").enable().set_pc(0)
        with self.assertRaisesRegex(EmulationError, "reserved"):
            block.run(1)

    # ----------------------------------------------------------------------------- MOV STATUS and the IRQ flags
    def test_mov_status_compares_the_fifo_level_with_n(self):
        # RP2040 Table 382 p.376: TXLEVEL: all-ones if the TX FIFO level < N, otherwise all-zeroes; RXLEVEL likewise for the RX FIFO
        p = prog()
        p.mov(X, STATUS)
        for n, words, want in ((2, 1, 0xFFFFFFFF), (2, 2, 0), (1, 0, 0xFFFFFFFF), (0, 0, 0)):
            r = run_case(p, 1, status_sel="tx", status_n=n, tx=[7] * words)
            self.assertEqual(r.x, want, (n, words))
        r = run_case(p, 1, status_sel="rx", status_n=1)             # RX empty: level 0 < 1
        self.assertEqual(r.x, 0xFFFFFFFF)

    def test_mov_status_from_an_irq_flag_on_version_1(self):
        # RP2350 Table 1052 p.856: STATUS_SEL = IRQ: all-ones if the indexed flag is raised; STATUS_N 0 is this block, 8 the next lower-numbered block, 16 the next higher
        p = prog("rp2350")
        p.irq(3)
        p.mov(X, STATUS)
        r = run_case(p, 2, version=1, status_sel="irq", status_n=3)
        self.assertEqual(r.x, 0xFFFFFFFF)
        r = run_case(p, 2, version=1, status_sel="irq", status_n=2)
        self.assertEqual(r.x, 0)
        lower, upper = PioBlock(version=1), PioBlock(version=1)
        me = PioBlock(version=1)
        me.neighbours.update({-1: lower, 1: upper})
        q = prog("rp2350")
        q.mov(X, STATUS)
        q.mov(Y, STATUS)
        me.sm[0].configure(program=me.load(q), status_sel="irq", status_n=8 + 1).enable()
        lower.irq = 0b10
        me.run(1)
        self.assertEqual(me.sm[0].x, 0xFFFFFFFF)
        me.sm[0].configure(status_n=16 + 1)
        me.run(1)
        self.assertEqual(me.sm[0].y, 0)                         # the higher block has no flag 1 raised
        upper.irq = 0b10
        me.sm[0].set_pc(0)
        me.run(2)
        self.assertEqual(me.sm[0].y, 0xFFFFFFFF)
        with self.assertRaises(ValueError):
            PioBlock(version=0).sm[0].configure(status_sel="irq")

    def test_irq_set_wins_over_clear_in_the_same_cycle(self):
        # RP2350 11.2.8 p.798: "when a flag is both set and cleared on the same cycle, the set takes precedence"
        setter, clearer = prog(), prog()
        setter.irq(1)
        clearer.irq(1, mode=CLEAR)
        block = PioBlock()
        block.sm[0].configure(program=block.load(setter)).enable()
        block.sm[1].configure(program=block.load(clearer)).enable()
        block.irq = 0b10
        block.run(1)
        self.assertEqual(block.irq, 0b10)

    def test_irq_relative_index_adds_the_state_machine_number_modulo_four_and_keeps_bit_2(self):
        # RP2040 3.4.9.2 p.330: with the MSB set the state machine ID is added to the index by modulo-4 addition on the two LSBs; bit 2 is unaffected
        p = prog()
        p.irq(5, rel=True)
        block = PioBlock()
        for i in (1, 3):
            block.sm[i].configure(program=block.load(p, offset=0)).enable()
        block.run(1)
        self.assertEqual(block.irq, (1 << (4 | ((1 + 1) & 3))) | (1 << (4 | ((1 + 3) & 3))))      # flags 6 and 4

    def test_wait_irq_clears_the_flag_only_for_polarity_one(self):
        # RP2040 3.4.3.2 p.324-325: WAIT 1 IRQ clears the flag when the wait condition is met; WAIT 0 does not touch it
        p1, p0 = prog(), prog()
        p1.wait_irq(1, 2)
        p0.wait_irq(0, 3)
        for p, flags, want in ((p1, 0b0100, 0), (p0, 0b0000, 0)):
            block = PioBlock()
            block.sm[0].configure(program=block.load(p)).enable()
            block.irq = flags
            block.run(1)
            self.assertEqual(block.irq, want)
        block = PioBlock()
        block.sm[0].configure(program=block.load(p1)).enable()
        block.run(3)                                         # the flag is not raised: stalled
        self.assertEqual(block.sm[0].stall_cycles, 3)
        block.irq_force(0b0100)
        block.run(1)
        self.assertEqual((block.irq, block.sm[0].executed), (0, 1))

    def test_host_clears_and_forces_irq_flags(self):
        # RP2040 3.7 p.369: IRQ is write-1-to-clear; IRQ_FORCE raises flags in the state machines' view
        p = prog()
        p.irq(0, mode=WAIT)
        block = PioBlock()
        sm = block.sm[0].configure(program=block.load(p)).enable()
        block.run(3)
        self.assertEqual((block.irq, sm.executed), (1, 0))            # raised and waiting for the host
        block.irq_clear(1)
        block.run(1)
        self.assertEqual(sm.executed, 1)
        block.irq_force(0b110)
        self.assertEqual(block.irq, 0b110)                      # flag 0 was cleared and the next cycle has not run yet

    # ----------------------------------------------------------------------------- delay and side-set
    def test_side_set_bits_come_out_of_the_delay_field(self):
        # RP2040 3.4.1 p.322: the delay is the 5 - SIDESET_COUNT least significant bits, the side-set the SIDESET_COUNT most significant bits (an optional one includes the enable bit)
        p = prog(side_set=2)                          # 2 side-set bits, delay 0..7
        p.nop(side=3, delay=7)
        r = run_case(p, 9, sideset_base=0, dirs=3, record=True)
        self.assertEqual((r.delay_cycles, r.executed, r.wave(0)[0], r.wave(1)[0]), (7 * 1 + 0, 2, "1", "1"))
        q = prog(side_set=1, side_opt=True)           # enable + one value bit: delay 0..7
        q.nop(side=1, delay=5)
        q.nop(delay=7)
        r = run_case(q, 14, sideset_base=0, dirs=1)
        self.assertEqual(r.wave(0), "1" * 14)         # the second instruction has no side-set enable: the pin keeps its level

    def test_delay_cycles_start_after_a_stalled_instruction_completes(self):
        # RP2040 3.4.3.2 p.324: "delay cycles begin after the instruction completes ... they do not begin counting until after the wait condition is met"; 3.5.1 p.332:
        # "if an instruction stalls, the side-set still takes effect immediately"
        p = prog(side_set=1)
        p.wait_pin(1, 0, side=1, delay=2)
        p.set(X, 1, side=0)
        r = run_case(p, 8, stim={0: "00011111"}, sideset_base=1, dirs=2, record=True, sync_cycles=0)
        kinds = [row["sms"][0]["kind"] for row in r.trace]
        self.assertEqual("".join(kinds), "sssedd" + "ee")        # three stalled cycles, the WAIT completes in cycle 3, then its 2 delay cycles, then the next instruction
        self.assertEqual(r.wave(1)[:4], "1111")                    # side-set already in the first (stalled) cycle
        self.assertEqual(r.wave(1)[6], "0")                        # and the next instruction's side-set 0 in cycle 6

    # ----------------------------------------------------------------------------- EXEC and forced instructions
    def test_out_exec_ignores_its_own_delay_and_the_executee_keeps_its_delay(self):
        # RP2040 3.4.5.2 p.326: "Delay cycles on the initial OUT are ignored, but the executee may insert delay cycles as normal"; 3.5.7 p.344: the executee comes from a latch
        # and the PC does not advance on that second cycle
        setx = word(lambda p: p.set(X, 5, delay=1))
        p = prog()
        p.pull()
        p.out(EXEC, 16, delay=3)
        p.set(Y, 7)
        r = run_case(p, 6, tx=[setx], record=True)
        self.assertEqual("".join(row["sms"][0]["kind"] for row in r.trace), "eefdes")      # pull, out exec (no delay), set x (f) + its delay (d), set y, pull stalls (no more data)
        self.assertEqual((r.x, r.y), (5, 7))

    def test_mov_exec_ignores_its_delay_and_a_jump_executee_moves_the_pc(self):
        # RP2040 3.4.8.2 p.329: "Delay cycles on MOV EXEC are ignored, but the executee may insert delay cycles as normal"; an executed JMP uses an absolute address
        jmp5 = word(lambda p: p.jmp(5))
        q = prog()
        q.mov(EXEC, Y, delay=4)
        for _ in range(6):
            q.nop()
        r = run_case(q, 3, y=jmp5, record=True)
        self.assertEqual([row["sms"][0]["kind"] for row in r.trace], ["e", "f", "e"])      # no delay after the MOV; the jump executee has no delay
        self.assertEqual(r.pc, 6)                                                           # the jump went to 5, one more instruction ran (the nop at 5)

    def test_forced_instruction_ignores_delay_the_clock_divider_and_the_enable(self):
        # RP2040 3.5.7 p.343-344: a word written to SMx_INSTR is executed immediately, "delay cycles are ignored ... ignoring the state machine clock divider"; the PC does not
        # advance unless the instruction is a jump; the SDK uses this to set up a state machine before enabling it
        forced = word(lambda p: p.set(Y, 7, delay=9))
        p = prog()
        p.set(X, 1)
        p.set(X, 2)
        block = PioBlock()
        sm = block.sm[0].configure(program=block.load(p), clkdiv=1000)
        sm.force(forced)                                      # the state machine is not enabled
        block.run(1)
        self.assertEqual((sm.y, sm.pc, sm.executed, sm.delay_left), (7, 0, 1, 0))
        sm.enable()
        block.run(1)                                          # the divider has not produced an enable yet, so the program does not run
        self.assertEqual(sm.x, 0)
        sm.cfg.clkdiv = 1
        sm.force(word(lambda q: q.jmp(1)))
        block.run(1)
        self.assertEqual(sm.pc, 1)

    def test_forced_instruction_replaces_the_fetch_in_its_cycle_and_a_stalled_one_is_latched(self):
        # RP2040 3.5.7 p.343-344: the state machine executes the written instruction "rather than the instruction it would have fetched"; if it stalls it is latched (EXEC_STALLED)
        # and retried until it completes, after which the program continues where it left off
        p = prog()
        p.set(X, 1)
        p.set(X, 2)
        block = PioBlock(sync_cycles=0)
        sm = block.sm[0].configure(program=block.load(p)).enable()
        sm.force(word(lambda q: q.wait_gpio(1, 3)))
        block.ext = 0
        block.run(4)
        self.assertEqual((sm.x, sm.pc, sm.stall_cycles), (0, 0, 4))
        block.set_input(3, 1)
        block.run(1)
        self.assertEqual((sm.x, sm.pc), (0, 0))               # the forced WAIT completed in this cycle
        block.run(1)
        self.assertEqual((sm.x, sm.pc), (1, 1))

    # ----------------------------------------------------------------------------- OUT_STICKY and INLINE_OUT_EN (descriptions are short in the datasheets: details [verify])
    def test_out_sticky_asserts_the_latest_write_again_over_lower_state_machines(self):
        # RP2040 Table 382 p.376: OUT_STICKY "continuously assert the most recent OUT/SET to the pins", which "can create useful masking/override behaviour due to the priority
        # ordering of state machine pin writes (SM0 < SM1 < ...)"; the details are not given [verify]
        def level_after(sticky, cycles):
            a, b = prog(), prog()
            a.set(PINS, 0)                                  # SM0 writes 0 on every cycle
            b.set(PINS, 1)                                  # SM1 writes 1 once, then only SETs x
            b.wrap_target()
            b.set(X, 0)
            b.wrap()
            block = PioBlock()
            block.sm[0].configure(program=block.load(a), set_base=0, set_count=1).enable()
            block.sm[1].configure(program=block.load(b), set_base=0, set_count=1, out_sticky=sticky).enable()
            block.dirs = 1
            block.run(cycles)
            return block.out_level & 1
        self.assertEqual(level_after(False, 1), 1)              # in cycle 0 both write: the highest-numbered state machine, SM1, wins
        self.assertEqual(level_after(False, 2), 0)              # afterwards only SM0 writes
        self.assertEqual(level_after(True, 2), 1)               # the sticky write of SM1 is asserted again and keeps beating SM0

    def test_inline_out_enable_with_sticky_deasserts_the_latest_write(self):
        # RP2040 Table 382 p.376: INLINE_OUT_EN uses bit OUT_EN_SEL of the OUT data as an auxiliary write enable; with OUT_STICKY "writes with an enable of 0 will deassert the
        # latest pin write"
        p = prog()
        p.pull()
        p.out(PINS, 2)                                          # data 0b11: bit 1 = enable = 1: write pins = 0b11
        p.out(PINS, 2)                                          # the next two bits are 0b00: enable 0: no write, and the sticky write is dropped
        block = PioBlock()
        sm = block.sm[0].configure(program=block.load(p), out_base=0, out_count=1, out_sticky=True, inline_out_en=True, out_en_sel=1).enable()
        sm.put(0b0011)
        block.dirs = 1
        block.run(3)
        self.assertEqual(block.out_level & 1, 1)
        self.assertEqual(sm.sticky, {})


@support.cases
class DatasheetGapTests(support.TestCase):
    """Directed tests added for the mutation survivors of the audit (COR-43, COR-47). Expected values are worked out from the datasheet sentences quoted in each test."""

    def test_the_program_counter_wraps_only_when_it_equals_wrap_top(self):
        # RP2040 3.5.2 p.333, step 2: "if PC matches EXECCTRL_WRAP_TOP, set PC to EXECCTRL_WRAP_BOTTOM" (matches = equals): a program that jumps past wrap_top does not wrap.
        # 0: set x, 1   1: jmp 3 (wrap_top = 1)   2: set x, 9 (skipped)   3: set y, 5   4: set x, 7   5: jmp 5
        p = prog()
        p.wrap_target()
        p.set(X, 1)
        p.jmp("past")
        p.wrap()
        p.set(X, 9)
        p.label("past")
        p.set(Y, 5)
        p.set(X, 7)
        p.label("hang")
        p.jmp("hang")
        r = run_case(p, 12)
        self.assertEqual((r.x, r.y), (7, 5))                 # a wrap at pc 3 (pc >= wrap_top) would run `set x, 1` again and never reach `set x, 7`

    def test_jmp_x_decrement_also_decrements_when_it_falls_through(self):
        # RP2040 3.4.2.2: "X--: branch if scratch X non-zero, post-decrement"; the decrement happens whether or not the branch is taken, so X = 0 becomes 0xFFFFFFFF
        p = prog()
        p.set(X, 0)
        p.jmp("never", when=X_DEC)
        p.label("never")
        p.set(Y, 3)
        r = run_case(p, 3)
        self.assertEqual((r.x, r.y), (0xFFFFFFFF, 3))

    def test_wait_0_irq_does_not_clear_the_flag(self):
        # RP2040 3.4.9 / RP2350 11.4.9: only "wait 1 irq" clears the flag it waits for; "wait 0 irq" stalls while the flag is up and changes nothing
        p = prog()
        p.wait_irq(0, 3)
        r = run_case(p, 8, on_error="stop")
        self.assertEqual(r.irq, 0)                         # no flag was ever raised: the wait completes
        b = PioBlock()
        sm = b.sm[0]
        sm.configure(program=b.load(p))
        sm.enable()
        b.irq_force(1 << 3)
        for _ in range(6):
            b.step()
        self.assertEqual(b.irq, 1 << 3)                     # still up after six stalled cycles

    def test_autopull_refills_in_the_same_cycle_the_threshold_is_reached(self):
        # RP2040 3.5.4.2 (autopull): with PULL_THRESH = 8 the OSR is refilled from the TX FIFO as soon as 8 bits have been shifted out, if the FIFO has a word, so the next OUT does not
        # stall. Cycle 0: the OSR is exhausted (count 32 >= 8), the first OUT stalls while it is refilled (pins still 0). Cycle 1: out pins, 8 -> 0xAA; the count reaches 8 and the
        # OSR is refilled in the same cycle. Cycle 2: out pins, 8 -> 0x55, without a stall.
        p = prog()
        p.out(PINS, 8)
        r = run_case(p, 3, tx=[0xAA, 0x55], out_base=0, out_count=8, dirs=0xFF, autopull=True, pull_thresh=8)
        self.assertEqual([x & 0xFF for x in r.pads], [0x00, 0xAA, 0x55])

    def test_autopull_stalls_when_the_count_is_exactly_the_threshold_and_the_fifo_is_empty(self):
        # the same program with one word only: after the OUT of cycle 1 the OSR holds 0 bits (count 8 = threshold), the FIFO is empty, so the OUT of cycle 2 stalls and the pins keep 0xAA
        p = prog()
        p.out(PINS, 8)
        r = run_case(p, 4, tx=[0xAA], out_base=0, out_count=8, dirs=0xFF, autopull=True, pull_thresh=8)
        self.assertEqual([x & 0xFF for x in r.pads], [0x00, 0xAA, 0xAA, 0xAA])

    def test_mov_rxfifo_with_idxi_indexes_by_the_two_low_bits_of_index(self):
        # RP2350 11.4.8.2 p.809: "If IdxI (index by immediate) is set, the RX FIFO's registers are indexed by the two least-significant bits of the Index operand." Encoding
        # (Table 1027): 100 delay/side-set 0001 IdxI Index. Index 5 = 0b101: register 1.
        b = PioBlock(version=1)
        sm = b.sm[0]
        b.mem[0] = 0x8000 | 0x10 | 0x08 | 5                       # mov rxfifo[5 -> 1], isr
        sm.configure(fifo_join="rx_put")
        sm.isr = 0x1234
        sm.enable()
        b.step()
        self.assertEqual(sm.rx_regs, [0, 0x1234, 0, 0])

    def test_mov_rxfifo_index_without_idxi_must_be_zero(self):
        # the same page: "When IdxI is clear, all non-zero values of Index are reserved encodings, and their operation is undefined" (also Index 4..7)
        for index in (1, 4, 7):
            b = PioBlock(version=1)
            b.mem[0] = 0x8000 | 0x10 | index
            b.sm[0].configure(fifo_join="rx_put")
            b.sm[0].enable()
            with self.assertRaisesRegex(EmulationError, "reserved encoding"):
                b.step()

    def test_in_count_zero_means_32_pins(self):
        # RP2350 Table 1053 p.857-858 (SHIFTCTRL IN_COUNT): "A count of 32 is encoded with a field value of 0"; mov x, pins then reads all 32 pins
        p = prog("rp2350")
        p.mov(X, PINS)
        r = run_case(p, 2, version=1, stim={k: "1" for k in range(32)}, in_base=0, in_count=0)
        self.assertEqual(r.x, 0xFFFFFFFF)
        r = run_case(p, 2, version=1, stim={k: "1" for k in range(32)}, in_base=0, in_count=8)
        self.assertEqual(r.x, 0xFF)
