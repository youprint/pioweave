The clock divider
=================

Each state machine runs from the system clock divided by a 16-bit integer and an 8-bit fraction (1/256 steps; ``CLKDIV`` register, RP2040 datasheet 3.5.5, RP2350 11.5.5). ``pioweave.clock`` works out the two fields at design time, with the same quantisation as the emulator, so a number from here and a simulation agree. It needs no Amaranth and no hardware.

.. testcode::

   from pioweave.clock import pio_clkdiv, pio_bitrate_div

   c = pio_clkdiv(125_000_000, 4_000)          # a state machine clock of 4 kHz
   print(c.int_part, c.frac, c.divider)

   u = pio_bitrate_div(125_000_000, 115_200, 8)    # a UART that spends 8 state machine cycles per bit
   print(u.int_part, u.frac, round(u.error_ppm, 1), u.period_jitter_cycles)

.. testoutput::

   31250 0 31250.0
   135 162 6.4 1

The divider is ``round(wanted * 256) / 256`` between 1 and 65536; 65536 is written as ``INT = 0``. A fraction other than 0 makes the periods ``INT`` or ``INT + 1`` system clock cycles (the datasheet's first-order delta-sigma), so an edge can move by one system clock cycle: ``period_jitter_cycles`` is 1 then. Use an integer divider where edge jitter matters. ``DEFAULT_SYS_HZ`` holds the Pico SDK default system clocks (125 MHz RP2040, 150 MHz RP2350 family; pico-sdk ``hardware/clocks.h``) [verify against the datasheets]; pass ``sys_hz`` if the program changes the clock.

From the command line::

   python -m pioweave clock --sys 125000000 --target 4000
   python -m pioweave clock --chip rp2350 --bitrate 115200 --cycles-per-bit 8 --max-error-ppm 5000

Nothing here has been measured on a chip.
