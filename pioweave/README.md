# pioweave

Amaranth platforms for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 (A and B variants), an FSM-to-PIO compiler, and project writers (PIO program,
Pico SDK C project, MicroPython). Depends on Amaranth 0.5.x (tested: every 0.5.0 to 0.5.10, the whole suite on 0.5.1 and 0.5.10, and on the 0.6 development version 0.6.0.dev140; the compiler reads Amaranth's internals, see `_compat.py`) and on pioweave-builder.
Design note: ../docs/amaranth-pio.md.

    python -m pioweave list
    python -m pioweave build square_wave out/ --project pico-c --clock-hz 1000000

Nothing generated here has run on a chip.
