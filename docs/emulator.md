# The PIO emulator (`pioweave.emulator`)

A software model of the PIO blocks of the RP2040, RP2350 and RP2354, written in Python and needing no Amaranth. It runs a program cycle by cycle the way the datasheets describe it, so you can
see the pins, the registers and the FIFOs without a chip, and so that pioweave can check a design against a second, independent implementation.

```python
from pioweave.builder import Program, PINS, X, X_DEC
from pioweave.emulator import run_case

p = Program("uart_tx", chip="rp2040", side_set=1, side_opt=True)
p.pull(side=1, delay=7)
p.set(X, 7, side=0, delay=7)
p.label("bitloop")
p.out(PINS, 1)
p.jmp("bitloop", when=X_DEC, delay=6)

r = run_case(p, 120, tx=[0x55], side_base=0, out_base=0, out_count=1, dirs=1)
print(r.wave(0))            # the level of pin 0 after every cycle
```

## What is modelled

One `PioBlock` has a shared 32-word instruction memory, four state machines, the eight IRQ flags and the pins of the block's 32-pin window.

| Part | Behaviour |
|---|---|
| instructions | all nine, with every source and destination: `JMP` (all eight conditions), `WAIT` (GPIO, pin, IRQ, and on version 1 JMPPIN), `IN`, `OUT` (including `PC`, `ISR`, `EXEC`, `PINDIRS`), `PUSH` / `PULL` (`iffull`, `ifempty`, block and noblock), `MOV` (inversion, bit reversal, all sources and destinations, `STATUS`), `IRQ` (set, clear, wait, relative), `SET` |
| timing | one instruction per state machine clock cycle; delays; stalls (a stalled instruction retries every cycle); side-set applied in the first cycle of the instruction, after the instruction's own pin write (so side-set wins); `OUT EXEC` / `MOV EXEC` / forced instructions run in the next cycle and their delay is the executed instruction's own |
| shifters | OSR and ISR with the shift directions, the shift counters (OSR starts empty), autopush and autopull with thresholds, the counter saturating at 32 |
| FIFOs | 4 words each way; joined (8 words one way) with `fifo_join="tx"` or `"rx"`; on version 1 the RX FIFO as four registers with `MOV rxfifo[]` (`rx_put`, `rx_get`, `rx_put_get`) |
| IRQ flags | eight flags per block, set and clear from the instructions; a flag that is set and cleared in the same cycle ends up set; `IRQ PREV` / `NEXT` between linked blocks (`PioBlock.neighbours`) on version 1 |
| pins | output levels and directions per pin; several state machines writing the same pin: the highest-numbered one wins [verify]; inputs from `set_input()` / waveforms, through the optional two-flop input synchroniser (`sync_cycles`, default 2 [verify]; 0 bypasses it) |
| clock divider | `clkdiv` as a fractional divider (integer and 1/256 steps); the state machine advances in 1 / `clkdiv` of the system clock cycles [verify the exact jitter pattern of the hardware] |
| PIO version 1 | `WAIT JMPPIN`, `MOV PINDIRS`, `MOV` to and from the RX FIFO registers, `IN` with a count (`in_count`), `STATUS` from an IRQ flag, the GPIO base (0 or 16) for the 48-GPIO parts |
| errors | reserved encodings, version-1 instructions on version 0, undefined combinations (for example `PUSH` with an RX FIFO register join) and executing empty instruction memory raise `EmulationError` |

Not modelled: DMA and its request signals, the CPU-side interrupt lines (the IRQ flags are visible, the NVIC is not), GPIO function selection and pad electrical behaviour (input hysteresis, slew, pulls),
the system clock itself (a cycle is a system clock cycle), and more than one clock domain.

## Using a block directly

```python
from pioweave.builder import Program, PINS
from pioweave.emulator import PioBlock

block = PioBlock(version=0)                     # 0: RP2040, 1: RP2350 and RP2354; gpio_base=16 for the 48-GPIO parts
prog = Program("blink")
prog.set(PINS, 1, delay=1)
prog.set(PINS, 0, delay=1)
loaded = block.load(prog)                       # JMP targets are relocated by the load offset, as the SDK does
sm = block.sm[0]
sm.configure(program=loaded, set_base=0, set_count=1)      # wrap range and side-set come from the program
block.dirs = 1                                  # pin 0 is an output
sm.enable()                                     # a state machine runs only when enabled
block.run(8)
```

- `block.load(program)` takes a `pioweave.builder.Program` or a list of 16-bit words (then give `side=(bits, optional, pindirs)`; `bits` counts the enable bit of an optional side-set, as the hardware register does).
  It returns a `Loaded` (offset, length, wrap range, side-set). `block.unload(loaded)` frees the memory.
- `sm.configure(program=loaded, **settings)` sets `in_base`, `in_count`, `out_base`, `out_count`, `set_base`, `set_count`, `side_base`, `jmp_pin`, `in_shift` / `out_shift` (`"right"` or `"left"`), `autopush`,
  `autopull`, `push_thresh`, `pull_thresh`, `status_sel` (`"tx"`, `"rx"`, `"irq"`), `status_n`, `fifo_join`, `clkdiv`.
- Host side: `sm.put(word)` writes the TX FIFO (False and counted in `tx_dropped` when full), `sm.get()` reads the RX FIFO (`None` when empty), `sm.force(word)` executes an instruction in the next cycle,
  `sm.set_pc(address)`, `sm.enable()` / `sm.disable()`, `sm.reset()`.
- Pins: `block.set_input(pin, level)` or `block.ext = bits`; `block.pads()` is what is on the pads (the block's output where it drives the pin, the outside world elsewhere); `block.out_level`, `block.dirs`.
- Several blocks: `a.neighbours[1] = b; b.neighbours[-1] = a` link them for `IRQ NEXT` / `PREV`.
- `block.recording = True` keeps a trace, one record per cycle with the pads, directions, IRQ flags and a snapshot of every state machine (`block.trace`).

## Running a test case

`run_case(program, cycles, version=0, stim=None, tx=(), tx_feed=(), drain=True, sync_cycles=0, gpio_base=0, record=False, on_error="raise", **settings)` loads the program into a fresh block, configures state
machine 0 and runs it. It uses the conventions of bitweave's PIO simulator and of pioweave's Amaranth reference simulation: input waveforms are strings of `0` and `1` per cycle (the last character repeats),
`tx_feed` words are written before the cycle's instruction runs, the host drains the RX FIFO after every cycle (`drain=True`), and `Result.pads[t]` is what is on the pads after cycle `t`. The settings include
`x`, `y`, `dirs` and `start_pc`. `Result.wave(pin)` gives a pin as a string; `Result.rx` is `[(cycle, word)]`; `Result.error` and `error_cycle` are set with `on_error="stop"`.

`run_compiled(compiled, cycles, stim, tx, tx_feed, drain)` runs a program compiled from an Amaranth design with the configuration the compiler derived (real GPIO numbers for the stimulus). `to_vcd(trace, pins)`
writes a VCD of recorded pins.

## From the command line

    python -m pioweave emulate uart_tx [--chip CHIP] [--cycles N] [--stim GPIO=0101...] [--tx W,W] [--tx-feed CYCLE=WORD] [--no-drain] [--vcd FILE]

compiles an example design (or `module:Class`), runs it in the emulator and prints the program, the output pin waveforms, the words received and, with `--vcd`, writes a waveform file.

## How it was checked

| Check | What | Needs |
|---|---|---|
| `tests/test_emulator.py` | 41 directed tests of every instruction and feature, with expected values worked out from the datasheet semantics | nothing |
| `tests/test_emulator_amaranth.py` | every example design and random designs (control flow and data path, all chips) run in the emulator and in the Amaranth simulator must agree pin by pin, cycle by cycle, on the words pushed and the FIFO contents | nothing |
| `tests/test_emulator_differential.py` | random programs (every instruction form, both PIO versions, random configurations, input waveforms and FIFO traffic) must agree with bitweave's PIO simulator in every cycle on the instruction executed, all registers, the pads, the directions, the IRQ flags, the words received and the FIFO counts; programs that do something undefined must stop in the same cycle | the `bitweave` binary and `pioasm` |

The emulator and bitweave's simulator were written separately from the same datasheets, so the comparison catches slips in either, not a misreading common to both. **Nothing has run on a chip**: items
marked [verify] (the priority between state machines writing one pin, the default of the input synchroniser, the clock divider pattern, the reset values of the shift directions) come from the datasheets as
recalled and need a check against the documents or a chip. A multi-state-machine program is verified only by the directed tests, not by the differential test (bitweave's simulator runs one state machine).
