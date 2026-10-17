The data path: FIFOs, OSR and ISR
=================================

A PIO state machine moves data with two 4-word FIFOs (TX: host to machine, RX: machine to host) and two 32-bit shift registers, the OSR (output shift register) and the ISR (input shift register). ``platform.shifter(m)`` gives an Amaranth design the same pieces, and its helper methods add to the current FSM state exactly the register updates of one PIO instruction, so the Amaranth simulation stays the reference for the compiled program.

The API reference of this part, generated from the docstrings, is in :doc:`reference/data`.

.. code:: python

   sh = platform.shifter(m, out_shift="right", in_shift="right")    # once per design, inside elaborate()

``out_shift`` and ``in_shift`` are the shift directions (``"right"`` or ``"left"``, default right as in the Pico SDK). The other options are ``autopull=True, pull_thresh=n`` and ``autopush=True, push_thresh=n`` (thresholds 1 to 32, default 32), described in :ref:`autopull-autopush`.

The helpers
-----------

.. list-table::
   :header-rows: 1

   * - Helper
     - PIO instruction
   * - ``with sh.pull(m): m.next = ...``
     - ``pull block``: the state stays while the TX FIFO is empty; the body runs in the cycle a word was taken
   * - ``with sh.pull(m, block=False): m.next = ...``
     - ``pull noblock``: loads X into the OSR when the FIFO is empty
   * - ``with sh.push(m): m.next = ...``
     - ``push block``: stays while the RX FIFO is full
   * - ``with sh.push(m, block=False): m.next = ...``
     - ``push noblock``: when the RX FIFO is full the word is dropped and the ISR cleared
   * - ``sh.out(m, pin)`` or ``sh.out(m, pin, n)``
     - ``out pins, N`` (N defaults to the width of the pin group)
   * - ``sh.out(m, "x" / "y" / "null", n)``
     - ``out x, n`` / ``out y, n`` / ``out null, n`` (discard n bits)
   * - ``sh.out(m, d, n)`` with ``d = platform.directions(pin)``
     - ``out pindirs, n``: the pin directions of the group (:ref:`pin-directions`)
   * - ``sh.in_(m, pin)`` or ``sh.in_(m, pin, n)``
     - ``in pins, N`` (N defaults to the width of the pin group, at most the width)
   * - ``sh.in_(m, "x" / "y" / "null", n)``
     - ``in x, n`` / ``in y, n`` / ``in null, n`` (shift in zeros)
   * - ``with sh.pull(m, ifempty=True): ...`` / ``with sh.push(m, iffull=True): ...``
     - ``pull ifempty`` / ``push iffull`` (blocking or ``block=False``): do nothing until the threshold is reached
   * - ``sh.out(m, "isr", n)``
     - ``out isr, n`` (the ISR gets the n bits and its count is set to n)
   * - ``sh.in_(m, "isr" / "osr", n)``
     - ``in isr, n`` / ``in osr, n``
   * - ``sh.mov(m, dst, src, op="none")``
     - ``mov dst, [op] src`` with the OSR, ISR, pins or status on a side; ``dst``: ``"x"``, ``"y"``, ``"osr"``, ``"isr"``, an output pin group, or a direction register ``d`` (``mov pindirs``, PIO version 1); ``src``: ``"x"``, ``"y"``, ``"null"``, ``"status"``, ``"isr"``, ``"osr"`` or an input pin group (PIO version 1); ``op``: ``"none"``, ``"invert"``, ``"reverse"``
   * - ``sh.irq(m, f)`` / ``sh.irq(m, f, "clear")``
     - ``irq set f`` / ``irq clear f`` (``rel=True`` adds ``rel``)
   * - ``with sh.irq(m, f, "wait"): m.next = ...``
     - ``irq wait f``: raises the flag, stalls until it is lowered
   * - ``with sh.wait_irq(m, f, polarity): m.next = ...``
     - ``wait polarity irq f`` (``wait 1`` lowers the flag when it passes)
   * - ``with m.If(bus.i[k]): m.next = ...`` (also ``~bus.i[k]``) on a multi-pin input group
     - ``wait 1 pin k`` / ``wait 0 pin k`` (relative to the IN base)
   * - ``with m.If(sh.osr_not_empty): m.next = LABEL``
     - ``jmp !osre LABEL``
   * - ``with sh.out(m, ...): m.next = ...``
     - ``out`` with autopull (the instruction can stall for a refill; the body runs in the cycle it completes)
   * - ``with sh.in_(m, ...): m.next = ...``
     - ``in`` with autopush (the instruction can stall while the RX FIFO is full)

``n`` is a constant from 1 to 32 (a plain integer). After a helper, write ``m.next = ...`` as usual; a data operation is one instruction, so a state that does one cannot also set a pin or change a register (side-set is fine) or jump to a state other than the next one.

Rules that follow from the instructions:

- A blocking ``pull`` / ``push`` is written with ``with``, because the FIFO flag is the test; the state must continue with the next state, and a side-set value must be assigned *before* the ``with``.
- ``out pins`` makes its pin group the OUT group (``out_base``, ``out_count``), ``in pins`` its pin group the IN group (``in_base``). There is one of each per program. The OUT group may also be the side-set group: the UART transmitter below drives its pin by side-set (idle level and start bit) and by ``out pins``.
- After ``pull``, the OSR counts 0 bits shifted; at reset it is empty (32 shifted). ``out`` shifts bits out from the least significant end (right) or most significant end (left); ``in`` shifts bits in at the most significant end (right) or the least (left).

``platform.shifter(m, status="tx", status_n=2)`` selects the source of ``mov ..., status`` (``"tx"`` or ``"rx"``, the FIFO level below ``status_n``). The thresholds ``pull_thresh`` and ``push_thresh`` also apply to ``pull ifempty``, ``push iffull`` and ``jmp !osre`` without autopull.

``out pc``, ``out exec`` and the same two destinations of ``mov`` are not supported; the helpers raise ``ValueError`` with the reason when the design is written (``pc`` and ``exec`` would be jumps and instructions taken from data, which an FSM state cannot express). ``pindirs`` is supported through a direction register, see :ref:`pin-directions`; naming it with the string ``"pindirs"`` is refused, because the string cannot say which pin group is meant. ``mov x, pins`` is for PIO version 1 only: the program masks the input to the width of the pin group (``in_count``), while PIO version 0 would also read the neighbouring pins, which the model cannot reproduce.

.. _autopull-autopush:

Autopull and autopush
---------------------

``platform.shifter(m, autopull=True, pull_thresh=8)`` makes the OSR refill itself from the TX FIFO, ``autopush=True, push_thresh=8`` makes the ISR push itself to the RX FIFO. The Amaranth model does exactly what the PIO does (RP2040 datasheet 3.5.4, RP2350 datasheet 11.5.4, as the emulator and bitweave's simulator implement it):

- An ``out`` finds the OSR *empty* when ``pull_thresh`` bits or more have been shifted out. It then does not execute: that cycle is a stall in which the OSR is refilled if the TX FIFO holds a word; the stall repeats while the FIFO is empty.
- Otherwise it executes, and in the same cycle, after its shift, the OSR is refilled if the threshold is now reached and the FIFO holds a word, so the next ``out`` does not stall. Every other cycle (delay cycles, stalled cycles, any other instruction) refills too when the threshold is reached and a word is there.
- An ``in`` shifts once; when the ISR has ``push_thresh`` bits or more the word is pushed and the ISR cleared if the RX FIFO has room, otherwise the ``in`` stalls (and does not shift again) until it has.
- A ``pull`` with autopull on does nothing when the OSR has just been refilled (no bits shifted out).

Because these instructions can stall, write them with ``with``; the body (``m.next = ...``) runs in the cycle the instruction completes, and the state stays until then. Assign a side-set value *before* the ``with``, so that it applies while the state waits:

.. code:: python

   sh = platform.shifter(m, out_shift="left", in_shift="left", autopull=True, pull_thresh=8, autopush=True, push_thresh=8)
   with m.State("OUT"):
       m.d.sync += clk.o.eq(0)
       with sh.out(m, mosi, 1):              # out pins, 1 side 0 [1]
           m.next = "OUT_HOLD"

The ``spi_master`` example is exactly that; it compiles to two instructions, with the configuration in ``sim_config()``:

.. testcode::

   from pioweave import PLATFORMS, compile_design
   from pioweave.designs import SpiMaster

   compiled = compile_design(SpiMaster(), PLATFORMS["rp2040"](), "spi")
   print(compiled.program.pio_text().strip())
   print(compiled.sim_config()["autopull"], compiled.sim_config()["pull_thresh"])

.. testoutput::

   .program spi
   .pio_version 0
   .side_set 1 opt
       out pins, 1 side 0 [1]
       in pins, 1 side 1 [1]
   True 8

A plain ``sh.out(m, ...)`` or ``sh.in_(m, ...)`` with autopull or autopush on is a design error and ``PioCompileError`` names the line (the state would not stall, so the Amaranth simulation and the program would differ). Without autopull or autopush the plain call stays valid, and so does the ``with`` form (its body always runs).

The configuration reaches the generated code: ``sm_config_set_out_shift(&c, right, autopull, pull_thresh)`` and ``sm_config_set_in_shift(&c, right, autopush, push_thresh)`` in C, ``autopull=True``, ``autopush=True``, ``pull_thresh=``, ``push_thresh=`` in the MicroPython decorator [verify the keyword names on a device], and ``autopull``, ``autopush``, ``pull_thresh``, ``push_thresh`` in the bitweave case and in ``Compiled.sim_config()``.

.. _pin-directions:

Pin directions
--------------

A pin group has the direction the host configures and keeps it, unless the design asks for a direction register: ``d = platform.directions(buffer, init=0)`` gives one bit per pin (1 = output); ``init`` is the direction the host sets before the state machine starts (``pio_sm_set_consecutive_pindirs`` in the C project, ``dirs`` in the bitweave case). The register is changed by the program:

- ``sh.out(m, d, n)`` is ``out pindirs, n`` and ``sh.mov(m, d, src, op)`` is ``mov pindirs, src`` (PIO version 1 only); the group of ``d`` is the OUT group, as for ``out pins``.
- ``m.d.sync += d.eq(C)`` alone in a state is ``set pindirs, C``; the group of ``d`` is the SET group, so a program that also uses ``set pins`` must use the same group.
- ``m.d.sync += d.eq(C)`` in a state that does something else (a register operation, a data operation, a test) is the side-set, and then the whole side-set of the program writes pin directions (``.side_set N opt pindirs``): levels and directions cannot both be the side-set of one program.

``pin.o`` keeps being the output level register: ``set pins``, ``out pins`` and the rest write it whatever the direction. What a pin shows is the pad, the design's output where the pin is an output and the outside world (the stimulus) where it is an input; ``pin.i`` of a bidirectional buffer (``platform.buffer(m, "io", ...)``) reads the pad. The simulations report both: ``amaranth_run`` returns the pad in ``"waves"`` and the directions after each cycle in ``"dirs"``, and the generated case carries ``expect`` and ``expect_dirs``. An open-drain line is the level register held at 0 and the direction toggled (``open_drain`` example: it pulls the line low for three cycles, lets go, and waits until the line is high, which another device may hold low):

.. testcode::

   from pioweave import PLATFORMS, amaranth_run, compile_design
   from pioweave.designs import OpenDrain

   factory = lambda: (OpenDrain(), PLATFORMS["rp2040"]())
   design, platform = factory()
   compiled = compile_design(design, platform, "od")
   print(compiled.program.pio_text().strip())
   print(compiled.sim_config())
   run = amaranth_run(factory, 24, {0: "111111110000111111111111"})
   print(run["waves"][0])
   print(run["dirs"][0])

.. testoutput::

   .program od
   .pio_version 0
       set pindirs, 1 [2]
       set pindirs, 0
       wait 1 gpio 0 [2]
   {'set_base': 0, 'set_count': 1, 'dirs': 0}
   000111100000111000111100
   111000011100000111000011

The design pulls the line low in cycles 0 to 2 (direction 1) and lets it go in cycle 3, after which the pad follows the outside world: high through cycle 6. When it lets go again in cycle 10, another device is holding the line low (stimulus 0 in cycles 8 to 11), so the ``wait`` stalls until the stimulus is high again in cycle 12. The ``bus_turnaround`` example (RP2350) takes a word from the TX FIFO, drives its first nibble on a four-pin bus (``out pins, 4``), makes the second nibble the directions (``out pindirs, 4``), reads the pad (``in pins, 4``) and releases the bus (``mov pindirs, null``). Refused: ``mov pindirs`` on PIO version 0 (use ``out pindirs`` or ``set pindirs``), the register in a test or sliced, anything but a constant (or the helper bits) assigned to it, ``set pindirs`` for a group other than the SET group, a jump in the same state, and the combinations that would need two instructions. Design note section 2f has the model and the rules.

Joined FIFOs and ``wait pin``
-----------------------------

``platform.shifter(m, fifo_join="tx")`` joins the two FIFOs into one TX FIFO of eight words (no RX FIFO); ``fifo_join="rx"`` into one RX FIFO of eight words (no TX FIFO). A FIFO that is not there is always full and always empty: with the TX join a ``push block`` (or an autopush ``in``) stalls for ever and ``push noblock`` drops the word; with the RX join a ``pull block`` (or an autopull ``out``) stalls for ever and ``pull noblock`` loads X, and the words the host writes are dropped. More than four words in ``SimOptions(tx=[...])`` are only accepted with the TX join. The RP2350 joins that turn the RX FIFO into registers (``txput``, ``txget``, ``putget``) are not supported. The option goes to ``sm_config_set_fifo_join``, the MicroPython decorator and the bitweave case.

``with m.If(bus.i[k]): m.next = NEXT`` in a state that stays until then, on one bit of an input group of more than one pin, is ``wait 1 pin k`` (``~bus.i[k]`` is ``wait 0 pin k``): the bit is counted from the IN base, the first GPIO of the group, which also becomes ``in_base`` of the state machine. A one-pin input stays ``wait gpio N``. The IN group is one per program: ``in pins``, ``mov ..., pins`` and ``wait pin`` must name the same group. A test of one bit of a group as a *jump* is refused (PIO has no jump on a pin relative to the IN base).

IRQ flags
---------

A PIO block has eight IRQ flags, set and cleared by the programs and by the host. The model is for the one state machine of the design (state machine 0; in a block of several machines the flags are shared, see :doc:`blocks`): the program's own ``irq`` and ``wait irq`` instructions, and the host, which raises and lowers flags in the simulation with ``SimOptions(irq_set=[(cycle, flag)], irq_clear=[(cycle, flag)])`` (the same entries as in bitweave's cases; clear first, then set, at the start of the cycle, before the instruction). ``irq wait f`` raises flag ``f`` in its first cycle and stalls until the host (or another state machine) has lowered it; ``wait 1 irq f`` stalls until the flag is up and lowers it when it passes; ``wait 0 irq f`` stalls until it is down. The ``rel`` form adds the state machine number: the model is state machine 0, so the flag is the same; the generated C project claims state machine 0 when a program uses ``rel``. ``pioweave.amaranth_run`` returns the flags after every cycle as ``"irq"``. ``platform.shifter(m, status="irq", status_n=N)`` (PIO version 1) lets ``mov x, status`` read flag ``N``.

The ``irq_handshake`` example waits in ``irq wait 0`` until the host lowers flag 0 in cycle 12; the output goes low one cycle later, and the flags after each cycle are returned with the waveform:

.. testcode::

   from pioweave import PLATFORMS, amaranth_run
   from pioweave.designs import IrqHandshake

   run = amaranth_run(lambda: (IrqHandshake(), PLATFORMS["rp2040"]()), 20, irq_clear=[(12, 0)])
   print(run["waves"][0])
   print(run["irq"])

.. testoutput::

   11111111111110000000
   [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 4, 4, 4, 4, 4, 4]

The generated C project raises a flag for a host event with ``pio->irq_force = 1u << f`` and lowers it with ``pio_interrupt_clear(pio, f)``, in the order of the cycles but not at the exact cycle (the CPU cannot hit it); the MicroPython project notes the events in a comment because MicroPython has no call for them [verify]. ``irq`` with ``prev`` / ``next`` (other PIO blocks) is not supported.

Examples
--------

The examples with a data path in ``pioweave/designs.py`` (``python -m pioweave list``):

.. list-table::
   :header-rows: 1

   * - Name
     - What it shows
   * - ``uart_tx``
     - the Pico SDK transmitter: ``pull block side 1 [1]``, ``set x, 7 side 0``, ``out pins, 1``, ``jmp x--``. Two cycles per bit.
   * - ``uart_rx``
     - the receiver: ``wait 0 gpio 4``, ``set x, 7 [10]``, ``in pins, 1 [6]``, ``jmp x--``, ``push block``. Eight cycles per bit; the byte arrives in bits 31..24 of the word.
   * - ``byte_swap``
     - ``out x, 8``, ``out y, 8``, ``in null, 16``, ``in y, 8``, ``in x, 8`` with left shifts: a word in, a word out.
   * - ``noblock_echo``
     - ``pull noblock`` and ``push noblock``: X holds 21 when the TX FIFO is empty.
   * - ``osre_loop``
     - ``pull ifempty block``, ``out pins, 1 [1]``, ``jmp !osre``, ``mov y, status`` and ``mov pins, y``: bytes out of a word at a threshold of 8 bits, then the TX FIFO level on the pin.
   * - ``irq_handshake``
     - ``irq wait 0``, ``irq set 2 rel``, ``wait 1 irq 1``, ``irq clear 2``: a handshake with the host through IRQ flags.
   * - ``pin_gate``
     - ``wait 1 pin 1`` and ``wait 0 pin 0`` on a three-pin input group; the output follows.
   * - ``tx_join``
     - ``fifo_join="tx"``: eight words wait in the joined TX FIFO, ``pull block``, ``out pins, 4``.
   * - ``open_drain``
     - pin directions: ``set pindirs, 1 [2]`` pulls a line low (the level register stays 0), ``set pindirs, 0`` lets it go, ``wait 1 gpio 0 [2]`` waits for the line to be high.
   * - ``bus_turnaround``
     - ``out pins, 4``, ``out pindirs, 4``, ``in pins, 4``, ``push block``, ``mov pindirs, null`` on a four-pin bidirectional bus (RP2350).
   * - ``spi_master``
     - a full-duplex SPI master (mode 0, 8 bits, MSB first) with autopull and autopush: ``out pins, 1 side 0 [1]`` and ``in pins, 1 side 1 [1]``, two instructions in all.

A receiver, in full:

.. code:: python

   class UartRx(PioDesign):
       def elaborate(self, platform):
           m = Module()
           rx = platform.buffer(m, "i", "gpio", 4)
           sh = platform.shifter(m, in_shift="right")
           x = platform.scratch("x")
           with m.FSM():
               with m.State("WAIT_START"):
                   with m.If(~rx.i):                       # wait 0 gpio 4
                       m.next = "SETX"
               with m.State("SETX"):
                   m.d.sync += x.eq(7)
                   m.next = "H1"
               for k in range(1, 11):                      # ten hold states: the delay [10] of the set
                   with m.State("H%d" % k):
                       m.next = "H%d" % (k + 1) if k < 10 else "BIT"
               with m.State("BIT"):
                   sh.in_(m, rx)                           # in pins, 1
                   m.next = "B1"
               for k in range(1, 7):                       # six hold states: the delay [6] of the in
                   with m.State("B%d" % k):
                       m.next = "B%d" % (k + 1) if k < 6 else "LOOP"
               with m.State("LOOP"):
                   m.d.sync += x.eq(x - 1)
                   with m.If(x != 0):
                       m.next = "BIT"
                   with m.Else():
                       m.next = "PUSH"
               with m.State("PUSH"):
                   with sh.push(m):
                       m.next = "WAIT_START"
           return m

How it works, and the limits that follow
----------------------------------------

The design needs no combinational logic. A word taken from the TX FIFO or stored in the RX FIFO is recorded by a *toggle register* of the design (``tx_pop``, ``rx_push``, with ``rx_data`` holding the ISR at the push); the FIFO model (``pioweave.data.FifoModel``, an ``amaranth.lib.wiring.Component``) applies the toggle one clock edge later, while its flags (``tx_ready``, ``rx_space``) and its head already account for the pending word, so the next instruction sees what the PIO shows it. The flags the other helpers test (``osr_n < pull_thresh`` and the like) are driven by a second submodule, ``ShiftFlags``, and the IRQ flags by a third, ``IrqModel``, created by the first helper that needs it; these three and the pin buffers are the only submodules allowed. With autopull the shifter also adds one statement outside the FSM, the background refill of the OSR, which the compiler recognises.

The compiler reads each helper through two marker registers (``op``, ``arg``) and checks that the statements of a state on the shifter registers are exactly those of the one operation the marker names. A state with two operations, a hand-written assignment to ``sh.osr`` or ``sh.isr``, an operation in a state with a test, and a blocking pull or push that does not continue with the next state are refused with the source line.

The host side in simulation
---------------------------

``SimOptions(tx=[...], tx_feed=[(cycle, word)], drain=True)`` (see :doc:`projects <projects>`) describes what the host does: words in the TX FIFO at cycle 0, words written later (the instruction of that cycle sees them, as in bitweave's simulator) and whether the RX FIFO is emptied every cycle. ``irq_set`` and ``irq_clear`` (``[(cycle, flag)]``) are the host's IRQ events. ``pioweave.amaranth_run(factory, cycles, stim, tx, tx_feed, drain, irq_set, irq_clear)`` returns the pin waveforms, the words pushed with their cycles, what is left in the FIFOs and the IRQ flags after every cycle. A draining host takes every pushed word in the cycle it is pushed, so the RX FIFO is never full for the design and ``mov x, status`` with the RX source sees an empty FIFO, as in the emulator. The generated bitweave case carries ``tx``, ``tx_feed``, ``rx_drain``, the IRQ events, ``expect_rx`` and ``expect_irq``.
