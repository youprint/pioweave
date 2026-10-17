# bitweave/python: write PIO programs in Python and Amaranth

Optional Python package of the bitweave repository. The `bitweave` binary itself contains no Python and does not need this package: bitweave runs these
files as *generator* commands (a command that prints `EMIT {"path": "x.pio"}`), exactly like any other design generator. Nothing here has run on a chip.

| File | What |
|---|---|
| `pioprog.py` | Python builder for PIO programs (one method per instruction, errors when the program is built, its own encoder, pioasm text) for the RP2040, RP2350, RP2354 and their A / B variants. Note: `docs/pio-dsl.md` |
| `pio_examples.py`, `bitweave_pio_emit.py` | example programs written with the builder and the generator script for them |
| `platform_rp.py` | Amaranth platform module for the PIO blocks (RP2040, RP2350, RP2350A/B, RP2354, RP2354A/B): GPIO resources, pin and scratch-register tracking, `build()` |
| `pio_compile.py` | compiler from one Amaranth FSM (one state = one PIO instruction) to PIO instructions. Note: `docs/amaranth-pio.md` |
| `pio_project.py` | project writers for `platform.build(..., project="pio" \| "pico-c" \| "micropython")` and the Amaranth reference simulation |
| `pio_designs.py`, `bitweave_pio_amaranth.py` | example Amaranth designs and their generator script |
| `tests/` | tests (pytest) and the co-simulation of Amaranth against bitweave's PIO simulator |

## Use

    .tools/bin/pip install "amaranth==0.5.10" pytest        # into bitweave's tools environment (git-ignored)
    cargo build --release                                  # bitweave itself
    cd examples/pio && ../../target/release/bitweave pio uart_tx_am   # or any [pio.*_am] / [pio.*_py] entry of examples/pio/bitweave.toml
    .tools/bin/python -m pytest python/tests                # about 2 to 3 minutes; PIO_COSIM_N=150 draws more random designs

The tests need the built `bitweave` (`target/release/bitweave`) and `pioasm` (`.tools/bin/pioasm`, see `bitweave guide pio-and-raspberry-pi`); the tests
that need them are skipped, not passed, when they are missing (`BITWEAVE` and `PIOASM` name them). The generated Pico SDK C projects are built only with
`PIO_BUILD_FIRMWARE=1` and the SDK and `arm-none-eabi-gcc` under `.tools`.

## Verification (2026-10-17)

- the builder's own encoder equals the real `pioasm` on 10,681 instruction words in 351 programs;
- Amaranth designs: six examples, directed forms and 450 random FSM designs (RP2040, RP2350, RP2350A/B, RP2354B, GPIO base 0 and 16) agree with the bitweave
  PIO simulator pin by pin and cycle by cycle; 36 designs outside the subset are refused with a reason;
- the generated bitweave cases pass; three generated Pico SDK C projects (RP2040, RP2350, RP2350B with GPIO base 16) build to a UF2 with the real SDK.

Not verified: any run on a chip; the MicroPython output (rp2 API names from the documentation, not run on a device); Amaranth versions other than 0.5.10 (the
compiler reads `Fragment.statements`, which is not a stable public API); the `pioasm` rules found by comparison come from one SDK checkout (2026-10-17).
