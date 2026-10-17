Blink
=====

Blinks the user LED of a board. The design is an Amaranth FSM; the board decides which GPIO the name ``"led"`` is.

::

   python examples/blink/blink.py --board pico --out build/blink
   python examples/blink/blink.py --board weact
   python examples/blink/blink.py --board waveshare

Boards: ``pico``, ``pico2``, ``weact`` (WeAct Studio RP2350B core, LED on GPIO 25), ``waveshare`` (Waveshare Core2350B, LED on GPIO 39).

Writes ``blink.pio``, ``bitweave.toml`` and ``README.md`` into the output directory and checks in the emulator that the LED is on for 994 cycles and off for 994 cycles. Nothing was run on a board.
