# bitweave/python: PIO programs in Python and Amaranth (three optional packages)

The `bitweave` binary, its MCP server and its PIO flow and simulator are Rust and keep the name Bitweave. This directory holds only the Raspberry Pi RP work in Python,
as three pip packages (owner decision 2026-10-17: named amaranth-rp; the boards package was added the same day). bitweave runs them as *generator* commands (a command that prints `EMIT {"path": "x.pio"}`).
Nothing here has run on a chip.

| Package | Import | Needs | What |
|---|---|---|---|
| `builder/` amaranth-rp-builder | `amaranth_rp_builder` | nothing | Python builder for PIO programs for the RP2040, RP2350, RP2354 and A / B variants: one method per instruction, its own encoder, pioasm text. Note: `docs/pio-dsl.md` |
| `boards/` amaranth-rp-boards | `amaranth_rp_boards` | amaranth-rp | Board definitions, one module per board like amaranth-boards: Raspberry Pi Pico (RP2040, the owner's RP2040 target), Pico 2, Waveshare Core2350B, with named `led`, `uart_*`, `i2c_*` and `spi_*` pins from the Pico SDK headers and the SDK board name for the firmware flow |
| `rp/` amaranth-rp | `amaranth_rp` | amaranth 0.5.10 (0.5.x), the builder | Amaranth platforms (`RP2040PioPlatform` ... `RP2354BPioPlatform`), FSM-to-PIO compiler, project writers (`pio`, `pico-c`, `micropython`), reference simulation. Note: `docs/amaranth-pio.md`. `_compat.py` is the only file that touches Amaranth's private IR and checks the version |

Command lines: `python -m amaranth_rp_builder emit square build/`, `python -m amaranth_rp build square_wave build/ --project pico-c`, `python -m amaranth_rp list`.

## Install and test

    bitweave install rp-pio --yes                          # into .bitweave-tools of a project (amaranth + both packages, from this directory; not on PyPI)
    # or by hand, into bitweave's own tools environment:
    .tools/bin/pip install -e python/builder -e python/rp -e python/boards pytest
    .tools/bin/python -m pytest python/builder/tests python/rp/tests python/boards/tests     # about 5 minutes; PIO_COSIM_N=150 draws more random designs

The rp tests need the built `bitweave` (`target/release/bitweave`) and `pioasm` (`.tools/bin/pioasm`, see `bitweave guide pio-and-raspberry-pi`); tests that need them are skipped, not
passed, when missing (`BITWEAVE`, `PIOASM`). Generated Pico SDK projects are built only with `PIO_BUILD_FIRMWARE=1` and the SDK and `arm-none-eabi-gcc` under `.tools`.
`BITWEAVE_PYTHON` names this directory for `bitweave install rp-pio` when the binary is not inside the repository.

`python/scripts/matrix.sh 0.5.10 [other versions]` runs the tests against given Amaranth versions in fresh environments; only 0.5.10 exists in the 0.5 series on PyPI (checked 2026-10-17) and
another version is refused by pip (package metadata) and by the version gate (shown for 0.5.9 and 0.4.0).

## Verification (2026-10-17)

- the builder's encoder equals the real `pioasm` on 10,681 instruction words in 351 programs;
- Amaranth designs: six examples, directed forms and 450 random FSM designs (RP2040, RP2350, RP2350A/B, RP2354B, GPIO base 0 and 16) agree with the bitweave PIO simulator pin by
  pin and cycle by cycle; 36 designs outside the subset are refused with a reason;
- the generated bitweave cases pass; three generated Pico SDK C projects (RP2040, RP2350, RP2350B with GPIO base 16) build to a UF2 with the real SDK.

Not verified: any run on a chip; the MicroPython output (rp2 API names from the documentation); Amaranth versions other than 0.5.10; PyPI publication (not planned, licence is proprietary).
