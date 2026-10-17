UART transmitter and receiver
=============================

An 8N1 transmitter and receiver, 8 state machine cycles per bit, written as Amaranth FSMs on the named pins ``uart_tx`` and ``uart_rx`` of the Pico board (GPIO 0 and 1).

::

   python examples/uart/uart.py --out build/uart

The script writes the ``.pio`` project of each design (``build/uart/tx``, ``build/uart/rx``), runs the transmitter in the emulator with four bytes in the TX FIFO (the FIFO is four words deep), feeds the transmitter's pin waveform to the receiver and checks that the same four bytes arrive. The receiver pushes a word with the byte in bits 31..24 (the ISR shifts right), so the script shifts it down by 24.
