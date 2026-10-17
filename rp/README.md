# amaranth-rp

Amaranth platforms for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 (A and B variants), an FSM-to-PIO compiler, and project writers (PIO program,
Pico SDK C project, MicroPython). Depends on Amaranth 0.5.10 (0.5.x from 0.5.10; the compiler reads Amaranth's private IR, see `_compat.py`) and on amaranth-rp-builder.
Design note: ../docs/amaranth-pio.md.

    python -m amaranth_rp list
    python -m amaranth_rp build square_wave out/ --project pico-c --clock-hz 1000000

Nothing generated here has run on a chip.
