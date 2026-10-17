# pioweave documentation

Start with the [README](../README.md) for what pioweave is, then:

1. [Getting started](getting-started.md): install, a first design, build, check.
2. [Designing for PIO](designing-for-pio.md): the Amaranth style the compiler accepts.
3. [The data path](data-path.md): FIFOs, OSR and ISR.
4. [The Python builder](builder.md): writing programs instruction by instruction.
5. [Boards](boards.md): named pins for real boards.
6. [Projects and the command line](projects.md): what `build()` writes and how to use it.
7. [Compatibility](compatibility.md): Amaranth versions and the test matrix.
8. [Testing](testing.md): running the tests and what they prove.
9. [Design notes](design-notes/): why the code is the way it is.

Conventions: unverified facts carry `[verify]`, owner decisions `[confirm]`. Dates are ISO 8601. Nothing in these documents claims a result on hardware unless it says so, and nothing has run on a chip yet.
