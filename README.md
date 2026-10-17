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
- 450 random Amaranth designs without and 250 with a data path agree with bitweave's PIO simulator pin by pin and cycle by cycle, as do all ten example designs; the Python emulator agrees with the Amaranth simulation on the same designs and with bitweave's simulator, cycle by cycle, on random programs.
- The whole test suite (166 tests, with real Pico SDK builds of generated C projects) passes on Amaranth 0.5.1, 0.5.10 and the 0.6 development version 0.6.0.dev140; a quick suite passes on every 0.5.x.
- Not built yet in the Amaranth front end: autopull and autopush, `mov` with the OSR or ISR, `irq`, joined FIFOs and several state machines (the Python builder has all instructions).

## Install

pioweave is a normal Python package for an environment that has Amaranth:

    pip install pioweave                                          # once it is published
    pip install git+https://github.com/youprint/pioweave.git     # now, from the repository (private: needs access)
    pip install -e .                                              # from a checkout, for development

`pip` pulls in `amaranth>=0.5,<0.6`. Then `python -m pioweave doctor` checks the environment (Amaranth version, optional tools, a smoke test) and `python -m pioweave list` shows the example designs.
With bitweave: `bitweave install pioweave --yes` installs Amaranth and pioweave into `.bitweave-tools` of a project (see [docs/getting-started.md](docs/getting-started.md)).

The package has four parts, as `amaranth` has `hdl`, `lib` and `vendor`: `pioweave` (platforms, compiler, project writers), `pioweave.builder` (the Python builder, needs no Amaranth), `pioweave.emulator`
(the PIO emulator, needs no Amaranth) and `pioweave.boards` (board definitions).

## Documentation

| | |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | install, first design, build, check it with bitweave |
| [docs/designing-for-pio.md](docs/designing-for-pio.md) | the Amaranth style that compiles to PIO: what maps to which instruction, delays, side-set, pins, what is refused |
| [docs/data-path.md](docs/data-path.md) | FIFOs, OSR and ISR: `pull`, `push`, `out`, `in` |
| [docs/builder.md](docs/builder.md) | the Python builder (`pioweave.builder`) |
| [docs/emulator.md](docs/emulator.md) | the PIO emulator: what is modelled, how to use it, how it was checked |
| [docs/boards.md](docs/boards.md) | board definitions: Pico, WeAct Studio RP2350B, Waveshare Core2350B |
| [docs/projects.md](docs/projects.md) | `build()`, `SimOptions`, the project kinds, the command line, the generated C and MicroPython glue |
| [docs/compatibility.md](docs/compatibility.md) | supported Amaranth versions, the version gate, the test matrix |
| [docs/testing.md](docs/testing.md) | running the tests, co-simulation, environment variables |
| [docs/design-notes/](docs/design-notes/) | the design notes the code was written from |

## Layout

    pioweave/              the package (flat, as amaranth-soc and amaranth-boards): __init__ (lazy public names), compile, data, designs, platform, project, doctor, _compat
    pioweave/builder/      the Python builder (needs no Amaranth)
    pioweave/emulator/     the PIO emulator (needs no Amaranth)
    pioweave/boards/       board definitions
    tests/                 the tests (unittest); tests/__init__.py makes it a package
    docs/                  the documentation (Sphinx with Markdown: `sphinx-build docs docs/_build`)
    scripts/matrix.sh      the Amaranth version matrix
    .github/workflows/     CI

## Relation to bitweave

[bitweave](https://github.com/youprint/bitweave) is a separate Rust program (a flow runner and MCP server). It assembles, analyses and simulates PIO programs (`bitweave pio NAME`), builds Pico SDK
firmware, loads boards and drives logic analysers. pioweave is a Python library that writes the programs and projects; the two meet only through files: bitweave runs `python -m pioweave build ...`
as a generator, and pioweave's tests use the bitweave binary as the reference simulator.

## Licence

Proprietary, see [LICENSE](LICENSE) [confirm the wording]. No third-party code is copied; the instruction encodings come from the RP2040 and RP2350 datasheets.
