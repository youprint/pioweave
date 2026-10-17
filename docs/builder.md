# The Python builder (`pioweave.builder`)

`pioweave.builder.Program` writes a PIO program instruction by instruction. It needs no Amaranth. Errors are raised as `PioError` when the program is built, not when the chip misbehaves, and the
instruction words come from an encoder written from the RP2040 (chapter 3) and RP2350 (chapter 11) datasheets, independent of `pioasm`.

The API reference of this part, generated from the docstrings, is in {doc}`reference/builder`.

```python
from pioweave.builder import Program, PINS, X, X_DEC

p = Program("uart_tx", chip="rp2350", side_set=1, side_opt=True)
p.pull(side=1, delay=7)
p.set(X, 7, side=0, delay=7)
p.label("bitloop")
p.out(PINS, 1)
p.jmp("bitloop", when=X_DEC, delay=6)
print(p.pio_text())      # pioasm source
print(p.words())         # the 16-bit instruction words
```

## API

| Area | API |
|---|---|
| program | `Program(name, chip="rp2040", side_set=0, side_opt=False, side_pindirs=False, origin=None, emit_version=True)`; `chip` is `rp2040`, `rp2350`, `rp2354` or the variants `rp2350a`, `rp2350b`, `rp2354a`, `rp2354b` |
| structure | `label(name)` names the next instruction; `wrap_target()`, `wrap()`, `with p.wrapped():` |
| instructions | `nop`, `jmp`, `wait_gpio`, `wait_pin`, `wait_irq`, `wait_jmppin`, `in_`, `out`, `push`, `pull`, `mov`, `mov_put`, `mov_get`, `irq`, `set`, `word`; each takes `side=` and `delay=` |
| operands | `PINS X Y NULL ISR OSR PC EXEC PINDIRS STATUS`; JMP conditions `ALWAYS NOT_X X_DEC NOT_Y Y_DEC X_NE_Y PIN NOT_OSRE`; MOV operations `NONE INVERT REVERSE`; IRQ modes `SET WAIT CLEAR` |
| directives | `define`, `clock_divider`, `fifo`, `mov_status`, `config_in`, `config_out`, `config_set` (only before the first instruction, as `pioasm` requires) |
| output | `pio_text()`, `words()`, `wrap_range`, `write_pio(programs, path)` for several programs in one file |

The RP2354 is an RP2350 with 2 MB of flash in the package, so it is PIO version 1 like the RP2350; the A (QFN-60, 30 GPIOs) and B (QFN-80, 48 GPIOs) variants have the same PIO and differ only in the pinout.

## What `PioError` covers

An operand not allowed for the instruction, a value out of range, a delay larger than the bits left after the side-set (5 minus the side-set bits minus the enable bit), a missing `side=` under a mandatory
side-set, undefined or duplicate labels, more than 32 instructions, directives after the first instruction, and every restriction that depends on the chip (PIO version 1 only: `wait jmppin`, `mov pindirs`,
`mov rxfifo[]`, `irq prev/next`, `.fifo txput/txget/putget`, `.mov_status irq`, `.in` with a count other than 32).

Some rules are not in the datasheets and were found by comparing with the real `pioasm` (SDK checkout of 2026-10-17): `push` needs `.fifo` txrx or rx; `mov rxfifo[], isr` needs txput or putget;
`.in ... auto` is incompatible with txput, txget and putget; a numeric JMP target must lie inside the program; `.word` cannot be assembled in a program with a mandatory side-set. JMP targets in the
assembled output are program-relative even with `.origin`: the SDK adds the load offset when it loads the program.

## Two independent encoders

`words()` is encoded by `pioweave.builder` from the datasheet tables; `pioasm` encodes from its own source. The tests assemble the text the builder prints and compare word for word: 10,681 instruction words in
351 programs (RP2040 and RP2350 forms, every operand combination of every instruction, five side-set configurations, delay and side values at their limits) are equal.

## Examples and the command line

`pioweave.builder.examples.PROGRAMS` holds `square`, `uart_tx` and `passthru`; they equal the hand-written `.pio` files in `tests/pio_ref/` in words, wrap range, side-set and version.

    python -m pioweave.builder list
    python -m pioweave.builder emit uart_tx build/ [rp2040|rp2350|...]    # writes build/uart_tx.pio and prints EMIT {"path": ...}

The `EMIT` line is what a bitweave `generator = [...]` expects. A bitweave case for a builder program:

    [pio.square_py]
    generator = ["{root}/.bitweave-tools/bin/python", "-m", "pioweave.builder", "emit", "square", "{outdir}"]
    chip = "rp2040"
    [[pio.square_py.sim]]
    name = "four_cycle_period"
    cycles = 24
    dirs = 1
    set_base = 0
    set_count = 1
    [pio.square_py.sim.expect]
    0 = "110011001100110011001100"
