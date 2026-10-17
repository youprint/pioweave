Two state machines in one block
===============================

A baud generator (state machine 0) and a UART transmitter (state machine 1) that handshake through IRQ flag 0: the generator raises the flag every 8 cycles, the transmitter waits for it before the start bit, every data bit and the stop bit (``wait 1 irq 0``, which lowers the flag again). Each machine is an ordinary single-machine design; ``Block`` runs them together.

::

   python examples/block/block.py --out build/block

The script writes the Pico SDK project (``uart_baud.pio`` with two programs, ``main.c`` that claims state machines 0 and 1, loads the programs at their offsets and starts them together, ``bitweave.toml`` with one case per machine), runs the whole block in the Amaranth simulation and in the emulator, checks that they agree cycle by cycle, and decodes the two bytes from the pin waveform of the ``uart_tx`` pin. See docs/blocks.rst.
