The PIO emulator (``pioweave.emulator``)
========================================

A software model of the PIO blocks of the RP2040, RP2350 and RP2354, written in Python and needing no Amaranth. It runs a program cycle by cycle the way the datasheets describe it, so you can see the pins, the registers and the FIFOs without a chip, and so that pioweave can check a design against a second, independent implementation.

The API reference of this part, generated from the docstrings, is in :doc:`reference/emulator`.

.. testcode::

   from pioweave.builder import Program, PINS, X, X_DEC
   from pioweave.emulator import run_case

   p = Program("uart_tx", chip="rp2040", side_set=1, side_opt=True)
   p.pull(side=1, delay=7)
   p.set(X, 7, side=0, delay=7)
   p.label("bitloop")
   p.out(PINS, 1)
   p.jmp("bitloop", when=X_DEC, delay=6)

   r = run_case(p, 120, tx=[0x55], side_base=0, out_base=0, out_count=1, dirs=1)
   print(r.wave(0))            # the level of pin 0 after every cycle

.. testoutput::

   111111110000000011111111000000001111111100000000111111110000000011111111000000001111111111111111111111111111111111111111

What is modelled
----------------

One ``PioBlock`` has a shared 32-word instruction memory, four state machines, the eight IRQ flags and the pins of the block's 32-pin window.

.. list-table::
   :header-rows: 1

   * - Part
     - Behaviour
   * - instructions
     - all nine, with every source and destination: ``JMP`` (all eight conditions), ``WAIT`` (GPIO, pin, IRQ, and on version 1 JMPPIN), ``IN``, ``OUT`` (including ``PC``, ``ISR``, ``EXEC``, ``PINDIRS``), ``PUSH`` / ``PULL`` (``iffull``, ``ifempty``, block and noblock), ``MOV`` (inversion, bit reversal, all sources and destinations, ``STATUS``), ``IRQ`` (set, clear, wait, relative), ``SET``
   * - timing
     - one instruction per state machine clock cycle; delays; stalls (a stalled instruction retries every cycle); side-set applied in the first cycle of the instruction, after the instruction's own pin write (so side-set wins); ``OUT EXEC`` / ``MOV EXEC`` run the executee in the next cycle and the executee's own delay applies; a forced instruction runs at once and its delay is ignored
   * - shifters
     - OSR and ISR with the shift directions, the shift counters (OSR starts empty), autopush and autopull with thresholds, the counters saturating at 32; reset: ISR counter 0, OSR counter 32, both shifts to the right, thresholds 32 (RP2040 Table 383 p.377), ``set_count`` 5 (Table 386 p.378)
   * - FIFOs
     - 4 words each way; joined (8 words one way) with ``fifo_join="tx"`` or ``"rx"``, both unavailable with ``"both"``; changing the join flushes both FIFOs (RP2040 3.5.3 p.334-335, Table 383 p.377); on version 1 the RX FIFO as four registers with ``MOV rxfifo[]`` (``rx_put``, ``rx_get``, ``rx_put_get``; RP2350 11.4.8 p.810, 11.4.9 p.811)
   * - IRQ flags
     - eight flags per block, set and clear from the instructions and from the host (``block.irq_clear(mask)``, ``block.irq_force(mask)``); a flag that is set and cleared in the same cycle ends up set (RP2350 11.2.8 p.799); ``IRQ PREV`` / ``NEXT`` between linked blocks (``PioBlock.neighbours``) on version 1; ``MOV STATUS`` from a TX or RX level, and on version 1 from a flag of this or a neighbouring block (RP2350 Table 1052 p.857)
   * - pins
     - output levels and directions per pin; several state machines writing the same pin: the highest-numbered one wins, separately for levels and directions (RP2040 datasheet 3.5.6.1 p.341-342), and within one state machine the side-set wins over OUT / SET (3.5.1 p.332); inputs from ``set_input()`` / waveforms, through the two-flop input synchroniser (``sync_cycles``, 2 by default, engaged unless the pin's bit in ``block.sync_bypass`` (INPUT_SYNC_BYPASS) is set: 3.5.6.3 p.342, Table 376 p.375; 0 bypasses it for all pins); optional ``loopback="datasheet"`` for the 2 / 4 cycle delay of a state machine reading its own outputs (3.5.6.1 p.342)
   * - clock divider
     - ``clkdiv`` from 1 to 65536 in steps of 1/256 as a first-order delta-sigma divider: periods of n cycles, one of them n + 1 every time the running total of the fraction wraps (RP2040 3.5.5 p.339-340, Figures 46 and 47). Where the first period starts after enabling is not stated [verify]; a forced instruction ignores the divider
   * - forced and EXEC instructions
     - ``OUT EXEC`` / ``MOV EXEC``: the executee runs in the next cycle with its own delay, the delay of the OUT / MOV is ignored, the PC does not advance for the executee (3.4.5.2 p.327, 3.4.8.2 p.329, 3.5.7 p.344). ``sm.force(word)`` (a write to SMx_INSTR): executes at once, whether or not the state machine is enabled and whatever its divider, delay cycles ignored, the PC does not advance unless it is a jump; a stalled one is latched and retried (3.5.7 p.344-345)
   * - EXECCTRL extras
     - ``out_sticky``, ``inline_out_en``, ``out_en_sel`` (RP2040 Table 382 p.376); the datasheets give two sentences, so the details are [verify]
   * - PIO version 1
     - ``WAIT JMPPIN``, ``MOV PINDIRS``, ``MOV`` to and from the RX FIFO registers, ``IN`` with a count (``in_count``), ``STATUS`` from an IRQ flag, the GPIO base (0 or 16) for the 48-GPIO parts
   * - errors
     - reserved encodings, version-1 instructions on version 0, undefined combinations (for example ``PUSH`` with an RX FIFO register join) and executing empty instruction memory raise ``EmulationError``

Not modelled: DMA and its request signals, the CPU-side interrupt lines (the IRQ flags are visible, the NVIC is not), GPIO function selection and pad electrical behaviour (input hysteresis, slew, pulls), the system clock itself (a cycle is a system clock cycle), and more than one clock domain.

Using a block directly
----------------------

.. testcode::

   from pioweave.builder import Program, PINS
   from pioweave.emulator import PioBlock

   block = PioBlock(version=0)                     # 0: RP2040, 1: RP2350 and RP2354; gpio_base=16 for the 48-GPIO parts
   prog = Program("blink")
   prog.set(PINS, 1, delay=1)
   prog.set(PINS, 0, delay=1)
   loaded = block.load(prog)                       # JMP targets are relocated by the load offset, as the SDK does
   sm = block.sm[0]
   sm.configure(program=loaded, set_base=0, set_count=1)      # wrap range and side-set come from the program
   block.dirs = 1                                  # pin 0 is an output
   sm.enable()                                     # a state machine runs only when enabled
   block.run(8)

- ``block.load(program)`` takes a ``pioweave.builder.Program`` or a list of 16-bit words (then give ``side=(bits, optional, pindirs)``; ``bits`` counts the enable bit of an optional side-set, as the hardware register does). It returns a ``Loaded`` (offset, length, wrap range, side-set). ``block.unload(loaded)`` frees the memory.
- ``sm.configure(program=loaded, **settings)`` sets ``in_base``, ``in_count``, ``out_base``, ``out_count``, ``set_base``, ``set_count``, ``side_base``, ``jmp_pin``, ``in_shift`` / ``out_shift`` (``"right"`` or ``"left"``), ``autopush``, ``autopull``, ``push_thresh``, ``pull_thresh``, ``status_sel`` (``"tx"``, ``"rx"``, ``"irq"``), ``status_n``, ``fifo_join``, ``clkdiv``.
- Host side: ``sm.put(word)`` writes the TX FIFO (False and counted in ``tx_dropped`` when full), ``sm.get()`` reads the RX FIFO (``None`` when empty), ``sm.force(word)`` executes an instruction in the next cycle, ``sm.set_pc(address)``, ``sm.enable()`` / ``sm.disable()``, ``sm.restart()`` (the chip's SM_RESTART: clears the ISR, the shift counters, the delay counter, a stalled forced or EXEC instruction and OUT_STICKY writes; the program counter, the OSR, X and Y, the FIFOs and the enable are not affected, RP2040 CTRL register p.372), ``sm.clkdiv_restart()`` and ``sm.reset()`` (the model's power-on state: everything 0, FIFOs empty, disabled; the datasheet gives no power-on values for X, Y, the ISR and the OSR).
- Pins: ``block.set_input(pin, level)`` or ``block.ext = bits``; ``block.pads()`` is what is on the pads (the block's output where it drives the pin, the outside world elsewhere); ``block.out_level``, ``block.dirs``.
- Several blocks: ``a.neighbours[1] = b; b.neighbours[-1] = a`` link them for ``IRQ NEXT`` / ``PREV``.
- ``block.recording = True`` keeps a trace, one record per cycle with the pads, directions, IRQ flags and a snapshot of every state machine (``block.trace``).

Running a test case
-------------------

``run_case(program, cycles, version=0, stim=None, tx=(), tx_feed=(), drain=True, sync_cycles=0, gpio_base=0, record=False, on_error="raise", **settings)`` loads the program into a fresh block, configures state machine 0 and runs it. It uses the conventions of bitweave's PIO simulator and of pioweave's Amaranth reference simulation: input waveforms are strings of ``0`` and ``1`` per cycle (the last character repeats), ``tx_feed`` words are written before the cycle's instruction runs, the host drains the RX FIFO after every cycle (``drain=True``), and ``Result.pads[t]`` is what is on the pads after cycle ``t``. The settings include ``x``, ``y``, ``dirs`` and ``start_pc``. ``Result.wave(pin)`` gives a pin as a string; ``Result.rx`` is ``[(cycle, word)]``; ``Result.error`` and ``error_cycle`` are set with ``on_error="stop"``.

``run_compiled(compiled, cycles, stim, tx, tx_feed, drain)`` runs a program compiled from an Amaranth design with the configuration the compiler derived (real GPIO numbers for the stimulus). ``to_vcd(trace, pins)`` writes a VCD of recorded pins.

Unit tests and cycle counts: ``emulate()``
------------------------------------------

``emulate(program, stop_when=..., ...)`` is a generator that yields a ``(before, after)`` pair of frozen ``State`` snapshots for every executed instruction (``per="instruction"``, the default) or for every system clock cycle (``per="cycle"``), the style of a unit test: step, look at the registers, the FIFOs and the pins, stop when a condition holds. A ``State`` has ``clock``, ``pc`` (relative to the program), ``x``, ``y``, ``isr``, ``osr``, ``isr_count``, ``osr_count``, ``tx`` and ``rx`` (tuples), ``pins`` (what is on the pads), ``dirs``, ``irq``, ``opcode`` (the word handled in the step), ``kind`` (``"executed"``, ``"stalled"``, ``"forced"``, ``"delay"`` or ``"idle"``), ``delay_left`` and ``next_opcode``. With ``per="instruction"`` a step ends when the instruction has completed and its delay cycles have run, so ``after.clock - before.clock`` is what the instruction cost, stalls included. The configuration keywords are those of ``run_case``; ``stop_when(state)`` sees the state a step would start from, ``cycles`` and ``max_cycles`` bound the run.

.. testcode::

   from pioweave.builder import Program, X, X_DEC
   from pioweave.emulator import emulate

   p = Program("count")
   p.set(X, 2)
   p.label("loop")
   p.jmp("loop", when=X_DEC)
   for before, after in emulate(p, stop_when=lambda s: s.x == 0xFFFFFFFF):
       print(after.clock, before.x, "->", after.x)

.. testoutput::

   1 0 -> 2
   2 2 -> 1
   3 1 -> 0
   4 0 -> 4294967295

Inputs come from ``input_source(state)`` (called before every system clock cycle, returning the levels the outside world drives) or from ``stim`` waveforms; ``initial_state`` starts from given registers, FIFOs and pins. Built on it:

- ``count_cycles(program, stop_when)``: the system clock cycles until the condition holds.
- ``pin_history(program, cycles, pins=[...])``: ``{pin: [level after each cycle]}``; ``waveform_text(history)`` prints it as text; ``plot_waveform(history)`` draws it with matplotlib, imported only when called.
- ``to_vcd(result.trace, pins)`` writes a VCD for GTKWave or Surfer from a ``run_case(..., record=True)`` run.

.. testcode::

   from pioweave.builder import Program, PINS
   from pioweave.emulator import pin_history, waveform_text

   blink = Program("blink")
   with blink.wrapped():
       blink.set(PINS, 1, delay=1)
       blink.set(PINS, 0, delay=1)
   print(waveform_text(pin_history(blink, 8, pins=[0], set_base=0, set_count=1, dirs=1), names={0: "led"}))

.. testoutput::

   led |##__##__|

Porting tests from rp2040-pio-emulator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The mapping from the names that project documents (its README and guides) to this package. The behaviour differs in the points listed after the table; see :doc:`design-notes/emulator-comparison` for the full comparison.

.. list-table::
   :header-rows: 1

   * - rp2040-pio-emulator
     - pioweave.emulator
   * - ``emulate(opcodes, stop_when=lambda opcode, state: ...)``
     - ``emulate(words, stop_when=lambda state: ...)``; the opcode about to run is ``state.next_opcode``
   * - ``before, after`` per instruction
     - the same with ``per="instruction"`` (default); ``per="cycle"`` for one pair per system clock cycle
   * - ``State.clock``
     - ``State.clock``
   * - ``program_counter``
     - ``pc``, relative to the start of the program (it equals the absolute address for a program at offset 0)
   * - ``pin_values``, ``pin_directions``
     - ``pins`` (the pads), ``dirs``
   * - ``transmit_fifo``, ``receive_fifo`` (deques)
     - ``tx``, ``rx`` (tuples)
   * - ``input_shift_register``, ``output_shift_register`` (contents and counter)
     - ``isr``, ``isr_count``, ``osr``, ``osr_count``
   * - ``x_register``, ``y_register``
     - ``x``, ``y``
   * - ``initial_state=State(...)``
     - ``initial_state=State(...)``
   * - ``input_source`` (a function of the state or of the clock)
     - ``input_source(state)``; use ``state.clock`` for the cycle
   * - ``auto_pull``, ``auto_push``
     - ``autopull``, ``autopush``
   * - ``pull_threshold``, ``push_threshold``
     - ``pull_thresh``, ``push_thresh`` (OSR bits shifted out before a pull; ISR bits shifted in before a push)
   * - ``shift_isr_right``, ``shift_osr_right``
     - ``in_shift="right"`` or ``"left"``, ``out_shift``
   * - ``out_base``, ``out_count``, ``set_base``, ``set_count``, ``jmp_pin``
     - the same names; the reset value of ``out_count`` is 0 (hardware), so pass ``out_count=32`` where the other project defaults to 32
   * - ``side_set_base``, ``side_set_count``
     - ``sideset_base``; the width comes from ``Program(side_set=n, side_opt=...)`` or, for a word list, ``side=(bits, optional, pindirs)``
   * - ``wrap_target``, ``wrap_top``
     - the same names for a word list (program-relative); a ``Program`` carries its own
   * - opcodes from Adafruit's PIOASM
     - any list of 16-bit words, or a ``pioweave.builder.Program``
   * - one state machine only
     - ``emulate`` runs state machine 0 of a block; use ``PioBlock`` for several

Differences to know when porting: the pins are the 32-pin window of the block with the two-flop synchroniser off in ``emulate`` and ``run_case`` (``sync_cycles=0``, as bitweave's simulator) and on in ``PioBlock`` (2); JMP targets of a word list are used as written when the program is loaded at offset 0; undefined programs raise ``EmulationError`` instead of ending the run quietly.

From the command line
---------------------

::

   python -m pioweave emulate uart_tx [--chip CHIP] [--cycles N] [--stim GPIO=0101...] [--tx W,W] [--tx-feed CYCLE=WORD] [--no-drain] [--vcd FILE]

compiles an example design (or ``module:Class``), runs it in the emulator and prints the program, the output pin waveforms, the words received and, with ``--vcd``, writes a waveform file.

Checking a bitweave case
------------------------

bitweave can ask the emulator for a second opinion on a simulation case. In a ``[pio.NAME]`` section of ``bitweave.toml``, ``cross_check = "pioweave"`` makes bitweave run, for every ``[[pio.NAME.sim]]`` case after its own simulator, ::

   python -m pioweave.emulator.check PROGRAM_JSON CASE_JSON

``PROGRAM_JSON`` is the file ``pioasm -o json`` writes (programs with ``name``, ``pioVersion``, ``wrapTarget``, ``wrap``, ``origin``, ``sideset``, ``instructions`` and ``usedGPIORanges``) and ``CASE_JSON`` is the case as a JSON object with the keys of bitweave's case (``cycles``, ``start_pc``, ``x``, ``y``, the pin mapping, shift and FIFO settings, ``tx``, ``tx_feed``, ``rx_drain``, ``rx_regs``, ``dirs``, ``pins_in``, ``irq_set``, ``irq_clear``, ``sync_cycles``, ``gpio_base``, ``sm`` and the ``expect*`` keys). The case runs with the semantics bitweave documents: state machine ``sm`` of one block, the 32-pin window, no input synchroniser unless ``sync_cycles`` says so, the host draining the RX FIFO after every cycle unless ``rx_drain`` is false, ``set_count`` 0 when the case does not give it (the SDK's default configuration; the register reset value of 5 applies only to a state machine whose PINCTRL was never written), and the ``loopback="direct"`` convention. One JSON object is printed on standard output:

.. list-table::
   :header-rows: 1

   * - Key
     - Content
   * - ``ok``
     - no error and every ``expect*`` of the case holds
   * - ``error``, ``error_cycle``
     - the message (``cycle N: ...`` for a run-time error) and its cycle, or null
   * - ``cycles``
     - the cycles of the case
   * - ``pads``, ``dirs``
     - ``{pin: waveform}`` (one 0 or 1 per cycle run) for every pin in ``expect`` / ``expect_dirs`` and every pin the program drives
   * - ``rx``, ``rx_left``, ``tx_left``
     - the words the draining host read with their cycles, the words left in the RX FIFO, the words left in the TX FIFO
   * - ``final``
     - ``x``, ``y``, ``pc`` (relative to the program, unsigned 32 bit), ``isr``, ``osr``, ``irq_flags``
   * - ``unsupported``
     - keys of the case this version does not know; they are not applied, so the result says nothing about them
   * - ``mismatches``
     - the first expectations that did not hold

The exit status is 0 whenever a result was produced, even when the program errored (an error is data), and 2 on a usage error. The same function is ``pioweave.emulator.check.check_case(programs, case)``. bitweave compares ok / error, the per-cycle pad and direction waveforms, the words with their cycles, the FIFO counts and the final registers, and reports the first differences in its stage ``xcheck:CASE``. Two models written from the same datasheets that agree are still models: nothing has run on a chip.

How it was checked
------------------

.. list-table::
   :header-rows: 1

   * - Check
     - What
     - Needs
   * - ``tests/test_emulator.py``
     - 41 directed tests of every instruction and feature, with expected values worked out from the datasheet semantics
     - nothing
   * - ``tests/test_emulator_datasheet.py``
     - the facts that were [verify] before they were read in the datasheets (reset values, output priority, synchroniser, divider, joins, MOV STATUS, IRQ flags, delay and side-set, EXEC and forced instructions), each test naming the section and page
     - nothing
   * - ``tests/test_emulator_check.py``
     - ``python -m pioweave.emulator.check`` directed (result fields, exit status, unsupported keys, errors as data, usage errors) and differential: the same random programs and cases run through bitweave's own ``pio`` flow and through the check must agree on ok / error, the pad and direction waveforms, the words received, the FIFO counts and the final registers
     - for the differential part, the ``bitweave`` binary and ``pioasm``
   * - ``tests/test_emulator_trace.py``, ``tests/test_emulator_parity.py``
     - the ``emulate`` API, and behaviours that another emulator documents (pin ranges that wrap after GPIO 31, thresholds of PUSH IFFULL and PULL IFEMPTY, stalls and delays, several state machines, shift directions), worked out fresh from the datasheets
     - nothing (matplotlib for one plot test, skipped without it)
   * - ``tests/test_emulator_amaranth.py``
     - every example design and random designs (control flow and data path, all chips) run in the emulator and in the Amaranth simulator must agree pin by pin, cycle by cycle, on the words pushed and the FIFO contents
     - nothing
   * - ``tests/test_emulator_differential.py``
     - random programs (every instruction form, both PIO versions, random configurations, input waveforms and FIFO traffic) must agree with bitweave's PIO simulator in every cycle on the instruction executed, all registers, the pads, the directions, the IRQ flags, the words received and the FIFO counts; programs that do something undefined must stop in the same cycle. Extra families aim at OUT / MOV EXEC with valid instruction words in the FIFO, IRQ flags and MOV STATUS, the FIFO joins and the RX FIFO registers, autopush and autopull with every threshold, and the pin directions (SET, OUT, MOV and side-set)
     - the ``bitweave`` binary and ``pioasm``

The emulator and bitweave's simulator were written separately from the same datasheets, so the comparison catches slips in either, not a misreading common to both. **Nothing has run on a chip**: the hardware test plan is :doc:`hardware-testing` and is a plan only. The facts of this page were read in the RP2040 datasheet (build-date 2025-02-20, build-version 3184e62-clean) and the RP2350 datasheet (Release 8, build date 01/10/2026, build version 80d627281ed5) with printed page numbers; what those documents do not say stays [verify]: where the fractional clock divider starts its first period after enabling, whether a stalled instruction asserts its side-set again on every retry (the datasheet says it takes effect "on the first cycle"; the emulator applies it once), the side-set of a forced instruction, the details of OUT_STICKY and INLINE_OUT_EN, the output-to-input delay of a state machine reading its own pins on the RP2350 (the RP2040 states 2 and 4 cycles), and what a MOV STATUS compare with N above the FIFO depth does (reserved on the RP2350, accepted here), and whether an IN with autopush that stalled on a full RX FIFO shifts again when it retries (modelled: no; RP2040 3.5.4.1 p.337 is silent). A multi-state-machine program is verified only by the directed tests, not by the differential test (bitweave's simulator runs one state machine and has no forced instructions, clock divider or OUT_STICKY).
