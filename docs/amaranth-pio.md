# PIO programs written in Amaranth: design note

Code: python/rp/src/amaranth_rp/ (platform.py, compile.py, data.py, project.py, designs.py). Tests: python/rp/tests/test_compile.py, test_refusals.py, test_project.py, with cosim.py.

## 1. Idea

Amaranth has one platform module per target (`amaranth.build.Platform`; the vendor modules are in `amaranth.vendor`). This adds one for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 (the RP2354 is an RP2350 with 2 MB of flash in the package, owner 2026-10-17: PIO version 1). The variants `rp2350a`, `rp2350b`, `rp2354a`, `rp2354b` are the same PIO and differ only in the pinout (owner, 2026-10-17; RP2350 datasheet Table 1: A = QFN-60 with 30 GPIOs, B = QFN-80 with 48 GPIOs); `rp2350` and `rp2354` without a letter keep the 30-GPIO pinout. A design is an ordinary Amaranth Elaboratable. Its build products are a PIO project of the kind the user chooses:

| `project=` | files |
|---|---|
| `"pio"` | `NAME.pio`, `bitweave.toml` with a simulation case, `README.md` |
| `"pico-c"` | the same, plus `CMakeLists.txt` and `main.c` of a Pico SDK project |
| `"micropython"` | the same, plus `NAME_pio.py` (written by `pioasm -o python`) and `main.py` |

    platform = RP2040PioPlatform(sm_clock_hz=1_000_000)
    platform.build(SquareWave(), "square", "build/square", project="pico-c", factory=lambda: (SquareWave(), RP2040PioPlatform()), cycles=24)

A PIO state machine is an FSM whose state is the program counter and that executes one instruction per clock cycle. A design written as one Amaranth FSM in which every state does what one PIO instruction does is therefore cycle-equivalent to the program, and the Amaranth simulation of the design is the reference. The compiler maps one FSM state to one instruction and refuses everything that would change the cycle behaviour.

## 2. What is accepted (python/pio_compile.py reads the Amaranth 0.5.10 IR, Fragment.statements)

One `m.FSM()` in the `sync` domain, no submodules, no logic outside the FSM, no combinational logic. States are placed in definition order (Amaranth numbers FSM states by first mention, but the cases of the generated Switch follow the source order; the compiler uses the source order, the first defined state is PIO address 0).

| In the state | PIO |
|---|---|
| `pin.o.eq(C)` on a requested output (alone) | `set pins, C` (set group: consecutive GPIOs, at most 5 bits) |
| `pin.o.eq(C)` together with another operation, a test, a jump or a wait | side-set (at most 4 bits with the enable bit; optional side-set, so a state that does not assign it leaves the pins alone, as in Amaranth) |
| `x.eq(C)`, C in 0..31 (`x = platform.scratch("x")`, 32 bits) | `set x, C` |
| `x.eq(y)`, `x.eq(~y)`, `x.eq(~x)` | `mov x, y`, `mov x, !y`, `mov x, !x` |
| `x.eq(x - 1)` with `m.If(x != 0)` selecting the jump | `jmp x-- LABEL` (decrements in both branches, like the instruction) |
| `x.eq(x - 1)` and nothing else, going to the next state | `jmp x-- NEXT` (decrement without branching) |
| `m.If(x == 0)`, `y == 0`, `x != y` selecting a jump (the other branch is the next state) | `jmp !x`, `jmp !y`, `jmp x!=y` |
| `m.If(pin.i)` selecting a jump | `jmp pin LABEL` (one JMP pin per program) |
| a state that stays until `m.If(pin.i)` / `m.If(~pin.i)` lets it go to the next state | `wait 1|0 gpio N` |
| `m.next = OTHER` and nothing but side-set | `jmp OTHER` |
| states without effect after an instruction (nothing jumps to them) | folded into the delay `[n]` of that instruction, up to the bits left after the side-set; more become `nop` |
| the last state going to a state other than the first, with a `set`/`mov` operation | `.wrap_target` at that state |

Jumps and waits take their layout from the next-state rule: PIO can jump on `!x`, `x--`, `!y`, `y--`, `x!=y` and on a pin that is high, so the compiler picks, of the two branches, the one that is not the next state as the jump and negates the test if needed. A branch state cannot also carry a delay (the taken path would pay it too), so only `set`/`mov`/`nop`/`wait` states take folded delays.

## 2a. The data path: FIFOs, OSR and ISR (python/rp/src/amaranth_rp/data.py)

`sh = platform.shifter(m, out_shift="right", in_shift="right")` (once per design, inside `elaborate`) creates the OSR, ISR, their bit counters, and the model of the two 4-word FIFOs (a submodule, `FifoModel`). Its
methods add to the current FSM state exactly the register updates of one PIO instruction, so the Amaranth simulation of the design stays the reference:

| In the state | PIO |
|---|---|
| `with sh.pull(m): m.next = ...` | `pull block`: the state stays while the TX FIFO is empty (the FIFO flag is the test, so the same rules as `wait` apply: continue with the next state, side-set assigned before the `with`) |
| `with sh.pull(m, block=False): ...` | `pull noblock`: X is loaded into the OSR when the FIFO is empty |
| `with sh.push(m): m.next = ...` / `sh.push(m, block=False)` | `push block` / `push noblock` (the word is dropped and the ISR cleared when the RX FIFO is full) |
| `sh.out(m, pin)` / `sh.out(m, "x" \| "y" \| "null", n)` | `out pins, N` (N = width of the pin group unless given) / `out x, n` / `out y, n` / `out null, n` |
| `sh.in_(m, pin)` / `sh.in_(m, "x" \| "y" \| "null", n)` | `in pins, N` / `in x, n` / `in y, n` / `in null, n` |

`out_shift` and `in_shift` are the shift directions; they go to the generated C (`sm_config_set_out_shift` / `sm_config_set_in_shift`), MicroPython (`out_shiftdir`, `in_shiftdir`) and bitweave case (`out_shift`, `in_shift`).
`out pins` makes the pin group the OUT group (`out_base`, `out_count`), `in pins` the IN group (`in_base`); each program has one of each. One of them can also be a side-set or `set` pin (the UART transmitter
drives its pin by side-set and by `out pins`). `n` is a constant from 1 to 32 (an `in pins` count cannot exceed the width of the pin group).

How it is built, because it explains the limits. The design needs no combinational logic: a word taken from the TX FIFO or stored in the RX FIFO is recorded by a toggle register of the design (`tx_pop`, `rx_push`,
with `rx_data` = the ISR at the push), and the model applies it one clock edge later while its flags and its head already account for the pending word, so the next instruction sees what the PIO shows it. The compiler
reads each helper through two marker registers (`op`, `arg`), checks that the statements of a state on the shifter registers (OSR, ISR, their counters, the toggles) are exactly those of the one operation the
marker names, and compiles the state to that instruction. A state with two operations, an operation together with a pin or register operation or a jump (apart from side-set), an operation in a state with a test,
a blocking pull / push that does not continue with the next state, and hand-written assignments to the shifter registers are refused with the source line.

The reference simulation (`project.amaranth_run`) takes the words in the TX FIFO at cycle 0 (`tx`), words the host writes later (`tx_feed`, `(cycle, word)`: the instruction of that cycle sees them, as in bitweave's
simulator) and `drain` (the host empties the RX FIFO every cycle, the default, or not), and returns the pin waveforms, the words pushed with their cycles, and what is left in the FIFOs. The generated bitweave case carries
`tx`, `tx_feed`, `rx_drain` and `expect_rx` (words and order), the generated C project writes the words with `pio_sm_put_blocking` and prints the received words (`printf`, USB stdio), and the MicroPython project uses
`sm.put` / `sm.get` [verify on a device]. The examples are `uart_tx` (the SDK's transmitter, 2 cycles per bit), `uart_rx` (receiver, 8 cycles per bit, `in pins`, `push`), `byte_swap` (`out x/y`, `in null/y/x`, left shifts) and
`noblock_echo` (`pull noblock`, `push noblock`).

## 3. What is refused, with the Amaranth source line

More than one operation per state (it would need two cycles), compound tests, `Elif` chains, tests on other signals or on constants other than 0, `x == y`, a jump on a low pin, a pin or register assignment that is not a constant 0..31 / the other register / its complement / itself minus 1, assignments to a part of a signal, `m.Switch` on multi-bit values, a decrement in only one branch, a decrement without the test that needs it, registers or pins with a non-zero reset value (PIO registers reset to 0), two `set` pin signals or two side-set signals (merge them into one multi-bit resource or use `platform.pio_role(pin, "set" | "side")`), pin groups that are not consecutive GPIOs, other clock domains, more than 32 instructions. Pin roles are inferred (a pin assigned alone is a `set` pin; a pin assigned together with other work is a side-set pin; one used both ways is in both groups) unless forced.

The data path (FIFOs, OSR, ISR) is described in section 2a. Not built yet: autopull / autopush and their thresholds, `pull ifempty` / `push iffull`, `out pindirs` / `pc` / `isr` / `exec`, `in isr` / `osr`, `mov` with the OSR, the ISR or pins, the status source, joining the FIFOs, `irq`, `wait irq`, `wait pin` relative to IN_BASE, several state machines, `.wrap` placement other than at the last instruction.

### The 32-GPIO window of the 48-GPIO parts

A PIO block sees 32 GPIOs at a time; GPIOBASE selects GPIO 0 to 31 or GPIO 16 to 47 (RP2350 datasheet, register GPIOBASE: only 0 and 16). On the B variants the compiler takes the pins the program uses (the `set` and side-set groups, the JMP pin, the `wait gpio` pins) and picks the smallest base whose window holds all of them; pins that do not fit one window (for example GPIO 5 and GPIO 40) are refused. What the generated files contain:

- the `.pio` text keeps real GPIO numbers in `wait gpio N` (pioasm accepts 0 to 47 on version 1 and flags the word; the SDK flips bit 4 when it loads the program with GPIO base 16, `pio.c`, `add_program_at_offset`);
- `main.c` calls `pio_set_gpio_base(pio, 16)` before claiming a state machine and loading the program, and passes real GPIO numbers to `sm_config_set_*_pins`, `pio_gpio_init` and `pio_sm_set_consecutive_pindirs` (the SDK subtracts the base itself; `pio.h`, "On RP2350B ...");
- `bitweave.toml` carries `gpio_base = 16` and the PIO's own pin numbers (GPIO minus the base) in the case, because bitweave's simulator models the hardware view;
- the Pico SDK decides `PICO_RP2350A` from the board header: for a B variant the generated `bitweave.toml` needs a `board` whose header sets `PICO_RP2350A 0` (SDK examples with boards definitions in amaranth-rp-boards: `weact_studio_rp2350b_core`, `waveshare_core2350b`; the owner supplied the WeAct Studio RP2350B schematic and the Waveshare Core2350B page) (owner, 2026-10-17: owns both the WeAct Studio RP2350B core and the Waveshare Core2350B).

## 4. Cycle alignment of the two simulators

Amaranth sim: the FSM state in clock cycle t does what the instruction executed in cycle t does. Outputs are registers, so the value sampled after the t-th clock edge is the effect of cycle t, which is the pad level the bitweave PIO simulator reports for cycle t. Inputs set before the edge of cycle t are what the state (instruction) of cycle t reads. The 2-flop input synchroniser of the chip is off in the model (`sync_cycles = 0`), so a real pin sampled by a program reaches it two cycles later than in the simulation [verify on a chip].

## 5. Project kinds

- `pio`: `bitweave.toml` holds `[pio.NAME]` and a `[[pio.NAME.sim]]` case configured like the project (set/side-set/JMP pin bases, pin directions) with `expect` waveforms taken from the Amaranth simulation of the same design (`factory=` gives the fresh design/platform pair the simulation needs, because a design requests its pins from the platform when it is elaborated). `bitweave pio NAME` then checks the compiled program against the design it came from.
- `pico-c`: `main.c` claims a state machine, loads the program from the pioasm header, sets the set/side-set pin groups and their directions, the input pins, the JMP pin and the clock divider (`sm_clock_hz`), then starts it. Built with the real Pico SDK through `bitweave firmware` in the tests (PIO_BUILD_FIRMWARE=1).
- `micropython`: `pioasm -o python` writes the program; the decorator gets the initial pin directions the design needs (`set_init`, `sideset_init`) and `main.py` creates the `rp2.StateMachine`.

## 6. Verification

- Example designs (ten, four of them with the data path) and random designs of the subset (an FSM of 2 to 12 states with random kinds, layouts, side-set, pins and input waveforms): the Amaranth simulation and the bitweave simulator running the compiled program agree pin by pin and cycle by cycle (tests/test_pio_compile.py): 300 random designs of the first generator and 150 of the final one (which also draws RP2350A, RP2350B and RP2354B and puts pins on GPIO 20 to 45 so that the GPIO base is 16) all agree; the default run uses 15, `PIO_COSIM_N` sets more.
- Data path: 250 random designs (PULL / PUSH blocking and non-blocking, OUT to pins / X / Y / null, IN from pins / X / Y / null with random counts, both shift directions, TX words at cycle 0 and written later, draining and non-draining host, 1 to 4 output pins, 1 to 3 input pins, optional side-set, RP2040, RP2350, RP2350B and RP2354) agree with the bitweave simulator on pin waveforms, the words pushed with their cycles and the FIFO contents left at the end (one seed of the 250 first run was a generator error, fixed); the data-path refusals and the helper-misuse errors are tested too.
- Refusals: 25 tests in all (14 of them for the data path), each naming its reason (python/rp/tests/test_refusals.py; several tests contain more than one refused design).
- Project writers: files, content, the generated cases pass in bitweave, and two generated C projects build to a UF2 with the real Pico SDK (tests/test_pio_project.py).

## 7. Not verified

Nothing has run on a chip. The MicroPython glue follows the rp2 documentation from memory of its API names and was not run on a device [verify]; pioasm's `-o python` output has no `sideset_opt`-style declaration, so whether MicroPython accepts the optional side-set exactly as written is [verify]. The simulator and the compiler were written by the same author from the same datasheets; the co-simulation catches mismatches between the Amaranth semantics and the PIO model, not a misreading of the datasheets common to the model and the compiler (the model was also cross-checked against rp2040-pio-emulator, see bitweave `guide pio-and-raspberry-pi`). Only one Amaranth version (0.5.10, pinned) was used: the compiler reads Fragment.statements, which is not a stable public API [verify on other versions].
