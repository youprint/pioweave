Testing
=======

::

   pip install -e '.[test]'
   PYTHONWARNINGS=error python -m unittest discover -t . -s tests -v     # or: pdm run test

Without the tools below, the tests that need them are **skipped, not passed**. Run ``python -m pioweave doctor`` to see which tools are found.

What the tests prove
--------------------

.. list-table::
   :header-rows: 1

   * - Tests
     - What
     - Needs
   * - ``tests/test_program.py``
     - the builder: every instruction form, every error, and that its own encoder equals the real ``pioasm`` on 10,681 words in 351 programs
     - ``pioasm`` for the comparison
   * - ``tests/test_compile.py``
     - the compiler: the seventeen example designs, 450 random control-flow designs and 340 random data-path designs (autopull / autopush, ifempty / iffull, mov, IRQ flags with host events, wait pin, joined FIFOs, and in about 40 percent of the data-path designs and 30 percent of the others pin directions with a direction register: out / mov / set / side-set pindirs) run in the Amaranth simulator and in bitweave's PIO simulator and must agree pin by pin and cycle by cycle (and on the words pushed with their cycles, the IRQ flags after every cycle, and the FIFO contents left); directed forms; the instruction words of every example on every Amaranth version
     - the ``bitweave`` binary and ``pioasm``
   * - ``tests/test_emulator.py``, ``tests/test_emulator_datasheet.py``, ``tests/test_emulator_trace.py``, ``tests/test_emulator_parity.py``, ``tests/test_emulator_amaranth.py``, ``tests/test_emulator_differential.py``
     - the emulator: directed tests (facts cited from the datasheets), the ``emulate`` API, behaviours of another emulator worked out fresh, comparison with the Amaranth simulation (no tools) and with bitweave's PIO simulator on random programs and on extra families (EXEC, IRQ, joins, thresholds, pin directions); see :doc:`emulator <emulator>`; a hardware test plan is in :doc:`hardware-testing` (nothing has run on a chip)
     - the differential one needs ``bitweave`` and ``pioasm``
   * - ``tests/test_refusals.py``
     - everything the compiler must refuse, with the reason; the version gate; the FSM state-name lookup
     - nothing
   * - ``tests/test_project.py``
     - the generated files, that the generated bitweave cases pass in bitweave, that the generated C projects (eight examples and a PIO version 1 design with ``STATUS_IRQ_SET`` and the IN pin count) build to a UF2 with the real Pico SDK, the deprecated ``build()`` keywords, the ``PioDesign`` base class
     - bitweave, ``pioasm``; the SDK build needs ``PIO_BUILD_FIRMWARE=1``
   * - ``tests/test_block.py``, ``tests/test_block_cosim.py``
     - blocks with several state machines (:doc:`blocks`): the four example blocks and random blocks of two to four machines (shared IRQ flags and pins) run in the Amaranth simulation of the whole block and in the emulator and must agree cycle by cycle (pads, what the machines read, IRQ flags, words received, FIFO contents); each machine alone in the emulator with the derived environment must repeat the block; the shared-flag and pin-priority semantics; the project files; the command line; the generated one-machine cases in bitweave (and the SDK build with ``PIO_BUILD_FIRMWARE=1``); the refusals are in ``tests/test_refusals.py``
     - the bitweave cases need ``bitweave`` and ``pioasm``; the rest nothing
   * - ``tests/test_block_dirs.py``
     - blocks whose machines use pin directions (``platform.directions``; :doc:`blocks`): an open-drain line with a follower on the RP2040, and a bidirectional bus (``out pindirs``, ``mov pindirs``) with the line and a follower on the RP2350, with fixed and random stimulus; the Amaranth simulation of the block, the emulator and each machine alone must agree on the pads and the directions; ``expect_dirs`` in the generated cases and the final direction lines of ``main.c``; the cases in bitweave (and the SDK build with ``PIO_BUILD_FIRMWARE=1``)
     - the bitweave cases need ``bitweave`` and ``pioasm``; the rest nothing
   * - ``tests/test_amaranth_datasheet.py``
     - directed tests of the Amaranth data path with expected values worked out by hand from quoted datasheet sentences (autopush word, ``push iffull`` at the threshold, counter saturation, ``wait 1 irq`` against ``wait 0 irq``, no folding of a jump target): the Amaranth reference simulation alone
     - nothing
   * - ``tests/test_packaging.py``
     - the Documentation URL hook of ``pdm_build.py`` (runs twice when ``python -m build`` builds the wheel from the sdist), the versions of the subpackages, no ``py.typed`` without annotations
     - nothing
   * - ``tests/test_boards.py``
     - the board pins against the Pico SDK headers and a blink design compiled on each board
     - an SDK checkout for the header comparison
   * - the examples in the documentation
     - ``sphinx-build -W -b doctest docs docs/_build/doctest`` (or ``pdm run test-docs``) runs every ``.. testcode::`` example of the pages and the docstrings and compares the ``.. testoutput::`` listings (the ``.pio`` programs shown) with what the examples write
     - Sphinx and sphinx-rtd-theme (``pdm install -G docs``); no other tool

What this does **not** prove: anything about a chip. The simulator and the compiler were written by the same author from the same datasheets, so the co-simulation catches mismatches between the Amaranth semantics and the PIO model, not a misreading of the datasheets common to both (the model was also cross-checked against ``pioasm`` and against the rp2040-pio-emulator in bitweave's own tests). Real timing also depends on the state machine clock and on the pads. The Amaranth data path was written to match the emulator, so their agreement holds by construction; the independent checks are bitweave's simulator, the directed tests with hand-derived expectations (``tests/test_amaranth_datasheet.py``, ``tests/test_emulator_datasheet.py``) and, once it has been run, the hardware plan.

Environment variables
---------------------

.. list-table::
   :header-rows: 1

   * - Variable
     - Meaning
   * - ``BITWEAVE``
     - path of the ``bitweave`` binary (default: ``PATH``, then ``../bitweave/target/release/bitweave``)
   * - ``PIOASM``
     - path of ``pioasm`` (default: ``PATH``, next to the Python, then ``../bitweave/.tools/bin/pioasm``)
   * - ``BITWEAVE_REPO``
     - a bitweave checkout, used to find the Pico SDK and the compiler under ``.tools`` (default ``../bitweave``)
   * - ``PICO_SDK_PATH``, ``ARM_TOOLCHAIN_DIR``
     - the Pico SDK and the directory with ``arm-none-eabi-gcc``
   * - ``PIO_BUILD_FIRMWARE=1``
     - also build the generated C projects (about 35 s each)
   * - ``PIO_COSIM_N``
     - number of random Amaranth designs per generator (default 15; 250 for a thorough run)
   * - ``PIO_EMU_N``
     - number of random programs in the emulator's differential test against bitweave (default 40; each takes about 4 s)
   * - ``PIOWEAVE_ALLOW_UNTESTED=1``
     - let pioweave import with an Amaranth version it was not tested with

A random design that fails prints its seed, the generated program and the specification, so it can be replayed: ``PIO_COSIM_N=200 python -m unittest -v tests.test_compile.DataPathTests.test_random_data_path_designs_match_the_pio_simulator_194``.

Getting the tools
-----------------

``bitweave`` is a Rust program: ``cargo build --release`` in its repository. ``pioasm`` is built from the Pico SDK (``tools/pioasm``); bitweave documents the steps (``bitweave guide pio``). The SDK and the ARM compiler are needed only for the firmware builds.

Other Amaranth versions
-----------------------

``scripts/matrix.sh`` runs the suite against other Amaranth versions; see :doc:`compatibility <compatibility>`.
