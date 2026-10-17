# pioweave

Write programs for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 in Python or in Amaranth, check them in simulation, and get a ready project.

PIO (programmable I/O) is a small state machine inside these chips, with its own instruction set of nine instructions, a 32-word program memory, two FIFOs and two shift registers. It is
normally programmed in `pioasm` assembly. pioweave offers two other ways to write the same programs:

- **In Python**, with one method per instruction (`pioweave.builder`). Errors are raised when you build the program, not when the chip misbehaves.
- **In Amaranth**, as an ordinary finite state machine (`pioweave`). One FSM state is one PIO instruction, so the Amaranth simulation of your design *is* the reference for the program that is
  generated. The compiler refuses anything that would change the cycle behaviour; it never approximates.

- **In an emulator**: `pioweave.emulator` is a Python model of the PIO blocks (four state machines, shared instruction memory, FIFOs, IRQ flags, pins) that runs any program cycle by cycle, without a chip.

From either, pioweave writes a `.pio` file, and on request a Pico SDK C project or a MicroPython project, together with a test case for [bitweave](https://github.com/youprint/bitweave) that checks the
compiled program against the simulation of your design.

```python
from amaranth.hdl import Module
from pioweave import PioDesign, RP2040PioPlatform, SimOptions

class UartTx(PioDesign):                       # 8N1 transmitter, two clock cycles per bit
    def elaborate(self, platform):
        m = Module()
        tx = platform.buffer(m, "o", "gpio", 0)
        sh = platform.shifter(m, out_shift="right")
        x = platform.scratch("x")
        with m.FSM():
            with m.State("PULL"):
                m.d.sync += tx.o.eq(1)         # idle level
                with sh.pull(m):               # pull block: stays while the TX FIFO is empty
                    m.next = "PULL_HOLD"
            with m.State("PULL_HOLD"):         # no effect: becomes the delay of the pull
                m.next = "START"
            with m.State("START"):
                m.d.sync += [tx.o.eq(0), x.eq(7)]
                m.next = "BIT"
            with m.State("BIT"):
                sh.out(m, tx)                  # out pins, 1
                m.next = "LOOP"
            with m.State("LOOP"):
                m.d.sync += x.eq(x - 1)
                with m.If(x != 0):
                    m.next = "BIT"
                with m.Else():
                    m.next = "PULL"
        return m

factory = lambda: (UartTx(), RP2040PioPlatform(sm_clock_hz=16_000_000))
design, platform = factory()
platform.build(design, "uart_tx", "build/uart_tx", project="pico-c", sim=SimOptions(factory=factory, cycles=60, tx=[0x55, 0xC3]))
```

This compiles to

```
.program uart_tx
.side_set 1 opt
    pull block side 1 [1]
    set x, 7 side 0
s_BIT:
    out pins, 1
    jmp x-- s_BIT
```

## Status

Early (version 0.1.0). Everything below was checked in simulation and against the real `pioasm`; **nothing has run on a chip yet**, and the generated MicroPython code has not run on a device.

- The builder's own encoder agrees with `pioasm` on 10,681 instruction words in 351 programs.
- 450 random Amaranth designs without and 340 with a data path agree with bitweave's PIO simulator pin by pin and cycle by cycle, as do all fifteen example designs; the data-path designs use, in half of the seeds, autopull and autopush with thresholds, `pull ifempty`, `push iffull`, `mov` with the OSR, ISR, pins and status, `in isr/osr`, `out isr`, `jmp !osre`, IRQ flags with host events (compared after every cycle), `wait pin` and joined FIFOs. The Python emulator agrees with the Amaranth simulation on 600 of those designs and with bitweave's simulator, cycle by cycle, on random programs.
- The whole test suite passes on Amaranth 0.5.10 (597 tests in the default run with the tools, 1 skipped, including real Pico SDK builds of the generated C projects of the single-machine designs and of the four example blocks; 60 random designs of each kind in an earlier run of 547 tests). Before the block layer was added its default run (367 tests, without the SDK builds) also passed on 0.5.1 and the 0.6 development version 0.6.0.dev140, and the quick suite on 0.5.0, 0.5.1 and 0.6.0.dev140 (0.5.2 to 0.5.9 were last run before the autopull, IRQ and `wait pin` support was added); the new block tests have not been run on the other versions.
- Several state machines in one design (`pioweave.block`, [docs/blocks.rst](docs/blocks.rst)): single-machine designs run as state machines 0 to 3 of one block with shared IRQ flags and pins, one program memory (at most 32 instructions in all) and one project (a `.pio` file with a program per machine, a `bitweave.toml` with one case per machine, a Pico SDK `main.c` that starts the machines together). The four example blocks and 1,000 random blocks of two to four machines agree between the Amaranth simulation of the whole block and the emulator, cycle by cycle; bitweave's simulator runs one machine per case, so the combined behaviour is checked against the emulator and the Amaranth simulation only.
- Runnable examples are in [examples/](examples/) (blink on a board LED, UART, two state machines in one block, a WS2812-style waveform, a builder square wave, a unittest of a program in the emulator); the tests run them. `python -m pioweave new NAME` creates a project from a template.
- The change log is [CHANGELOG.rst](CHANGELOG.rst); version 0.1.0 is not released yet and the licence is not decided (see [docs/releasing.rst](docs/releasing.rst)).
- Not built yet in the Amaranth front end: `out` / `mov` to `pindirs`, `pc` and `exec` (refused with the reason: the model has no pin direction state, and a computed jump or an executed data word is not an FSM state), `irq prev` / `next` and the RP2350 joins that turn the RX FIFO into registers (the Python builder has all instructions). Not built for blocks: the same program on several machines, `rel`, clock dividers that differ between machines.

## Install

pioweave is a normal Python package for an environment that has Amaranth:

    pip install pioweave                                          # once it is published
    pip install git+https://github.com/youprint/pioweave.git     # now, from the repository (private: needs access)
    pip install -e .                                              # from a checkout, for development

`pip` pulls in `amaranth>=0.5,<0.6`. Then `python -m pioweave --version` prints the installed version, `python -m pioweave doctor` checks the environment (Amaranth version, optional tools, a smoke test) and `python -m pioweave list`
shows the example designs. `python -m pioweave new --list` shows the project templates and `python -m pioweave new my-blink` creates a project with a design, a unit test that runs it in the emulator and a `bitweave.toml`.
With bitweave: `bitweave install pioweave --yes` installs Amaranth and pioweave into `.bitweave-tools` of a project (see [docs/getting-started.rst](docs/getting-started.rst)).

The package has four parts, as `amaranth` has `hdl`, `lib` and `vendor`: `pioweave` (platforms, compiler, project writers), `pioweave.builder` (the Python builder, needs no Amaranth), `pioweave.emulator`
(the PIO emulator, needs no Amaranth) and `pioweave.boards` (board definitions).

## Documentation

| | |
|---|---|
| [docs/getting-started.rst](docs/getting-started.rst) | install, first design, build, check it with bitweave |
| [docs/examples.rst](docs/examples.rst) | the runnable examples in `examples/` and the `new` project templates |
| [docs/designing-for-pio.rst](docs/designing-for-pio.rst) | the Amaranth style that compiles to PIO: what maps to which instruction, delays, side-set, pins, what is refused |
| [docs/data-path.rst](docs/data-path.rst) | FIFOs, OSR and ISR: `pull`, `push`, `out`, `in` |
| [docs/blocks.rst](docs/blocks.rst) | several state machines in one block: shared IRQ flags and pins, one program memory |
| [docs/builder.rst](docs/builder.rst) | the Python builder (`pioweave.builder`) |
| [docs/emulator.rst](docs/emulator.rst) | the PIO emulator: what is modelled, how to use it, how it was checked |
| [docs/hardware-testing.rst](docs/hardware-testing.rst) | a plan (nothing run) to check programs and the emulator on the Pico and the two RP2350B boards with the logic analysers |
| [docs/boards.rst](docs/boards.rst) | board definitions: Pico, WeAct Studio RP2350B, Waveshare Core2350B |
| [docs/projects.rst](docs/projects.rst) | `build()`, `SimOptions`, the project kinds, the command line, the generated C and MicroPython glue |
| [docs/compatibility.rst](docs/compatibility.rst) | supported Amaranth versions, the version gate, the test matrix |
| [docs/testing.rst](docs/testing.rst) | running the tests, co-simulation, environment variables |
| [docs/releasing.rst](docs/releasing.rst) | tagging, building the wheel and sdist, what to run before a release, the open licence decision |
| [docs/design-notes/](docs/design-notes/README.rst) | the design notes the code was written from |

## Layout

    pioweave/              the package (flat, as amaranth-soc and amaranth-boards): __init__ (lazy public names), compile, data, designs, platform, project, scaffold, doctor, _compat
    pioweave/builder/      the Python builder (needs no Amaranth)
    pioweave/emulator/     the PIO emulator (needs no Amaranth)
    pioweave/boards/       board definitions
    examples/              runnable examples (each folder has a README.rst), run by tests/test_examples.py
    tests/                 the tests (unittest); tests/__init__.py makes it a package
    docs/                  the documentation (Sphinx with reStructuredText, examples run by `sphinx-build -b doctest`: `sphinx-build docs docs/_build`)
    scripts/matrix.sh      the Amaranth version matrix
    CHANGELOG.rst          the change log
    .github/workflows/     CI

## Relation to bitweave

[bitweave](https://github.com/youprint/bitweave) is a separate Rust program (a flow runner and MCP server). It assembles, analyses and simulates PIO programs (`bitweave pio NAME`), builds Pico SDK
firmware, loads boards and drives logic analysers. pioweave is a Python library that writes the programs and projects; the two meet only through files: bitweave runs `python -m pioweave build ...`
as a generator, and pioweave's tests use the bitweave binary as the reference simulator.

## Licence

Proprietary, see [LICENSE](LICENSE) [confirm the wording]. No third-party code is copied; the instruction encodings come from the RP2040 and RP2350 datasheets.
