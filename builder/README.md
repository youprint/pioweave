# amaranth-rp-builder

Python builder for PIO programs of the Raspberry Pi RP2040, RP2350 and RP2354 (A and B variants). No dependencies. One method per instruction, errors raised when
the program is built, pioasm text, and its own encoder that is compared with the real pioasm in the tests. Design note: ../docs/pio-dsl.md. The Amaranth side is
the package amaranth-rp (../rp).

    from amaranth_rp_builder import Program, PINS
    p = Program("square", chip="rp2040")
    with p.wrapped():
        p.set(PINS, 1, delay=1)
        p.set(PINS, 0, delay=1)
    print(p.pio_text())

Command line: `python -m amaranth_rp_builder emit square build/` (the bitweave generator protocol). Nothing here has run on a chip.
