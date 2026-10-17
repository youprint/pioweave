WS2812-style LED waveform
=========================

Serial data for WS2812-style LEDs: 10 cycles per bit, high for 7 cycles for a one and 2 cycles for a zero, with the 24-bit colour taken from the TX FIFO by autopull.

::

   python examples/ws2812/ws2812.py --out build/ws2812

This one uses the Python builder (``pioweave.builder``), not the Amaranth front end: the front end refuses a ``jmp`` whose two branches have different side-set values, and this waveform needs exactly that. The script writes ``ws2812.pio``, runs the program in the emulator with three colours and decodes the waveform back into the 72 bits that were sent. Whether the timing suits a particular LED (the datasheet limits) is not checked [verify].
