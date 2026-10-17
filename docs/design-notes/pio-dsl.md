# PIO programs in Python (Amaranth style): design note

Code: builder/src/pioweave.builder/program.py, builder/src/pioweave.builder/examples.py and `python -m pioweave.builder emit`. Tests: builder/tests/test_program.py.

## 1. What this is and is not

- It is host software, an optional Python package of the bitweave repository (the bitweave binary itself contains no Python). A Python builder, in the style of an Amaranth description, for the programs that run in the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354. It writes pioasm source; `pioasm` (Raspberry Pi, from the Pico SDK) assembles it; bitweave (`bitweave pio NAME` with a `generator`) runs it, analyses it and simulates it.
- It is not an FPGA design and not an Amaranth module: the PIO block of a chip is not reachable from Amaranth. A PIO-compatible state machine in Amaranth for an FPGA is a separate idea, not built here.
- The RP2354 is an RP2350 with 2 MB of flash in the package (owner, 2026-10-17); the DSL treats it as an RP2350 (PIO version 1).

## 2. API (builder/src/pioweave.builder/program.py)

    p = Program("uart_tx", chip="rp2350", side_set=1, side_opt=True)
    p.pull(side=1, delay=7)
    p.set(X, 7, side=0, delay=7)
    p.label("bitloop")
    p.out(PINS, 1)
    p.jmp("bitloop", when=X_DEC, delay=6)
    p.pio_text()      # pioasm source
    p.words()         # the 16-bit words, encoded by the DSL itself

| Area | API |
|---|---|
| program | `Program(name, chip, side_set, side_opt, side_pindirs, origin, emit_version)`; chip is rp2040, rp2350 or rp2354 |
| structure | `label(name)` (names the next instruction), `wrap_target()`, `wrap()`, `with p.wrapped():` |
| instructions | `nop jmp wait_gpio wait_pin wait_irq wait_jmppin in_ out push pull mov mov_put mov_get irq set word`; each takes `side=` and `delay=` |
| operands | constants `PINS X Y NULL ISR OSR PC EXEC PINDIRS STATUS`; JMP conditions `ALWAYS NOT_X X_DEC NOT_Y Y_DEC X_NE_Y PIN NOT_OSRE`; MOV operations `NONE INVERT REVERSE`; IRQ modes `SET WAIT CLEAR` |
| directives | `define`, `clock_divider`, `fifo`, `mov_status`, `config_in`, `config_out`, `config_set` (only before the first instruction, as pioasm requires) |
| output | `pio_text()`, `words()`, `wrap_range`, `write_pio(programs, path)` |

Errors are `PioError`, raised when the program is built: operand not allowed for the instruction, value out of range, delay larger than the bits left after the side-set (5 minus the side-set bits minus the enable bit), a missing `side=` under a mandatory side-set, undefined or duplicate labels, more than 32 instructions, and every restriction that depends on the chip.

## 3. Rules taken from the datasheets and checked against pioasm

Facts (RP2040 datasheet chapter 3, RP2350 datasheet chapter 11, read 2026-10-17): instruction encodings (tables "PIO instruction encoding"), the 5-bit delay / side-set field, the pioasm directive and instruction syntax. Only on PIO version 1: `wait jmppin`, `mov pindirs`, `mov rxfifo[] / mov osr, rxfifo[]`, `irq prev / next`, `.fifo txput / txget / putget`, `.mov_status irq`, `.in` with a count other than 32.

Rules that the datasheets do not state and that were found by comparing with the real pioasm (SDK checkout of 2026-10-17; the comparison is test_every_form_encodes_like_pioasm):

- `push` needs `.fifo` txrx (default) or rx; `mov rxfifo[], isr` needs txput or putget; `mov osr, rxfifo[]` needs txget or putget.
- `.in ... auto` (autopush) is incompatible with txput, txget and putget.
- A numeric JMP target must lie inside the program ("beyond the end of the program").
- `.word` cannot be assembled in a program with a mandatory side-set.
- JMP targets in the assembled output are program-relative even with `.origin`; the SDK adds the load offset when it loads the program. (bitweave's simulator does the same.)
- The RX FIFO register index is 0 to 3 here (datasheet); pioasm accepts up to 7. The DSL is stricter.

## 4. Two independent encoders

`words()` encodes from the datasheet tables; pioasm encodes from its own source. The test assembles the text the DSL prints and compares word for word: 10,681 instruction words in 351 programs (RP2040 and RP2350 forms, every operand combination of every instruction, five side-set configurations, delay and side values at their limits) are equal. Version-1-only forms are also refused by pioasm for version 0, as the DSL does. The three example programs equal the hand-written .pio files (pioweave/tests/pio_ref/) in words, wrap range, side-set and version.

## 5. Use with bitweave

    [pio.uart_tx_dsl]
    generator = ["{root}/.tools/bin/python", "-m", "pioweave.builder", "emit", "uart_tx", "{outdir}"]
    sources = ["{root}/python"]      # the generator is skipped while these files are unchanged
    chip = "rp2350"
    [[pio.uart_tx_dsl.sim]] ...        # simulation cases; see bitweave guide pio-and-raspberry-pi

`bitweave pio uart_tx_dsl` runs the generator, assembles, reports instruction memory use and runs the cases; `bitweave check` gates it. Simulation cases for the three examples are in bitweave.toml.

## 6. Not verified

- Nothing has run on a chip (running one on a board is open: the owner has RP2040, RP2350 and RP2354 boards, models [confirm]).
- Only one pioasm version was used (SDK checkout of 2026-10-17, built locally; its version string is local). Other SDK versions may differ in the rules of section 3 [verify].
- The simulator and the DSL's encoder share nothing, but both were written by the same author from the same datasheets; a misreading common to both would not be caught by the comparison with pioasm (the encoder) or with rp2040-pio-emulator (the simulator) unless that tool also differs.
