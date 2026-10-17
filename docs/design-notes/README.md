# Design notes

The notes the code was written from. They record decisions, facts and verification as of 2026-10-17 and keep the names that were current when they were written (the packages were `amaranth-rp`,
`amaranth-rp-builder` and `amaranth-rp-boards` before they became the single package `pioweave` with `pioweave.builder` and `pioweave.boards`; file names in the notes may be older). The user documentation
is in `docs/*.md`.

| Note | What |
|---|---|
| [pio-dsl.md](pio-dsl.md) | the Python builder: API, the rules found by comparing with `pioasm`, the two independent encoders |
| [amaranth-pio.md](amaranth-pio.md) | the Amaranth front end: what is accepted and refused, the data path, the 32-GPIO window, cycle alignment of the two simulators, project kinds, verification |
| [amaranth-package-study.md](amaranth-package-study.md) | how Amaranth packages are structured and the packaging options that were considered |
| [amaranth-ir-study.md](amaranth-ir-study.md) | how much of Amaranth's internals the compiler depends on, tested against Amaranth 0.6.0.dev140 |
