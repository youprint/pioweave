Square wave from the builder
============================

A square wave with a chosen high and low time, written with ``pioweave.builder`` (one method per instruction).

::

   python examples/square/square.py --high 5 --low 3 --out build/square
   python examples/square/square.py --high 20 --low 20 --chip rp2350

Writes ``square.pio`` and checks in the emulator that the measured levels equal the requested ones. Each level is one ``set pins`` with a delay, so 1 to 32 cycles; longer times need a counting loop.
