# bitweave/python: PIO programs in Python and Amaranth (three optional packages)

The `bitweave` binary, its MCP server and its PIO flow and simulator are Rust and keep the name Bitweave. This directory holds only the Raspberry Pi RP work in Python,
as three pip packages (owner decision 2026-10-17: named pioweave; the boards package was added the same day). bitweave runs them as *generator* commands (a command that prints `EMIT {"path": "x.pio"}`).
Nothing here has run on a chip.

| Package | Import | Needs | What |
|---|---|---|---|
| `builder/` pioweave-builder | `pioweave_builder` | nothing | Python builder for PIO programs for the RP2040, RP2350, RP2354 and A / B variants: one method per instruction, its own encoder, pioasm text. Note: `docs/pio-dsl.md` |
| `boards/` pioweave-boards | `pioweave_boards` | pioweave | Board definitions, one module per board like amaranth-boards: Raspberry Pi Pico (RP2040, the owner's RP2040 target), Pico 2, Waveshare Core2350B, with named `led`, `uart_*`, `i2c_*` and `spi_*` pins from the Pico SDK headers and the SDK board name for the firmware flow |
| `rp/` pioweave | `pioweave` | amaranth 0.5.x and the 0.6 development version, the builder | Amaranth platforms (`RP2040PioPlatform` ... `RP2354BPioPlatform`), FSM-to-PIO compiler, project writers (`pio`, `pico-c`, `micropython`), reference simulation. Note: `docs/amaranth-pio.md`. `_compat.py` is the only file that touches Amaranth's private IR and checks the version |

Command lines: `python -m pioweave_builder emit square build/`, `python -m pioweave build square_wave build/ --project pico-c`, `python -m pioweave list`.

## Install and test

    bitweave install pioweave --yes                          # into .bitweave-tools of a project (amaranth + both packages, from this directory; not on PyPI)
    # or by hand, into bitweave's own tools environment:
    .tools/bin/pip install -e python/builder -e python/pioweave -e python/boards pytest
    .tools/bin/python -m pytest python/builder/tests python/pioweave/tests python/boards/tests     # about 5 minutes; PIO_COSIM_N=150 draws more random designs

The rp tests need the built `bitweave` (`target/release/bitweave`) and `pioasm` (`.tools/bin/pioasm`, see `bitweave guide pio-and-raspberry-pi`); tests that need them are skipped, not
passed, when missing (`BITWEAVE`, `PIOASM`). Generated Pico SDK projects are built only with `PIO_BUILD_FIRMWARE=1` and the SDK and `arm-none-eabi-gcc` under `.tools`.
`BITWEAVE_PYTHON` names this directory for `bitweave install pioweave` when the binary is not inside the repository.

`python/scripts/matrix.sh 0.5.1 0.5.10 git:COMMIT` runs the tests against given Amaranth versions in fresh environments (`MATRIX_QUICK=1`: only the quick tests; `PIO_BUILD_FIRMWARE=1`: also the Pico SDK builds).
There is no 0.6 beta release (PyPI has no pre-release and the newest tag is v0.6.0.dev0), so 0.6 is tested as the development version at a pinned commit (`_compat.TESTED_DEV`). Results of 2026-10-17:
the whole suite (166 tests, with the Pico SDK builds) passes on Amaranth 0.5.1, 0.5.10 and 0.6.0.dev140 (commit 90449f1e6d5abdfb8cac7519ac7b34d83a9f42b7); the quick suite (72 tests plus the instruction
words of all ten examples) passes on 0.5.0, 0.5.2 to 0.5.9 as well; versions outside that (0.4.x, 0.5.11, other 0.6 development versions) are refused by pip (package metadata) and by the version gate.

## Verification (2026-10-17)

- the builder's encoder equals the real `pioasm` on 10,681 instruction words in 351 programs;
- Amaranth designs: ten examples, directed forms and 450 random FSM designs (RP2040, RP2350, RP2350A/B, RP2354B, GPIO base 0 and 16) agree with the bitweave PIO simulator pin by
  pin and cycle by cycle; designs outside the subset are refused with a reason (25 refusal tests);
- the data path (FIFOs, OSR, ISR: `pull`, `push`, `out`, `in`; UART transmitter and receiver, byte swap, non-blocking echo): 250 random designs agree with the simulator on pins, words pushed with
  their cycles and FIFO contents; not built yet: autopull/autopush, `mov` with OSR/ISR, `irq`, joined FIFOs (see `docs/amaranth-pio.md` section 2a);
- the generated bitweave cases pass; three generated Pico SDK C projects (RP2040, RP2350, RP2350B with GPIO base 16) build to a UF2 with the real SDK.

Not verified: any run on a chip; the MicroPython output (rp2 API names from the documentation); Amaranth versions other than those listed above (the 0.6 development version is a moving target: a later commit may break the compiler); PyPI publication (not planned, licence is proprietary).
