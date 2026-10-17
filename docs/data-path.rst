The data path: FIFOs, OSR and ISR
=================================

A PIO state machine moves data with two 4-word FIFOs (TX: host to machine, RX: machine to host) and two 32-bit shift registers, the OSR (output shift register) and the ISR (input shift register). ``platform.shifter(m)`` gives an Amaranth design the same pieces, and its helper methods add to the current FSM state exactly the register updates of one PIO instruction, so the Amaranth simulation stays the reference for the compiled program.

The API reference of this part, generated from the docstrings, is in :doc:`reference/data`.

.. code:: python

   sh = platform.shifter(m, out_shift="right", in_shift="right")    # once per design, inside elaborate()

``out_shift`` and ``in_shift`` are the shift directions (``"right"`` or ``"left"``, default right as in the Pico SDK).

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
   * - ``sh.in_(m, pin)`` or ``sh.in_(m, pin, n)``
     - ``in pins, N`` (N defaults to the width of the pin group, at most the width)
   * - ``sh.in_(m, "x" / "y" / "null", n)``
     - ``in x, n`` / ``in y, n`` / ``in null, n`` (shift in zeros)

``n`` is a constant from 1 to 32 (a plain integer). After a helper, write ``m.next = ...`` as usual; a data operation is one instruction, so a state that does one cannot also set a pin or change a register (side-set is fine) or jump to a state other than the next one.

Rules that follow from the instructions:

- A blocking ``pull`` / ``push`` is written with ``with``, because the FIFO flag is the test; the state must continue with the next state, and a side-set value must be assigned *before* the ``with``.
- ``out pins`` makes its pin group the OUT group (``out_base``, ``out_count``), ``in pins`` its pin group the IN group (``in_base``). There is one of each per program. The OUT group may also be the side-set group: the UART transmitter below drives its pin by side-set (idle level and start bit) and by ``out pins``.
- After ``pull``, the OSR counts 0 bits shifted; at reset it is empty (32 shifted). ``out`` shifts bits out from the least significant end (right) or most significant end (left); ``in`` shifts bits in at the most significant end (right) or the least (left).

Examples
--------

The four examples in ``pioweave/designs.py`` (``python -m pioweave list``):

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

The design needs no combinational logic. A word taken from the TX FIFO or stored in the RX FIFO is recorded by a *toggle register* of the design (``tx_pop``, ``rx_push``, with ``rx_data`` holding the ISR at the push); the FIFO model (``pioweave.data.FifoModel``, an ``amaranth.lib.wiring.Component`` and the only submodule allowed) applies the toggle one clock edge later, while its flags (``tx_ready``, ``rx_space``) and its head already account for the pending word, so the next instruction sees what the PIO shows it.

The compiler reads each helper through two marker registers (``op``, ``arg``) and checks that the statements of a state on the shifter registers are exactly those of the one operation the marker names. A state with two operations, a hand-written assignment to ``sh.osr`` or ``sh.isr``, an operation in a state with a test, and a blocking pull or push that does not continue with the next state are refused with the source line.

The host side in simulation
---------------------------

``SimOptions(tx=[...], tx_feed=[(cycle, word)], drain=True)`` (see :doc:`projects <projects>`) describes what the host does: words in the TX FIFO at cycle 0, words written later (the instruction of that cycle sees them, as in bitweave's simulator) and whether the RX FIFO is emptied every cycle. ``pioweave.amaranth_run(factory, cycles, stim, tx, tx_feed, drain)`` returns the pin waveforms, the words pushed with their cycles, and what is left in the FIFOs. The generated bitweave case carries ``tx``, ``tx_feed``, ``rx_drain`` and ``expect_rx``.
