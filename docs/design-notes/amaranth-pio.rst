PIO programs written in Amaranth: design note
=============================================

Code: pioweave/ (platform.py, compile.py, data.py, project.py, designs.py). Tests: tests/test_compile.py, test_refusals.py, test_project.py, with cosim.py.

1. Idea
-------

Amaranth has one platform module per target (``amaranth.build.Platform``; the vendor modules are in ``amaranth.vendor``). This adds one for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 (the RP2354 is an RP2350 with 2 MB of flash in the package, owner 2026-10-17: PIO version 1). The variants ``rp2350a``, ``rp2350b``, ``rp2354a``, ``rp2354b`` are the same PIO and differ only in the pinout (owner, 2026-10-17; RP2350 datasheet Table 1: A = QFN-60 with 30 GPIOs, B = QFN-80 with 48 GPIOs); ``rp2350`` and ``rp2354`` without a letter keep the 30-GPIO pinout. A design is an ordinary Amaranth Elaboratable. Its build products are a PIO project of the kind the user chooses:

.. list-table::
   :header-rows: 1

   * - ``project=``
     - files
   * - ``"pio"``
     - ``NAME.pio``, ``bitweave.toml`` with a simulation case, ``README.md``
   * - ``"pico-c"``
     - the same, plus ``CMakeLists.txt`` and ``main.c`` of a Pico SDK project
   * - ``"micropython"``
     - the same, plus ``NAME_pio.py`` (written by ``pioasm -o python``) and ``main.py``

::

   platform = RP2040PioPlatform(sm_clock_hz=1_000_000)
   platform.build(SquareWave(), "square", "build/square", project="pico-c", sim=SimOptions(factory=lambda: (SquareWave(), RP2040PioPlatform()), cycles=24))

A PIO state machine is an FSM whose state is the program counter and that executes one instruction per clock cycle. A design written as one Amaranth FSM in which every state does what one PIO instruction does is therefore cycle-equivalent to the program, and the Amaranth simulation of the design is the reference. The compiler maps one FSM state to one instruction and refuses everything that would change the cycle behaviour.

2. What is accepted (pioweave/compile.py reads the Amaranth 0.5.10 IR, Fragment.statements)
-------------------------------------------------------------------------------------------

One ``m.FSM()`` in the ``sync`` domain, no submodules, no logic outside the FSM, no combinational logic. States are placed in definition order (Amaranth numbers FSM states by first mention, but the cases of the generated Switch follow the source order; the compiler uses the source order, the first defined state is PIO address 0).

.. list-table::
   :header-rows: 1

   * - In the state
     - PIO
   * - ``pin.o.eq(C)`` on a requested output (alone)
     - ``set pins, C`` (set group: consecutive GPIOs, at most 5 bits)
   * - ``pin.o.eq(C)`` together with another operation, a test, a jump or a wait
     - side-set (at most 4 bits with the enable bit; optional side-set, so a state that does not assign it leaves the pins alone, as in Amaranth)
   * - ``x.eq(C)``, C in 0..31 (``x = platform.scratch("x")``, 32 bits)
     - ``set x, C``
   * - ``x.eq(y)``, ``x.eq(~y)``, ``x.eq(~x)``
     - ``mov x, y``, ``mov x, !y``, ``mov x, !x``
   * - ``x.eq(x - 1)`` with ``m.If(x != 0)`` selecting the jump
     - ``jmp x-- LABEL`` (decrements in both branches, like the instruction)
   * - ``x.eq(x - 1)`` and nothing else, going to the next state
     - ``jmp x-- NEXT`` (decrement without branching)
   * - ``m.If(x == 0)``, ``y == 0``, ``x != y`` selecting a jump (the other branch is the next state)
     - ``jmp !x``, ``jmp !y``, ``jmp x!=y``
   * - ``m.If(pin.i)`` selecting a jump
     - ``jmp pin LABEL`` (one JMP pin per program)
   * - a state that stays until ``m.If(pin.i)`` / ``m.If(~pin.i)`` lets it go to the next state
     - \`wait 1
   * - ``m.next = OTHER`` and nothing but side-set
     - ``jmp OTHER``
   * - states without effect after an instruction (nothing jumps to them)
     - folded into the delay ``[n]`` of that instruction, up to the bits left after the side-set; more become ``nop``
   * - the last state going to a state other than the first, with a ``set``/``mov`` operation
     - ``.wrap_target`` at that state

Jumps and waits take their layout from the next-state rule: PIO can jump on ``!x``, ``x--``, ``!y``, ``y--``, ``x!=y`` and on a pin that is high, so the compiler picks, of the two branches, the one that is not the next state as the jump and negates the test if needed. A branch state cannot also carry a delay (the taken path would pay it too), so only ``set``/``mov``/``nop``/``wait`` states take folded delays.

2a. The data path: FIFOs, OSR and ISR (pioweave/data.py)
--------------------------------------------------------

``sh = platform.shifter(m, out_shift="right", in_shift="right")`` (once per design, inside ``elaborate``) creates the OSR, ISR, their bit counters, and the model of the two 4-word FIFOs (a submodule, ``FifoModel``). Its methods add to the current FSM state exactly the register updates of one PIO instruction, so the Amaranth simulation of the design stays the reference:

.. list-table::
   :header-rows: 1

   * - In the state
     - PIO
   * - ``with sh.pull(m): m.next = ...``
     - ``pull block``: the state stays while the TX FIFO is empty (the FIFO flag is the test, so the same rules as ``wait`` apply: continue with the next state, side-set assigned before the ``with``)
   * - ``with sh.pull(m, block=False): ...``
     - ``pull noblock``: X is loaded into the OSR when the FIFO is empty
   * - ``with sh.push(m): m.next = ...`` / ``sh.push(m, block=False)``
     - ``push block`` / ``push noblock`` (the word is dropped and the ISR cleared when the RX FIFO is full)
   * - ``sh.out(m, pin)`` / ``sh.out(m, "x" | "y" | "null", n)``
     - ``out pins, N`` (N = width of the pin group unless given) / ``out x, n`` / ``out y, n`` / ``out null, n``
   * - ``sh.in_(m, pin)`` / ``sh.in_(m, "x" | "y" | "null", n)``
     - ``in pins, N`` / ``in x, n`` / ``in y, n`` / ``in null, n``

``out_shift`` and ``in_shift`` are the shift directions; they go to the generated C (``sm_config_set_out_shift`` / ``sm_config_set_in_shift``), MicroPython (``out_shiftdir``, ``in_shiftdir``) and bitweave case (``out_shift``, ``in_shift``). ``out pins`` makes the pin group the OUT group (``out_base``, ``out_count``), ``in pins`` the IN group (``in_base``); each program has one of each. One of them can also be a side-set or ``set`` pin (the UART transmitter drives its pin by side-set and by ``out pins``). ``n`` is a constant from 1 to 32 (an ``in pins`` count cannot exceed the width of the pin group).

How it is built, because it explains the limits. The design needs no combinational logic: a word taken from the TX FIFO or stored in the RX FIFO is recorded by a toggle register of the design (``tx_pop``, ``rx_push``, with ``rx_data`` = the ISR at the push), and the model applies it one clock edge later while its flags and its head already account for the pending word, so the next instruction sees what the PIO shows it. The compiler reads each helper through two marker registers (``op``, ``arg``), checks that the statements of a state on the shifter registers (OSR, ISR, their counters, the toggles) are exactly those of the one operation the marker names, and compiles the state to that instruction. A state with two operations, an operation together with a pin or register operation or a jump (apart from side-set), an operation in a state with a test, a blocking pull / push that does not continue with the next state, and hand-written assignments to the shifter registers are refused with the source line.

The reference simulation (``project.amaranth_run``) takes the words in the TX FIFO at cycle 0 (``tx``), words the host writes later (``tx_feed``, ``(cycle, word)``: the instruction of that cycle sees them, as in bitweave's simulator) and ``drain`` (the host empties the RX FIFO every cycle, the default, or not), and returns the pin waveforms, the words pushed with their cycles, and what is left in the FIFOs. The generated bitweave case carries ``tx``, ``tx_feed``, ``rx_drain`` and ``expect_rx`` (words and order), the generated C project writes the words with ``pio_sm_put_blocking`` and prints the received words (``printf``, USB stdio), and the MicroPython project uses ``sm.put`` / ``sm.get`` [verify on a device]. The examples are ``uart_tx`` (the SDK's transmitter, 2 cycles per bit), ``uart_rx`` (receiver, 8 cycles per bit, ``in pins``, ``push``), ``byte_swap`` (``out x/y``, ``in null/y/x``, left shifts) and ``noblock_echo`` (``pull noblock``, ``push noblock``).

2b. Autopull and autopush (item 1 of the front-end list)
---------------------------------------------------------

``platform.shifter(m, autopull=True, pull_thresh=n, autopush=True, push_thresh=n)`` (thresholds 1 to 32, default 32) model the two automatic transfers exactly as the emulator (written from RP2040 datasheet 3.5.4 and RP2350 datasheet 11.5.4) and bitweave's simulator do them. The facts, as the emulator implements them (``pioweave/emulator/core.py``, ``_exec`` and ``_background_pull``):

- *OUT with autopull.* If the OSR has shifted out at least ``pull_thresh`` bits (``osr_n >= pull_thresh``) the OUT does not execute: that cycle is a stall in which the OSR is refilled from the TX FIFO if it holds a word (and the stall repeats while it is empty). Otherwise the OUT executes, and in the same cycle, after the shift, the OSR is refilled when the new count has reached the threshold and the TX FIFO holds a word (the *background refill*): the next OUT then runs without a stall.
- *Background refill in every other cycle.* Every cycle that is not an executing OUT (a delay cycle, a stalled cycle of any instruction, any other instruction) refills the OSR when ``osr_n >= pull_thresh`` and the TX FIFO holds a word.
- *IN with autopush.* The IN shifts once (the flag ``in_shifted`` of the emulator remembers it); when the ISR count has reached ``push_thresh`` the word is pushed and the ISR cleared if the RX FIFO has room, otherwise the IN stalls (without shifting again) until it has.
- ``pull`` with autopull is a no-op when ``osr_n == 0`` (the OSR was just refilled); a delay follows the completed instruction only.

Model. The design needs no new logic in the states for the background refill: ``Shifter.__init__`` adds one statement outside the FSM, ``with m.If(bg): osr, osr_n, tx_pop <- refill`` where ``bg`` is ``osr_n >= pull_thresh and tx_ready`` (a combinational flag from the submodule ``ShiftFlags``, which holds every comparison the helpers need so that the design module stays free of ``comb`` logic). States that assign the OSR later in the same cycle override it, which is exactly the rule above (an executing OUT assigns ``osr`` / ``osr_n`` / ``tx_pop`` itself with the refill-after-shift, a stalled OUT assigns nothing and so the statement refills it). The OUT and IN helpers become context managers whose body (``m.next``) runs when the instruction completes::

    sh = platform.shifter(m, out_shift="right", autopull=True, pull_thresh=8)
    with m.State("BIT"):
        with sh.out(m, tx):              # out pins, 1; stalls for a refill when the OSR holds 8 bits shifted out
            m.next = "LOOP"

With autopull (OUT) or autopush (IN) a plain ``sh.out(m, ...)`` / ``sh.in_(m, ...)`` is a design error: the state would not stall, so the Amaranth simulation and the program would differ. The helper records it and ``compile_design`` raises with the line of the call. The compiler checks that the statements of such a state are exactly those of the operation (the owned-signal check of section 2a), that the state tests the flag the helper created, and that the stalling branch is the state itself. A hand-written ``m.If`` around the helper is refused as a second test in the state.

The generated code carries the configuration like the shift direction does: ``sm_config_set_out_shift(&c, right, autopull, pull_thresh)`` and ``sm_config_set_in_shift(&c, right, autopush, push_thresh)`` in C, ``autopull=``, ``autopush=``, ``pull_thresh=``, ``push_thresh=`` in the MicroPython decorator [verify the names on a device], and ``autopull``, ``autopush``, ``pull_thresh``, ``push_thresh`` in the bitweave case and in ``Compiled.sim_config()``.

2c. pull ifempty, push iffull, mov, out isr, in isr / osr, jmp !osre (item 2)
------------------------------------------------------------------------------

All of these are one instruction without a stall of their own (the two with a ``block`` form stall exactly as before), so they use the marker technique of section 2a: a helper adds the statements, the compiler checks that the state's statements on the shifter registers are exactly those of the marked operation.

- ``sh.pull(m, ifempty=True)`` / ``sh.push(m, iffull=True)``. The emulator: ``pull`` is a no-op (completes, takes nothing, stalls never) when ``autopull and osr_count == 0`` or ``ifempty and osr_count < pull_thresh``; ``push`` is a no-op when ``iffull and isr_count < push_thresh``. The helper builds the no-op condition from the OSR / ISR counters (``osr_n``, ``isr_n``) and the thresholds, then the same statements as before with ``take = not noop and fifo_ready`` as the condition of every register change, and for the blocking forms the flag ``noop or fifo_ready`` in the place of ``tx_ready`` / ``rx_space``. The ``ifempty`` / ``iffull`` bit travels in the marker ``arg`` (1), because it is not recoverable from the statements.
- ``sh.mov(m, dst, src, op)``. ``dst`` x, y, osr, isr or an output pin group; ``src`` x, y, null, status, isr, osr or an input pin group; ``op`` none, invert, reverse (bit reverse, ``val[::-1]``). The marker is ``MOV`` and ``arg`` encodes ``dst | src << 3 | op << 6`` with the 3-bit fields of the instruction word. ``mov osr`` and ``mov isr`` set the count to 0 as the emulator does; with autopull the ``mov osr`` state also assigns ``tx_pop`` to itself, which cancels the background refill of that cycle (the count is 0 after the instruction, so the refill the emulator does after the instruction does not happen). ``mov pins`` writes the OUT group (``out_count`` is the width of the group). The plain ``x.eq(y)`` / ``x.eq(~y)`` forms of section 2 stay.
- ``mov ..., status``. ``platform.shifter(m, status="tx" | "rx", status_n=N)`` selects the source (FIFO level below N: all ones, else zero), through ``tx_count`` / ``rx_count`` of the FIFO model, which are the levels the design sees (a pending pop or push included). The configuration goes to ``sm_config_set_mov_status`` and the bitweave keys ``status_sel`` / ``status_n``. ``status="irq"`` (RP2350) is part of item 3.
- ``mov x, pins`` reads all 32 pins from the IN base. On PIO version 1 the program masks the input to the group width (``in_count``, ``sm_config_set_in_pin_count``), which is exactly the group the model has; on PIO version 0 the other 31 - width pins would be read too, which the model cannot reproduce, so ``sh.mov`` refuses it there. MicroPython has no option for ``in_count`` or the status source [verify]: ``project="micropython"`` refuses such a design.
- ``out isr, n`` sets the ISR to the n shifted bits and its count to n (emulator ``d == 6``); ``in isr, n`` and ``in osr, n`` shift the low n bits of the register as it was before the instruction.
- ``jmp !osre`` is ``with m.If(sh.osr_not_empty): m.next = LABEL`` (a flag of ``ShiftFlags``: ``osr_n < pull_thresh``). PIO can jump while the OSR is not empty but not while it is empty, so the test ``~sh.osr_not_empty`` selecting the jump is refused.

Not supported, with the reason in the error of the helper (``ValueError`` when the design is written): ``out pindirs`` and ``mov pindirs`` (the model has no pin direction state: directions are fixed by the host configuration; adding them means ``io`` buffers with output enables and pad waveforms that follow the direction), ``out pc`` and ``mov pc`` (a computed jump: an FSM state cannot jump to an address that is data), ``out exec`` and ``mov exec`` (an instruction taken from data). None of these changes the cycle behaviour of the rest; they are left out because the Amaranth simulation could not stay the reference.

2d. IRQ flags for one state machine (item 3)
--------------------------------------------

``sh.irq(m, flag, "set" | "clear" | "wait", rel=False)`` and ``sh.wait_irq(m, flag, polarity, rel=False)`` model ``irq set | clear | wait`` and ``wait 0|1 irq`` for the one state machine of a design; the flags are set and cleared by the program and by the host. The facts, from the emulator and bitweave's simulator (RP2040 datasheet 3.4.9, 3.4.8, RP2350 datasheet 11.4.9, 11.4.8):

- ``irq set f`` raises flag f and completes. ``irq clear f`` lowers it and completes. Their effect is applied at the end of the cycle; a flag that is set and cleared in the same cycle ends up set (RP2350 datasheet 11.2.7).
- ``irq wait f``: in its first cycle it raises the flag and stalls (the emulator's ``irq_set_done``); it keeps stalling while the flag is up; it completes in the cycle in which it finds the flag down. The flag is lowered by the host (or another state machine), never by the instruction itself.
- ``wait 1 irq f`` stalls until the flag is up, and lowers it when it passes (the clear is applied at the end of that cycle); ``wait 0 irq f`` stalls until it is down and does nothing else.
- ``rel`` adds the state machine number to the low two bits of the flag number (``(f & 4) | ((f + sm) & 3)``). The model is state machine 0 (bitweave's default ``sm``), so ``rel`` changes the program text only; the generated C project claims state machine 0 (``pio_sm_claim(pio, 0)``) when a program has ``rel``, and the MicroPython project already uses state machine 0. The ``PREV`` / ``NEXT`` forms of PIO version 1 refer to other PIO blocks and are not supported.
- The host: bitweave's ``irq_clear`` / ``irq_set`` entries ``{cycle, flag}`` are applied at the start of a cycle, before the instruction, clear first. ``SimOptions(irq_set=[(cycle, flag)], irq_clear=[...])`` is the same in the Amaranth reference simulation.

Model. ``IrqModel`` (a submodule, created by the first helper that needs it) keeps the flags in a register and takes the program's changes through toggle registers, one per flag for set and one for clear, exactly as the FIFO model takes pops and pushes: a toggle in cycle t is a pending change in cycle t + 1, and the combinational views already include it (``after``), so the next instruction sees the flag. The host events are sampled one cycle early (the host signals the events of cycle t + 1 during cycle t, like ``tx_feed``) and applied to ``after`` to give ``view``, which the instructions of the cycle read (``flag[f]``). The flags after the program's effects of a cycle and before the host's events of the next (``after``) are what the simulators report at the end of that cycle; ``amaranth_run`` returns them per cycle as ``"irq"`` and the tests compare them cycle by cycle with the emulator and with bitweave. The helpers ``irq wait`` (a ``both`` style state, because the stall branch also changes registers: it raises the flag once and remembers it in ``irq_pend``) and ``wait irq`` (``body`` style) follow the rules of 2b: they are used with ``with``, the state stays while the flag says so, and a plain call is refused at compile time. ``mov ..., status`` can read one flag (``status="irq"``, ``status_n=N``, PIO version 1: all ones while flag N is up; ``sm_config_set_mov_status(&c, STATUS_IRQ_SET, N)``).

The generated C project raises a flag for a host event with ``pio->irq_force = 1u << f`` and lowers it with ``pio_interrupt_clear(pio, f)`` (SDK ``hardware/pio.h`` and ``hardware/structs/pio.h``; there is no ``pio_interrupt_set``), after the state machine is enabled, in the order of the cycles; the CPU cannot hit a cycle, so the time is approximate and the comment in ``main.c`` says so. The MicroPython project has no such call [verify] and notes the events in a comment. ``Compiled.uses_irq`` and ``Compiled.irq_rel`` report the use; the case in ``bitweave.toml`` carries the events and ``expect_irq`` (the flags after the last cycle).

2e. wait pin and joined FIFOs (item 4)
---------------------------------------

*wait pin.* ``wait 0|1 pin N`` waits on input pin N relative to IN_BASE (RP2040 datasheet 3.4.3: ``wait polarity pin index``; the emulator reads ``_in_pins(view) >> idx``, the pins rotated to start at ``in_base`` and, on PIO version 1, masked to ``in_count``). The Amaranth form is the test the single-pin ``wait gpio`` already uses, on **one bit of a multi-bit input group**: ``with m.If(bus.i[1]): m.next = NEXT`` (or ``~bus.i[1]``, or ``bus.i[1] == 0``) in a state that stays until then. The compiler turns a test on a bit of a group of more than one pin into ``wait pin k`` and makes the group the IN group (``in_base`` = its first GPIO, ``sm_config_set_in_pins``); a one-bit input resource keeps becoming ``wait gpio N`` (its absolute GPIO number). The group must be the same as the ``in pins`` / ``mov ..., pins`` group (one IN group per state machine). A test on a bit of a group that is a jump (``jmp pin`` takes one GPIO, not a bit of a group relative to IN_BASE) is refused with that reason.

*Joined FIFOs.* ``platform.shifter(m, fifo_join="tx")`` gives one TX FIFO of eight words and no RX FIFO, ``"rx"`` one RX FIFO of eight words and no TX FIFO (RP2040 datasheet 3.5.2 FJOIN_TX / FJOIN_RX). The emulator's rules (``_apply_join``): the depths are (8, 0) and (0, 8); a FIFO of depth 0 is always full and always empty, so ``push block`` and an autopush ``in`` stall for ever with the TX join, ``push noblock`` drops the word (the ISR is cleared), ``pull block`` and an autopull ``out`` stall for ever with the RX join, ``pull noblock`` loads X; host writes to a missing TX FIFO are dropped. ``FifoModel(tx_init, tx_depth, rx_depth)`` takes the depths (storage and pointers of 8 words; a depth of 0 is a flag stuck at "no room" / "no word"); the levels ``tx_count`` / ``rx_count`` for ``mov status`` follow. The RP2350 joins of the RX FIFO registers (``txput``, ``txget``, ``putget``) are not supported. The configuration goes to ``sm_config_set_fifo_join`` (``PIO_FIFO_JOIN_TX`` / ``PIO_FIFO_JOIN_RX``), the MicroPython decorator (``fifo_join=rp2.PIO.JOIN_TX`` / ``JOIN_RX`` [verify]) and the bitweave key ``fifo_join``; words of ``SimOptions.tx`` beyond the depth are dropped in the model and not written by the C and MicroPython projects. The generated projects read the RX FIFO only when there is one.

A correction found while building this (not a new feature): the FIFO model now shows a word the host writes in cycle t to the instruction of cycle t + 1 *after* the design's own pop of cycle t (the pop is pending in that cycle), so a full TX FIFO that the design emptied in cycle t accepts the write, as in the emulator and bitweave. Before, the write was judged against the FIFO as it was before the pop. The reference simulation of a draining host (``host_rx_drain``) takes every pushed word in the cycle it is pushed, so the RX level the design sees (``mov status``, ``rx_space``) is what the emulator shows; ``host_tx_level`` is the level after the design's last pop.

3. What is refused, with the Amaranth source line
-------------------------------------------------

More than one operation per state (it would need two cycles), compound tests, ``Elif`` chains, tests on other signals or on constants other than 0, ``x == y``, a jump on a low pin, a pin or register assignment that is not a constant 0..31 / the other register / its complement / itself minus 1, assignments to a part of a signal, ``m.Switch`` on multi-bit values, a decrement in only one branch, a decrement without the test that needs it, registers or pins with a non-zero reset value (PIO registers reset to 0), two ``set`` pin signals or two side-set signals (merge them into one multi-bit resource or use ``platform.pio_role(pin, "set" | "side")``), pin groups that are not consecutive GPIOs, other clock domains, more than 32 instructions. Pin roles are inferred (a pin assigned alone is a ``set`` pin; a pin assigned together with other work is a side-set pin; one used both ways is in both groups) unless forced.

The data path (FIFOs, OSR, ISR) is described in sections 2a to 2e. Not built yet: ``out pindirs`` / ``pc`` / ``exec`` and ``mov pindirs`` / ``pc`` / ``exec`` (refused with a reason, section 2c), ``irq`` with ``PREV`` / ``NEXT``, the RP2350 FIFO register joins, several state machines, ``.wrap`` placement other than at the last instruction.

The 32-GPIO window of the 48-GPIO parts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A PIO block sees 32 GPIOs at a time; GPIOBASE selects GPIO 0 to 31 or GPIO 16 to 47 (RP2350 datasheet, register GPIOBASE: only 0 and 16). On the B variants the compiler takes the pins the program uses (the ``set`` and side-set groups, the JMP pin, the ``wait gpio`` pins) and picks the smallest base whose window holds all of them; pins that do not fit one window (for example GPIO 5 and GPIO 40) are refused. What the generated files contain:

- the ``.pio`` text keeps real GPIO numbers in ``wait gpio N`` (pioasm accepts 0 to 47 on version 1 and flags the word; the SDK flips bit 4 when it loads the program with GPIO base 16, ``pio.c``, ``add_program_at_offset``);
- ``main.c`` calls ``pio_set_gpio_base(pio, 16)`` before claiming a state machine and loading the program, and passes real GPIO numbers to ``sm_config_set_*_pins``, ``pio_gpio_init`` and ``pio_sm_set_consecutive_pindirs`` (the SDK subtracts the base itself; ``pio.h``, "On RP2350B ...");
- ``bitweave.toml`` carries ``gpio_base = 16`` and the PIO's own pin numbers (GPIO minus the base) in the case, because bitweave's simulator models the hardware view;
- the Pico SDK decides ``PICO_RP2350A`` from the board header: for a B variant the generated ``bitweave.toml`` needs a ``board`` whose header sets ``PICO_RP2350A 0`` (SDK examples with boards definitions in pioweave-boards: ``weact_studio_rp2350b_core``, ``waveshare_core2350b``; the owner supplied the WeAct Studio RP2350B schematic and the Waveshare Core2350B page) (owner, 2026-10-17: owns both the WeAct Studio RP2350B core and the Waveshare Core2350B).

4. Cycle alignment of the two simulators
----------------------------------------

Amaranth sim: the FSM state in clock cycle t does what the instruction executed in cycle t does. Outputs are registers, so the value sampled after the t-th clock edge is the effect of cycle t, which is the pad level the bitweave PIO simulator reports for cycle t. Inputs set before the edge of cycle t are what the state (instruction) of cycle t reads. The 2-flop input synchroniser of the chip is off in the model (``sync_cycles = 0``), so a real pin sampled by a program reaches it two cycles later than in the simulation [verify on a chip].

5. Project kinds
----------------

- ``pio``: ``bitweave.toml`` holds ``[pio.NAME]`` and a ``[[pio.NAME.sim]]`` case configured like the project (set/side-set/JMP pin bases, pin directions) with ``expect`` waveforms taken from the Amaranth simulation of the same design (``factory=`` gives the fresh design/platform pair the simulation needs, because a design requests its pins from the platform when it is elaborated). ``bitweave pio NAME`` then checks the compiled program against the design it came from.
- ``pico-c``: ``main.c`` claims a state machine, loads the program from the pioasm header, sets the set/side-set pin groups and their directions, the input pins, the JMP pin and the clock divider (``sm_clock_hz``), then starts it. Built with the real Pico SDK through ``bitweave firmware`` in the tests (PIO_BUILD_FIRMWARE=1).
- ``micropython``: ``pioasm -o python`` writes the program; the decorator gets the initial pin directions the design needs (``set_init``, ``sideset_init``) and ``main.py`` creates the ``rp2.StateMachine``.

6. Verification
---------------

- Example designs (ten, four of them with the data path) and random designs of the subset (an FSM of 2 to 12 states with random kinds, layouts, side-set, pins and input waveforms): the Amaranth simulation and the bitweave simulator running the compiled program agree pin by pin and cycle by cycle (tests/test_pio_compile.py): 300 random designs of the first generator and 150 of the final one (which also draws RP2350A, RP2350B and RP2354B and puts pins on GPIO 20 to 45 so that the GPIO base is 16) all agree; the default run uses 15, ``PIO_COSIM_N`` sets more.
- Data path: 250 random designs (PULL / PUSH blocking and non-blocking, OUT to pins / X / Y / null, IN from pins / X / Y / null with random counts, both shift directions, TX words at cycle 0 and written later, draining and non-draining host, 1 to 4 output pins, 1 to 3 input pins, optional side-set, RP2040, RP2350, RP2350B and RP2354) agree with the bitweave simulator on pin waveforms, the words pushed with their cycles and the FIFO contents left at the end (one seed of the 250 first run was a generator error, fixed); the data-path refusals and the helper-misuse errors are tested too.
- Refusals: 25 tests in all (14 of them for the data path), each naming its reason (tests/test_refusals.py; several tests contain more than one refused design).
- Project writers: files, content, the generated cases pass in bitweave, and two generated C projects build to a UF2 with the real Pico SDK (tests/test_pio_project.py).

7. Not verified
---------------

Nothing has run on a chip. The MicroPython glue follows the rp2 documentation from memory of its API names and was not run on a device [verify]; pioasm's ``-o python`` output has no ``sideset_opt``-style declaration, so whether MicroPython accepts the optional side-set exactly as written is [verify]. The simulator and the compiler were written by the same author from the same datasheets; the co-simulation catches mismatches between the Amaranth semantics and the PIO model, not a misreading of the datasheets common to the model and the compiler (the model was also cross-checked against rp2040-pio-emulator, see bitweave ``guide pio-and-raspberry-pi``). Only one Amaranth version (0.5.10, pinned) was used: the compiler reads Fragment.statements, which is not a stable public API [verify on other versions].
