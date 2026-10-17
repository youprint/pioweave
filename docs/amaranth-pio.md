# PIO programs written in Amaranth: design note

Code: python/platform_rp.py, python/pio_compile.py, python/pio_project.py, python/pio_designs.py and python/bitweave_pio_amaranth.py. Tests: python/tests/test_pio_compile.py , test_pio_refusals.py, test_pio_project.py, with python/tests/pio_cosim.py.

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

## 3. What is refused, with the Amaranth source line

More than one operation per state (it would need two cycles), compound tests, `Elif` chains, tests on other signals or on constants other than 0, `x == y`, a jump on a low pin, a pin or register assignment that is not a constant 0..31 / the other register / its complement / itself minus 1, assignments to a part of a signal, `m.Switch` on multi-bit values, a decrement in only one branch, a decrement without the test that needs it, registers or pins with a non-zero reset value (PIO registers reset to 0), two `set` pin signals or two side-set signals (merge them into one multi-bit resource or use `platform.pio_role(pin, "set" | "side")`), pin groups that are not consecutive GPIOs, other clock domains, more than 32 instructions. Pin roles are inferred (a pin assigned alone is a `set` pin; a pin assigned together with other work is a side-set pin; one used both ways is in both groups) unless forced.

Not built yet: the data path (`pull`, `out`, `in`, `push`, the FIFOs, OSR and ISR), `irq`, `wait irq`, `wait pin` relative to IN_BASE, several state machines, `.wrap` placement other than at the last instruction, input pins wider than one bit.

### The 32-GPIO window of the 48-GPIO parts

A PIO block sees 32 GPIOs at a time; GPIOBASE selects GPIO 0 to 31 or GPIO 16 to 47 (RP2350 datasheet, register GPIOBASE: only 0 and 16). On the B variants the compiler takes the pins the program uses (the `set` and side-set groups, the JMP pin, the `wait gpio` pins) and picks the smallest base whose window holds all of them; pins that do not fit one window (for example GPIO 5 and GPIO 40) are refused. What the generated files contain:

- the `.pio` text keeps real GPIO numbers in `wait gpio N` (pioasm accepts 0 to 47 on version 1 and flags the word; the SDK flips bit 4 when it loads the program with GPIO base 16, `pio.c`, `add_program_at_offset`);
- `main.c` calls `pio_set_gpio_base(pio, 16)` before claiming a state machine and loading the program, and passes real GPIO numbers to `sm_config_set_*_pins`, `pio_gpio_init` and `pio_sm_set_consecutive_pindirs` (the SDK subtracts the base itself; `pio.h`, "On RP2350B ...");
- `bitweave.toml` carries `gpio_base = 16` and the PIO's own pin numbers (GPIO minus the base) in the case, because bitweave's simulator models the hardware view;
- the Pico SDK decides `PICO_RP2350A` from the board header: for a B variant the generated `bitweave.toml` needs a `board` whose header sets `PICO_RP2350A 0` (an example in the SDK: `waveshare_core2350b`) [confirm which board the owner has].

## 4. Cycle alignment of the two simulators

Amaranth sim: the FSM state in clock cycle t does what the instruction executed in cycle t does. Outputs are registers, so the value sampled after the t-th clock edge is the effect of cycle t, which is the pad level the bitweave PIO simulator reports for cycle t. Inputs set before the edge of cycle t are what the state (instruction) of cycle t reads. The 2-flop input synchroniser of the chip is off in the model (`sync_cycles = 0`), so a real pin sampled by a program reaches it two cycles later than in the simulation [verify on a chip].

## 5. Project kinds

- `pio`: `bitweave.toml` holds `[pio.NAME]` and a `[[pio.NAME.sim]]` case configured like the project (set/side-set/JMP pin bases, pin directions) with `expect` waveforms taken from the Amaranth simulation of the same design (`factory=` gives the fresh design/platform pair the simulation needs, because a design requests its pins from the platform when it is elaborated). `bitweave pio NAME` then checks the compiled program against the design it came from.
- `pico-c`: `main.c` claims a state machine, loads the program from the pioasm header, sets the set/side-set pin groups and their directions, the input pins, the JMP pin and the clock divider (`sm_clock_hz`), then starts it. Built with the real Pico SDK through `bitweave firmware` in the tests (PIO_BUILD_FIRMWARE=1).
- `micropython`: `pioasm -o python` writes the program; the decorator gets the initial pin directions the design needs (`set_init`, `sideset_init`) and `main.py` creates the `rp2.StateMachine`.

## 6. Verification

- Example designs (six) and random designs of the subset (an FSM of 2 to 12 states with random kinds, layouts, side-set, pins and input waveforms): the Amaranth simulation and the bitweave simulator running the compiled program agree pin by pin and cycle by cycle (tests/test_pio_compile.py): 300 random designs of the first generator and 150 of the final one (which also draws RP2350A, RP2350B and RP2354B and puts pins on GPIO 20 to 45 so that the GPIO base is 16) all agree; the default run uses 15, `PIO_COSIM_N` sets more.
- 36 refusal cases, each naming its reason (tests/test_pio_refusals.py).
- Project writers: files, content, the generated cases pass in bitweave, and two generated C projects build to a UF2 with the real Pico SDK (tests/test_pio_project.py).

## 7. Not verified

Nothing has run on a chip. The MicroPython glue follows the rp2 documentation from memory of its API names and was not run on a device [verify]; pioasm's `-o python` output has no `sideset_opt`-style declaration, so whether MicroPython accepts the optional side-set exactly as written is [verify]. The simulator and the compiler were written by the same author from the same datasheets; the co-simulation catches mismatches between the Amaranth semantics and the PIO model, not a misreading of the datasheets common to the model and the compiler (the model was also cross-checked against rp2040-pio-emulator, see bitweave `guide pio-and-raspberry-pi`). Only one Amaranth version (0.5.10, pinned) was used: the compiler reads Fragment.statements, which is not a stable public API [verify on other versions].
