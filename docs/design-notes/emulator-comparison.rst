The emulator against rp2040-pio-emulator: design note
=====================================================

Dated 2026-10-17. The other project is rp2040-pio-emulator (GitHub NathanY3G/rp2040-pio-emulator, Apache-2.0, package ``pioemu``, version 0.89.0, changelog up to 0.88.0 of 2026-08-28). It was read for its public behaviour only: the README ("Supported Instructions", "Known Limitations"), the Quick Start Guide, the Tour of pioemu, the FAQ, the Troubleshooting guide, the changelog, the signature, docstring and the first lines of the main loop of ``emulate()`` (to see how stop conditions and undecodable words behave), the fields of ``State`` and the names of its tests. Its code and tests were not copied or translated; ``pioweave.emulator`` was written from the datasheets (see :doc:`../emulator`), and the tests added for this note (``tests/test_emulator_parity.py``, ``tests/test_emulator_trace.py``) were written afresh with expected values worked out from the datasheets. Everything stated about the other project below is what those documents and that part of ``emulate()`` say; the project was not installed or run.

1. Purpose and scope
--------------------

.. list-table::
   :header-rows: 1

   * - Point
     - rp2040-pio-emulator (as documented)
     - pioweave.emulator
   * - purpose
     - unit tests, counting clock cycles, visualising GPIO over time, an alternative to debugging on hardware
     - the same, plus checking an Amaranth design against a second implementation; the reference of the tests of the compiler
   * - unit of emulation
     - one state machine ("a single State Machine is emulated and not an entire PIO block")
     - a whole block: four state machines, shared instruction memory, eight IRQ flags, pins, FIFOs
   * - chips
     - RP2040 and RP2350 (README); no mention of the PIO version differences
     - PIO version 0 (RP2040) and 1 (RP2350, RP2354), including the 48-GPIO window (``gpio_base``)
   * - interface
     - one generator ``emulate()`` yielding ``(before, after)`` immutable states, stop condition required
     - ``emulate()`` with the same shape (``per="instruction"`` or ``"cycle"``), plus ``run_case``, ``run_compiled``, ``PioBlock``, VCD output

2. Checklist: instructions
--------------------------

.. list-table::
   :header-rows: 1

   * - Item
     - rp2040-pio-emulator
     - pioweave.emulator
     - Test here
   * - JMP, all eight conditions
     - supported
     - supported; ``X--``/``Y--`` always decrement and wrap to 0xFFFFFFFF
     - ``test_quick_start_counts_x_down_through_zero`` and ``test_emulator.py``
   * - WAIT on GPIO and PIN
     - supported
     - supported
     - ``test_emulator.py``
   * - WAIT on IRQ
     - not supported
     - supported, with the clear on polarity 1 and the relative index
     - ``test_wait_irq_clears_the_flag_only_for_polarity_one``
   * - WAIT JMPPIN
     - not mentioned
     - supported on PIO version 1
     - ``test_emulator.py``
   * - IN
     - supported, "no support for pin-sets associated with IN; all pin numbers are with respect to GPIO 0"
     - supported with ``in_base`` rotation and wrap after GPIO 31
     - ``test_in_pins_are_rotated_by_in_base_and_wrap``
   * - OUT, destinations PINS, X, Y, NULL, PINDIRS, PC, ISR
     - supported
     - supported
     - ``test_emulator.py``, ``test_out_and_mov_to_isr_set_the_counters_and_null_is_zero``
   * - OUT EXEC
     - not implemented
     - supported: executee in the next cycle, the OUT's delay ignored
     - ``test_out_exec_ignores_its_own_delay_and_the_executee_keeps_its_delay``
   * - PUSH and PULL, block and noblock
     - supported
     - supported (noblock PULL copies X; noblock PUSH on a full FIFO clears the ISR and counts a lost word)
     - ``test_emulator.py``
   * - PULL IFEMPTY and PUSH IFFULL
     - supported, but "do not respect the pull and push thresholds"
     - respect the thresholds
     - ``test_pull_ifempty_and_push_iffull_use_the_thresholds``
   * - MOV
     - "some variants and operations not implemented"
     - all sources, destinations (PINS, X, Y, PINDIRS on v1, EXEC, PC, ISR, OSR) and operations (invert, bit-reverse), STATUS
     - ``test_emulator.py``, ``test_mov_status_compares_the_fifo_level_with_n``
   * - IRQ
     - not supported
     - set, clear, wait, relative index; PREV / NEXT on v1
     - ``test_irq_relative_index_adds_the_state_machine_number_modulo_four_and_keeps_bit_2``
   * - SET
     - supported, ``set_base`` / ``set_count`` mapping since 0.88.0
     - supported; the range wraps after GPIO 31
     - ``test_set_out_and_side_set_ranges_wrap_after_gpio_31``

3. Checklist: features
----------------------

.. list-table::
   :header-rows: 1

   * - Item
     - rp2040-pio-emulator
     - pioweave.emulator
     - Test here
   * - delay cycles, stalls (a stalled instruction retries without moving the PC)
     - supported
     - supported; delay starts after a stall completes
     - ``test_a_stalled_instruction_keeps_the_pc_and_costs_one_cycle_per_retry``, ``test_instruction_cost_is_one_plus_delay_and_a_taken_jmp_delay_applies``, ``test_delay_cycles_start_after_a_stalled_instruction_completes``
   * - side-set (count in the delay field; "side-set is not mistaken for delay")
     - supported; the pins from GPIO 16 to 31 were fixed in 0.85.0; pin sets "do not wrap after GPIO 31"
     - supported: mandatory, optional and pindirs side-set; wraps
     - ``test_side_set_bits_come_out_of_the_delay_field``, ``test_side_set_on_high_gpios``
   * - program wrapping (``wrap_target``, ``wrap_top``)
     - supported (0.82.0), default wrap at the last instruction
     - supported; ``Program`` carries its range, ``emulate`` accepts the two names for a word list
     - ``test_programs_wrap_to_the_start_by_default``, ``test_a_word_list_with_side_set_and_a_wrap_range``
   * - autopush and autopull with thresholds, stall when the RX FIFO is full or the TX FIFO empty
     - supported (0.84.0, "initial support")
     - supported
     - ``test_autopull_and_autopush_show_in_the_fifo_tuples_and_stalls``
   * - shift direction of ISR and OSR
     - supported
     - supported
     - ``test_shift_directions_of_in_and_out``
   * - input signals (``input_source``)
     - one callable returning all GPIO levels, called before each instruction; outputs are not overwritten
     - ``input_source(state)`` before every system clock cycle, ``stim`` waveforms, an optional two-flop synchroniser; outputs are not overwritten
     - ``test_input_source_drives_pins_the_block_does_not_drive``, ``test_stim_waveforms_as_in_run_case``
   * - initial state
     - ``initial_state``
     - ``initial_state``
     - ``test_initial_state_sets_registers_fifos_and_counters``
   * - stop conditions
     - ``stop_when(opcode, state)`` required
     - ``stop_when(state)`` (the opcode is ``state.next_opcode``), ``cycles``, ``max_cycles``
     - ``test_stop_when_sees_the_state_before_the_step_and_a_missing_stop_is_refused``, ``test_max_cycles_stops_a_program_stuck_on_a_wait``, ``test_stop_when_can_look_at_the_next_opcode``
   * - several state machines, shared memory, IRQ synchronisation
     - not supported
     - supported
     - ``test_two_state_machines_synchronise_through_an_irq_flag``
   * - FIFO joins, RX FIFO registers
     - not mentioned
     - supported, with the flush on a change of join
     - ``tests/test_emulator_datasheet.py``
   * - clock divider
     - not mentioned
     - fractional divider (first-order delta-sigma, RP2040 3.5.5)
     - ``test_fractional_divider_extends_some_periods_by_one``
   * - forced instructions (SMx_INSTR)
     - not mentioned
     - ``sm.force()``
     - ``test_forced_instruction_ignores_delay_the_clock_divider_and_the_enable``
   * - undefined programs
     - the run ends (``emulate`` returns when a word is not decoded)
     - ``EmulationError`` (or a stop with ``on_error="stop"``)
     - ``test_undefined_programs_raise``
   * - immutable states with equality and a printable form
     - ``State``
     - ``State`` (frozen dataclass)
     - ``test_states_are_frozen_comparable_and_printable``
   * - waveforms and viewers
     - an examples repository with a GTKWave (FST) tool and a Jupyter notebook
     - ``pin_history``, ``waveform_text``, ``to_vcd`` (GTKWave, Surfer), optional matplotlib ``plot_waveform``
     - ``test_pin_history_and_waveform_text``
   * - differential checks
     - (none described)
     - against bitweave's PIO simulator and the Amaranth simulation
     - ``tests/test_emulator_differential.py``, ``tests/test_emulator_amaranth.py``

4. Known limitations of the other project, and ours
---------------------------------------------------

The README lists five limitations. Against them:

1. "Not all of the available instructions are supported": ours supports all nine instructions with every source and destination.
2. "No support for pin-sets associated with IN": supported (``in_base``).
3. "Pin-sets do not wrap after GPIO 31": ours wrap (RP2040 3.5.6 p.341).
4. "PULL IFEMPTY and PUSH IFFULL do not respect the thresholds": ours do.
5. "No direct support for the concurrent running of multiple PIO programs": supported.

Limitations of ours that the other project does not have, or that are open:

- ``emulate`` and ``run_case`` run one state machine; a multi-state-machine program needs ``PioBlock`` directly (no generator for it yet).
- The cycle model is the system clock cycle with a clock divider; there is no pad or timing model (no hysteresis, slew, propagation, metastability).
- The behaviour on the chip is not verified: nothing has run on a board (:doc:`../hardware-testing` is a plan).
- Facts the datasheets leave open are listed at the end of :doc:`../emulator` ([verify]).

5. Gaps found and fixed
-----------------------

Reading the datasheets for this comparison found real gaps in the emulator, fixed in the same change and each covered by a test in ``tests/test_emulator_datasheet.py``:

- ``SET_COUNT`` resets to 5 (RP2040 Table 386 p.378), the emulator started with 0.
- A word written to SMx_INSTR ignores its delay cycles, the clock divider and whether the state machine is enabled (RP2040 3.5.7 p.344-345); the emulator applied the delay and required the state machine to run. A stalled forced instruction is now latched and the interrupted instruction keeps its progress.
- The clock divider followed an accumulator with a different phase from the description (periods of n and n + 1 by a running total, Figures 46 and 47); the average rate was already right.
- Changing the FIFO join flushes both FIFOs; setting both join bits makes both unavailable (RP2040 3.5.3 p.335, Table 383 p.377).
- ``MOV STATUS`` from an IRQ flag handles the flags of the next lower and higher PIO block (RP2350 Table 1052 p.857) and is refused on PIO version 0.
- INPUT_SYNC_BYPASS per GPIO (``PioBlock.sync_bypass``), the reserved ``MOV rxfifo[]`` encoding with a non-zero index and IdxI clear, host access to the IRQ flags, OUT_STICKY and INLINE_OUT_EN (short descriptions in RP2040 Table 382 p.376; details [verify]).
- The output-to-input delay of a state machine reading its own outputs (RP2040 3.5.6.1 p.342: 2 and 4 cycles) is available as ``PioBlock(loopback="datasheet")``; the default keeps the convention shared with bitweave's simulator and the Amaranth simulation (one cycle).

6. Licensing note
-----------------

rp2040-pio-emulator is Apache-2.0 and pioweave is proprietary. No code, test or text of the other project is in this repository; the comparison states its documented behaviour in the words of this note. If a future change wants to import its tests, the Apache-2.0 notice and attribution requirements would apply first.
