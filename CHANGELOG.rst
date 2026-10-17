Change log
##########

This document records changes to pioweave in the style of Amaranth's ``changes.rst``: one section per version, with the entries grouped under Added, Changed and Removed. Dates are ISO 8601. Facts about hardware are marked ``[verify]`` where they are not checked; nothing has run on a chip.

Version 0.1.0 (unreleased)
==========================

Added
-----

- ``pioweave.builder``: a Python builder for PIO programs with one method per instruction (``Program``), the operand constants, directives (``.side_set``, ``.wrap``, ``.fifo``, ``.mov_status``, ``.in``, ``.out``, ``.set``, ``.clock_divider``), labels, an encoder written from the RP2040 and RP2350 datasheets (equal to ``pioasm`` on 10,681 words in 351 programs), ``pio_text()``, ``words()``, ``write_pio()`` and ``PioError``. It needs no Amaranth.
- The Amaranth front end (``pioweave``): ``PioDesign``, the platforms ``RP2040PioPlatform``, ``RP2350PioPlatform``, ``RP2350APioPlatform``, ``RP2350BPioPlatform``, ``RP2354PioPlatform``, ``RP2354APioPlatform`` and ``RP2354BPioPlatform``; the FSM-to-PIO compiler (``compile_design``, ``PioCompileError``) that refuses anything that would change the cycle behaviour; the data path (``platform.shifter`` with ``pull``, ``push``, ``out``, ``in_``); ten example designs.
- ``pioweave.emulator``: a cycle-by-cycle emulator of the PIO blocks (four state machines, shared instruction memory, FIFOs, IRQ flags, pins, PIO versions 0 and 1) with ``PioBlock``, ``run_case``, ``run_compiled``, ``to_vcd`` and ``EmulationError``. It needs no Amaranth.
- ``pioweave.boards``: board definitions with named pins for the Raspberry Pi Pico, the Pico 2, the WeAct Studio RP2350B core and the Waveshare Core2350B.
- Project writers: ``platform.build()`` writes a ``.pio`` file, a ``bitweave.toml`` case with the expected waveforms taken from the Amaranth simulation, a README, and on request a Pico SDK C project or a MicroPython program (``SimOptions``, ``write_project``, ``PioProjectError``).
- The command line ``python -m pioweave``: ``list``, ``build`` (the generator command for bitweave), ``emulate`` (run a design in the emulator and print the pin waveforms, optionally a VCD), ``doctor`` (check the environment), ``new`` (scaffold a project from a template, ``new --list`` shows the templates) and ``--version``.
- ``examples/``: runnable examples (blink on a board LED, a UART transmitter and receiver, a WS2812-style waveform, a square wave generator, a unittest of a program in the emulator), run by ``tests/test_examples.py`` and described in ``docs/examples.rst``.
- The version gate: ``import pioweave`` accepts Amaranth 0.5.0 to 0.5.10 and one tested 0.6 development commit (``UnsupportedAmaranth``, ``PIOWEAVE_ALLOW_UNTESTED=1``), and ``scripts/matrix.sh`` runs the suite against other versions.
- Error messages of the builder, the compiler, the project writer and the version gate carry a short ``hint:`` line with what to change for the most common mistakes; the original text is unchanged.
- ``python -m pioweave.emulator.check PROGRAM_JSON CASE_JSON`` (``pioweave.emulator.check.check_case`` and ``main``): runs one bitweave simulation case, given as the JSON ``pioasm -o json`` writes and the case as a JSON object, in the emulator and prints one JSON object (``ok``, ``error``, the pad and direction waveforms, the words received, the FIFO counts, the final registers, the keys it does not support), so that bitweave's ``cross_check = "pioweave"`` can compare the two simulators; tested directly and against bitweave's ``pio`` flow on random programs.
- Documentation in reStructuredText (Sphinx, with the examples run by ``sphinx-build -b doctest``): getting started, designing for PIO, the data path, the builder, the emulator, boards, projects, compatibility, testing, examples, releasing, the API reference and the design notes.

Changed
-------

- None: this is the first version.

Removed
-------

- None: this is the first version.
