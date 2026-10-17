"""Directed tests of the emulator: every instruction and feature, with the expected values worked out from the RP2040 (chapter 3) and RP2350 (chapter 11) datasheets, not from
another implementation. Needs no other tool. The differential test against bitweave's simulator is in test_emulator_differential.py."""
import pytest

from pioweave.builder import (ALWAYS, CLEAR, EXEC, INVERT, ISR, NONE, NOT_OSRE, NOT_X, NOT_Y, NULL, OSR, PC, PIN, PINDIRS, PINS, REVERSE, SET, STATUS, WAIT, X, X_DEC,
                              X_NE_Y, Y, Y_DEC, Program)
from pioweave.emulator import EmulationError, PioBlock, run_case, to_vcd


def prog(chip="rp2040", **kw):
    return Program("t", chip=chip, **kw)


# ----------------------------------------------------------------------------- basics: set, delay, wrap
def test_set_pins_with_delay_and_wrap():
    p = prog()
    p.set(PINS, 1, delay=1)
    p.set(PINS, 0, delay=1)
    r = run_case(p, 12, set_base=0, set_count=1, dirs=1)
    assert r.wave(0) == "110011001100"
    assert r.executed == 6 and r.delay_cycles == 6 and r.stall_cycles == 0


def test_wrap_target_and_wrap():
    p = prog()
    p.set(X, 1)
    p.wrap_target()
    p.set(Y, 2)
    p.set(X, 3)
    p.wrap()
    r = run_case(p, 6, record=True)
    assert [row["sms"][0]["pc_at"] for row in r.trace] == [0, 1, 2, 1, 2, 1]


def test_jmp_conditions_x_y_and_decrement():
    p = prog()
    p.set(X, 2)
    p.label("a")
    p.jmp("a", when=X_DEC)                      # x = 2, 1, 0: taken twice, then falls through, x ends as 0xFFFFFFFF
    p.set(Y, 5)
    p.label("b")
    p.jmp("b", when=Y_DEC)
    p.set(X, 9)
    p.label("end")
    p.jmp("end")
    r = run_case(p, 40)
    assert r.x == 9 and r.y == 0xFFFFFFFF


def test_jmp_not_x_not_y_x_ne_y_pin_and_not_osre():
    p = prog()
    p.set(X, 3)
    p.set(Y, 3)
    p.jmp("hit1", when=X_NE_Y)                  # equal: not taken
    p.jmp("hit2", when=NOT_X)                   # x != 0: not taken
    p.set(X, 0)
    p.jmp("hit3", when=NOT_X)                   # taken
    p.set(Y, 31)                                # skipped
    p.label("hit1")
    p.label("hit2")
    p.label("hit3")
    p.jmp("pin", when=PIN)                      # pin 7 is high: taken
    p.set(Y, 30)                                # skipped
    p.label("pin")
    p.jmp("osre", when=NOT_OSRE)                # OSR empty (32 shifted): not taken
    p.set(Y, 7)
    p.label("osre")
    p.label("end")
    p.jmp("end")
    r = run_case(p, 14, jmp_pin=7, stim={7: "1"})
    assert (r.x, r.y) == (0, 7)


def test_jmp_not_osre_taken_while_the_osr_holds_bits():
    p = prog()
    p.pull(block=False)                         # OSR = X = 5, shift counter 0
    p.jmp("full", when=NOT_OSRE)                # fewer than pull_thresh bits shifted: taken
    p.set(Y, 1)
    p.label("full")
    p.set(Y, 2)
    r = run_case(p, 6, x=5)
    assert r.y == 2 and r.osr == 5


def test_jmp_target_is_relocated_by_the_load_offset():
    block = PioBlock()
    first = Program("a")
    for _ in range(4):
        first.nop()
    block.load(first)
    second = Program("b")
    second.label("top")
    second.set(X, 1)
    second.jmp("top")
    loaded = block.load(second)
    assert loaded.offset == 4 and block.mem[5] & 31 == 4          # the target word was 0, relocated to 4
    sm = block.sm[0].configure(program=loaded)
    sm.enable()
    block.run(6)
    assert sm.last[2] in (4, 5)


def test_origin_places_the_program():
    p = prog(origin=8)
    p.nop()
    block = PioBlock()
    assert block.load(p).offset == 8
    with pytest.raises(EmulationError):
        block.load(p, offset=40)
    with pytest.raises(EmulationError, match="no room"):
        b2 = PioBlock()
        for _ in range(4):
            q = prog()
            for _ in range(8):
                q.nop()
            b2.load(q)
        b2.load(q)


# ----------------------------------------------------------------------------- side-set and its priority
def test_mandatory_side_set_and_pindirs():
    p = prog(side_set=2)
    p.nop(side=0b10)
    p.nop(side=0b01)
    r = run_case(p, 4, sideset_base=3, dirs=0b11000)
    assert [(v >> 3) & 3 for v in r.pads] == [0b10, 0b01, 0b10, 0b01]
    q = prog(side_set=1, side_pindirs=True)
    q.nop(side=1)
    q.nop(side=0)
    r = run_case(q, 4, sideset_base=2)
    assert [(d >> 2) & 1 for d in r.dirs] == [1, 0, 1, 0]


def test_optional_side_set_leaves_the_pins_alone_when_absent():
    p = prog(side_set=1, side_opt=True)
    p.nop(side=1)
    p.nop()                                     # no side value: the pin keeps its level
    p.nop(side=0)
    r = run_case(p, 6, sideset_base=0, dirs=1)
    assert r.wave(0) == "110110"


def test_side_set_is_applied_after_the_pin_write_of_the_same_instruction():
    p = prog(side_set=1)
    p.set(PINS, 1, side=0)                      # both write pin 0: the side-set takes precedence
    r = run_case(p, 2, set_base=0, set_count=1, sideset_base=0, dirs=1)
    assert r.wave(0) == "00"


def test_side_set_happens_on_the_first_cycle_and_not_again_while_stalled():
    p = prog(side_set=1)
    p.wait_gpio(1, 4, side=1)                   # stalls until GPIO 4 is high; side-set is applied once, in the first cycle
    p.nop(side=0)
    r = run_case(p, 6, sideset_base=0, dirs=1, stim={4: "000011"})
    # cycles 0..3 the wait stalls (GPIO 4 low) and the side-set value 1 was applied in cycle 0; cycle 4 the wait ends; cycle 5 the nop applies side-set 0
    assert r.wave(0) == "111110"


# ----------------------------------------------------------------------------- stalls
def test_wait_gpio_pin_and_levels():
    p = prog()
    p.wait_gpio(1, 4)
    p.set(X, 1)
    p.wait_gpio(0, 4)
    p.set(Y, 1)
    r = run_case(p, 12, stim={4: "0001111000"}, record=True)
    kinds = "".join(row["sms"][0]["kind"] for row in r.trace)
    assert kinds[:3] == "sss" and kinds[3] == "e"           # stalls in cycles 0..2, passes in cycle 3
    assert r.x == 1 and r.y == 1


def test_wait_pin_is_relative_to_in_base():
    p = prog()
    p.wait_pin(1, 2)                            # in_base 5: GPIO 7
    p.set(X, 4)
    r = run_case(p, 4, in_base=5, stim={7: "0011"}, record=True)
    kinds = "".join(row["sms"][0]["kind"] for row in r.trace)
    assert kinds == "ssee" and r.x == 4


# ----------------------------------------------------------------------------- OUT and IN
def test_out_shifts_right_and_left():
    p = prog()
    p.pull()
    p.out(X, 4)
    p.out(Y, 8)
    r = run_case(p, 8, tx=[0x12345678], out_shift="right")
    assert (r.x, r.y, r.osr) == (0x8, 0x67, 0x12345)
    r = run_case(p, 8, tx=[0x12345678], out_shift="left")
    assert (r.x, r.y) == (0x1, 0x23) and r.osr == (0x12345678 << 12) & 0xFFFFFFFF


def test_out_destinations_pins_pindirs_isr_null():
    p = prog()
    p.pull()
    p.out(PINS, 3)
    p.out(PINDIRS, 3)
    p.out(NULL, 2)
    p.out(ISR, 4)
    r = run_case(p, 8, tx=[0b1111_00_110_101], out_base=2, out_count=3, dirs=0b11100)
    assert (r.pads[1] >> 2) & 7 == 0b101 and (r.dirs[2] >> 2) & 7 == 0b110
    assert r.isr == 0b1111


def test_out_pc_jumps_and_out_exec_runs_the_data_as_an_instruction():
    p = prog()
    p.pull()
    p.out(PC, 5)
    p.set(X, 31)
    p.set(Y, 30)
    p.set(X, 7)
    r = run_case(p, 8, tx=[4])                  # jumps to address 4: x = 7, the sets in between are skipped
    assert r.x == 7 and r.y == 0
    q = prog()
    q.pull()
    q.out(EXEC, 16)
    set_x_5 = prog()
    set_x_5.set(X, 5)
    r = run_case(q, 5, tx=[set_x_5.words()[0]])
    assert r.x == 5


def test_osr_counter_saturates_and_in_counter_too():
    p = prog()
    p.pull()
    p.out(NULL, 32)
    p.out(NULL, 32)
    p.in_(NULL, 32)
    r = run_case(p, 8, tx=[1], record=True)
    last = r.trace[-1]["sms"][0]
    assert last["osr_count"] == 32 and last["isr_count"] == 32


def test_in_sources_and_directions():
    p = prog()
    p.in_(PINS, 4)
    p.in_(X, 4)
    p.in_(NULL, 8)
    r = run_case(p, 3, in_base=1, x=0xA, stim={1: "1", 2: "0", 3: "1", 4: "0"}, in_shift="left")
    assert r.isr == (0b0101 << 12) | (0xA << 8)
    r = run_case(p, 3, in_base=1, x=0xA, stim={1: "1", 2: "0", 3: "1", 4: "0"}, in_shift="right")
    # pins (0101) enter at the top, then x (0xA) pushes them down by 4, then eight zeros push everything down by 8
    assert r.isr == 0x00A50000


def test_autopush_at_the_threshold_and_stall_when_full():
    p = prog()
    p.in_(X, 8)
    r = run_case(p, 6, x=0x5A, autopush=True, push_thresh=16, in_shift="left", drain=False)
    assert [w for _, w in [(0, w) for w in r.rx_left]] == [0x5A5A, 0x5A5A, 0x5A5A]
    q = prog()
    q.in_(X, 32)
    r = run_case(q, 12, x=1, autopush=True, push_thresh=32, drain=False)
    assert len(r.rx_left) == 4 and r.stall_cycles >= 1                     # the fifth push stalls on the full RX FIFO


def test_autopull_refills_the_osr():
    p = prog()
    p.out(X, 16)
    r = run_case(p, 4, tx=[0x00020001, 0x00040003], autopull=True, pull_thresh=32, out_shift="right", record=True)
    xs = [row["sms"][0]["x"] for row in r.trace]
    # the OSR starts empty (32 shifted), so the first OUT stalls while it refills; then 1, 2, then the next word 3, 4
    assert 1 in xs and 2 in xs


# ----------------------------------------------------------------------------- PUSH / PULL
def test_push_pull_block_noblock_iffull_ifempty():
    p = prog()
    p.pull(block=False)                         # empty FIFO: X goes to the OSR
    p.out(Y, 32)
    p.push(block=False)                         # pushes the ISR (0), clears it
    r = run_case(p, 3, x=77, drain=False)
    assert r.y == 77 and r.rx_left == [0]
    q = prog()
    q.set(X, 1)
    q.in_(X, 1)
    q.push(iffull=True, block=False)            # fewer than push_thresh bits in the ISR: does nothing
    r = run_case(q, 3, push_thresh=8, drain=False)
    assert r.rx_left == [] and r.isr != 0
    r = run_case(q, 3, push_thresh=1, drain=False)
    assert len(r.rx_left) == 1


def test_pull_ifempty_only_when_the_osr_is_empty_enough():
    p = prog()
    p.pull()
    p.pull(ifempty=True)                        # the OSR has 0 bits shifted: not below the threshold, no pull
    p.out(X, 8)
    r = run_case(p, 3, tx=[0xAB, 0xCD], pull_thresh=16)
    assert r.x == 0xAB and r.tx_left == 1


def test_noblock_push_to_a_full_fifo_loses_the_word_and_clears_the_isr():
    p = prog()
    p.set(X, 3)
    p.in_(X, 2)
    p.push(block=False)
    r = run_case(p, 30, drain=False)
    assert len(r.rx_left) == 4 and r.rx_lost > 0


# ----------------------------------------------------------------------------- MOV
def test_mov_operations_and_destinations():
    p = prog()
    p.mov(X, NULL, op=INVERT)                   # x = 0xFFFFFFFF
    p.mov(Y, X, op=REVERSE)                     # bit reverse of all ones
    p.set(X, 6)
    p.mov(ISR, X)
    p.mov(OSR, ISR, op=INVERT)
    r = run_case(p, 6)
    assert r.y == 0xFFFFFFFF and r.isr == 6 and r.osr == 0xFFFFFFF9
    q = prog()
    q.set(X, 1)
    q.mov(Y, X, op=REVERSE)
    r = run_case(q, 3)
    assert r.y == 0x80000000


def test_mov_to_pc_and_exec_and_pins():
    p = prog()
    p.set(X, 3)
    p.mov(PC, X)                                # jump to address 3
    p.set(Y, 1)
    p.set(Y, 2)
    p.set(Y, 4)
    r = run_case(p, 5)
    assert r.y == 4
    q = prog()
    q.set(X, 5)
    q.mov(PINS, X)
    r = run_case(q, 3, out_base=0, out_count=4, dirs=0xF)
    assert r.pads[1] & 0xF == 5


def test_mov_status_tx_and_rx_levels():
    p = prog()
    p.mov(X, STATUS)
    r = run_case(p, 2, status_sel="tx", status_n=2, tx=[1])                 # one word in the TX FIFO: level 1 < 2: all ones
    assert r.x == 0xFFFFFFFF
    r = run_case(p, 2, status_sel="tx", status_n=2, tx=[1, 2])
    assert r.x == 0


# ----------------------------------------------------------------------------- IRQ
def test_irq_set_clear_and_wait_between_state_machines():
    block = PioBlock()
    a = Program("a")
    a.irq(0, mode=WAIT)                         # set flag 0 and wait until it is cleared
    a.set(X, 9)
    a.label("end")
    a.jmp("end")
    b = Program("b")
    b.set(Y, 1)
    b.wait_irq(1, 0)                            # waits for flag 0 and clears it
    b.set(Y, 2)
    b.label("end")
    b.jmp("end")
    la, lb = block.load(a), block.load(b)
    sa = block.sm[0].configure(program=la).enable()
    sb = block.sm[1].configure(program=lb).enable()
    block.run(8)
    assert sa.x == 9 and sb.y == 2 and block.irq == 0


def test_irq_rel_adds_the_state_machine_number():
    block = PioBlock()
    p = Program("p")
    p.irq(0, rel=True)
    p.nop()
    loaded = block.load(p)
    for i in (0, 2):
        block.sm[i].configure(program=loaded).enable()
    block.run(3)
    assert block.irq == (1 << 0) | (1 << 2)


def test_set_and_clear_in_the_same_cycle_ends_set():
    block = PioBlock()
    a, b = Program("a"), Program("b")
    a.irq(3, mode=SET)
    b.irq(3, mode=CLEAR)
    block.sm[0].configure(program=block.load(a)).enable()
    block.sm[1].configure(program=block.load(b)).enable()
    block.run(1)
    assert block.irq == 0b1000


# ----------------------------------------------------------------------------- pins of several state machines, the synchroniser, the divider
def test_the_highest_numbered_state_machine_wins_a_pin():
    block = PioBlock()
    a, b = Program("a"), Program("b")
    a.set(PINS, 1)
    b.set(PINS, 0)
    for i, p in enumerate((a, b)):
        block.sm[i].configure(program=block.load(p), set_base=0, set_count=1).enable()
    block.dirs = 1
    block.run(1)
    assert block.out_level & 1 == 0


def test_input_synchroniser_delays_inputs_by_two_cycles():
    p = prog()
    p.in_(PINS, 1)
    r = run_case(p, 4, stim={0: "1"}, sync_cycles=0, in_shift="left")
    assert r.isr == 0b1111
    r = run_case(p, 4, stim={0: "1"}, sync_cycles=2, in_shift="left")
    assert r.isr == 0b0011                      # the first two cycles still see the old level 0


def test_clock_divider_runs_the_state_machine_at_a_fraction_of_the_system_clock():
    p = prog()
    p.nop()
    block = PioBlock()
    sm = block.sm[0].configure(program=block.load(p), clkdiv=2).enable()
    block.run(10)
    assert sm.executed == 5
    block = PioBlock()
    sm = block.sm[0].configure(program=block.load(p), clkdiv=1.5).enable()
    block.run(30)
    assert sm.executed == 20


# ----------------------------------------------------------------------------- FIFOs, joins, errors
def test_fifo_depths_and_joins():
    block = PioBlock()
    sm = block.sm[0]
    assert [sm.put(i) for i in range(5)] == [True] * 4 + [False] and sm.tx_dropped == 1
    sm.configure(fifo_join="tx")
    assert sm.tx.depth == 8 and sm.rx.depth == 0
    sm.configure(fifo_join="rx")
    assert sm.tx.depth == 0 and sm.rx.depth == 8
    assert sm.get() is None


def test_rx_fifo_registers_on_version_1():
    p = prog("rp2350")
    p.fifo("putget")
    p.set(X, 7)
    p.in_(X, 32)
    p.mov_put(2)                                # mov rxfifo[2], isr
    p.mov_get(2)                                # mov osr, rxfifo[2]
    r = run_case(p, 4, version=1, fifo_join="rx_put_get")
    assert r.osr == 7 << 0 and r.isr == 7
    with pytest.raises(ValueError, match="version 1"):
        PioBlock(version=0).sm[0].configure(fifo_join="rx_put")


def test_version_1_instructions_wait_jmppin_mov_pindirs_and_in_count():
    p = prog("rp2350")
    p.wait_jmppin(1, 1)                         # waits on pin jmp_pin + 1
    p.mov(PINDIRS, NULL, op=INVERT)
    r = run_case(p, 4, version=1, jmp_pin=3, out_base=0, out_count=2, stim={4: "0011"})
    assert r.dirs[-1] & 3 == 3
    q = prog("rp2350")
    q.in_(PINS, 8)
    r = run_case(q, 1, version=1, in_base=0, in_count=4, stim={pin: "1" for pin in range(8)})
    assert r.isr == 0x0F000000                  # only in_count pins are visible: the upper four read as 0


def test_irq_next_and_prev_reach_the_neighbouring_blocks():
    a, b = PioBlock(version=1), PioBlock(version=1)
    a.neighbours[1], b.neighbours[-1] = b, a
    p = prog("rp2350")
    p.irq(2, mode=SET, other="next")
    a.sm[0].configure(program=a.load(p)).enable()
    a.run(2)
    assert b.irq == 0b100 and a.irq == 0


def test_empty_instruction_memory_and_reserved_encodings_raise():
    block = PioBlock()
    block.sm[0].enable()
    with pytest.raises(EmulationError, match="empty instruction memory"):
        block.run(1)
    for word, match in ((0x4080, "IN source"), (0xE0C0, "SET destination"), (0xA0C0 | 0x18, "MOV operation"), (0xA004 | 0x0, None)):
        b = PioBlock()
        b.mem[0] = word
        b.sm[0].enable()
        if match:
            with pytest.raises(EmulationError, match=match):
                b.run(1)


def test_version_0_refuses_wait_jmppin_and_mov_pindirs():
    for word, match in ((0x20E0, "WAIT source 11"), (0xA060, "MOV PINDIRS")):
        b = PioBlock(version=0)
        b.mem[0] = word
        b.sm[0].enable()
        with pytest.raises(EmulationError, match=match):
            b.run(1)


def test_configuration_errors():
    sm = PioBlock().sm[0]
    for bad in ({"push_thresh": 0}, {"pull_thresh": 33}, {"fifo_join": "all"}, {"in_shift": "up"}, {"nonsense": 1}):
        with pytest.raises((ValueError, TypeError)):
            sm.configure(**bad)
    with pytest.raises(ValueError):
        PioBlock(version=2)
    with pytest.raises(ValueError):
        PioBlock(version=0, gpio_base=16)
    with pytest.raises(TypeError):
        sm.configure(program=object())


def test_forced_instruction_runs_before_the_program():
    p = prog()
    p.set(X, 1)
    block = PioBlock()
    sm = block.sm[0].configure(program=block.load(p)).enable()
    forced = prog()
    forced.set(Y, 7)
    sm.force(forced.words()[0])
    block.run(1)
    assert sm.y == 7 and sm.pc == 0


def test_vcd_of_a_recorded_run():
    p = prog()
    p.set(PINS, 1, delay=1)
    p.set(PINS, 0, delay=1)
    r = run_case(p, 6, set_base=0, set_count=1, dirs=1, record=True)
    v = to_vcd(r.trace, [0])
    assert "$var wire 1 ! pin0 $end" in v and "#0" in v and "1!" in v and "0!" in v
