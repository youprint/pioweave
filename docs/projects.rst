Projects, ``build()`` and the command line
==========================================

``platform.build()``
--------------------

The API reference of this part, generated from the docstrings, is in :doc:`reference/project`.

.. code:: python

   platform.build(design, name, build_dir, project="pio", sim=SimOptions(...))

The signature is that of ``amaranth.build.Platform.build`` (``elaboratable, name, build_dir, do_build, program_opts, do_program``) with two keyword-only options added. ``do_program=True`` is refused: pioweave never loads a board (bitweave does: ``bitweave program --pico``). With ``do_build=False`` it returns the ``BuildPlan`` without writing files.

``project`` selects what is written into ``build_dir``:

.. list-table::
   :header-rows: 1

   * - ``project=``
     - Files
   * - ``"pio"``
     - ``NAME.pio``, ``bitweave.toml`` with a simulation case, ``README.md`` (which FSM state became which instruction), ``build_NAME.sh``
   * - ``"pico-c"``
     - the same, plus ``CMakeLists.txt`` and ``main.c`` of a Pico SDK project, and a ``[firmware.NAME]`` section in ``bitweave.toml`` (with the board's ``board = ...`` when the platform has one)
   * - ``"micropython"``
     - the same as ``pio``, plus ``NAME_pio.py`` (written by ``pioasm -o python``, so ``pioasm`` is needed) and ``main.py``

``SimOptions``
--------------

``sim=SimOptions(...)`` describes the reference simulation whose results become the expectations of the bitweave case:

.. list-table::
   :header-rows: 1

   * - Field
     - Meaning
   * - ``factory``
     - a callable returning a fresh ``(design, platform)`` pair. A design requests its pins from the platform when it is elaborated, so every simulation needs new ones. Without it the case has no expectations.
   * - ``cycles``
     - length of the simulation in clock cycles (default 64)
   * - ``stim``
     - input waveforms, ``{gpio: "0101..."}``, one character per cycle; the last character repeats
   * - ``tx``
     - words in the TX FIFO at cycle 0
   * - ``tx_feed``
     - ``[(cycle, word)]`` written by the host so that the instruction of that cycle sees them
   * - ``drain``
     - the host empties the RX FIFO every cycle (default ``True``)
   * - ``irq_set``, ``irq_clear``
     - ``[(cycle, flag)]``: the host raises or lowers an IRQ flag at the start of that cycle (the instruction of that cycle sees it; clear first, then set)

The keywords ``factory``, ``cycles``, ``stim``, ``tx``, ``tx_feed`` and ``drain`` of version 0.1 development still work directly on ``build()`` and give a ``DeprecationWarning``.

``pioweave.amaranth_run(factory, cycles, stim, tx, tx_feed, drain, irq_set, irq_clear)`` runs the same simulation and returns ``{"waves": {gpio: waveform}, "rx": [(cycle, word)], "rx_left": n, "tx_left": n, "irq": [flags after each cycle], "dirs": {gpio: waveform}}`` (``irq`` is empty when the design uses no IRQ flag, ``dirs`` when it has no direction register).

The simulation case
-------------------

``bitweave.toml`` carries ``[pio.NAME]`` and ``[[pio.NAME.sim]]`` configured like the project (set / side-set / JMP / IN / OUT pin bases and counts, pin directions, shift directions, ``gpio_base`` on the 48-GPIO parts), the shift configuration (``autopull``, ``autopush``, thresholds), ``fifo_join``, ``status_sel`` / ``status_n`` and ``in_count`` when the design uses them, the ``tx`` words, ``tx_feed`` entries, ``rx_drain``, the host's IRQ events (``irq_set`` / ``irq_clear``), the input waveforms (``pins_in``), and the expectations from the Amaranth simulation: ``expect`` (pad levels, one character per cycle; for a group with a direction register the pad is the design's output where the pin is an output and the stimulus where it is an input), ``expect_dirs`` (the pin directions of those groups, 1 = output, one character per cycle), ``expect_rx`` (the words pushed) and ``expect_irq`` (the IRQ flags after the last cycle). ``bitweave pio NAME`` runs the compiled program in its own simulator and compares.

The generated C project (``pico-c``)
------------------------------------

``main.c`` claims a state machine, loads the program, sets the pin groups and their directions (``set``, side-set, OUT, IN, JMP pin; a group with a direction register gets one ``pio_sm_set_consecutive_pindirs`` call per run of equal bits of its initial direction), the shift directions with autopull / autopush and their thresholds, the FIFO join, ``mov`` status and IN pin count when used, the clock divider (from ``sm_clock_hz``), puts the ``tx`` words into the TX FIFO with ``pio_sm_put_blocking``, starts the machine, writes the ``tx_feed`` words, raises and lowers the IRQ flags of the host events (in order, not at the exact cycle), and when the program pushes, prints every received word (``printf``, USB stdio). A program with an ``irq ... rel`` instruction claims state machine 0. On the 48-GPIO parts it calls ``pio_set_gpio_base``. ``bitweave firmware NAME`` builds it (needs the Pico SDK, ``cmake``, ``arm-none-eabi-gcc``). Built to a UF2 with the real SDK in the tests; never run on a chip.

The generated MicroPython project
---------------------------------

``NAME_pio.py`` has the ``@rp2.asm_pio(...)`` decorator with the initial pin directions, shift directions (``out_shiftdir``, ``in_shiftdir``), ``autopull`` / ``autopush``, their thresholds and ``fifo_join`` [verify the names]; an input bit of a direction register is ``rp2.PIO.IN_LOW`` in ``set_init`` / ``sideset_init`` / ``out_init`` [verify the constant]; a design that needs ``mov`` status or the IN pin count, or whose side-set writes pin directions, is refused with ``PioProjectError`` (MicroPython has no option for them [verify]), and host IRQ events are only noted in a comment; ``main.py`` creates ``rp2.StateMachine(0, NAME, freq=..., set_base=..., ...)``, puts the words, activates it and prints received words. The ``rp2`` API names come from the MicroPython documentation and have not been run on a device [verify]. Programs that need the GPIO base 16 (pins above GPIO 31) may not work in MicroPython [verify].

The command line
----------------

::

   python -m pioweave list                                          # the example designs and their chips
   python -m pioweave doctor [--strict]                             # check this environment
   python -m pioweave build DESIGN OUTDIR [options]

``DESIGN`` is an example name or ``module:Class`` of your own ``PioDesign`` / ``Elaboratable``. ``module`` is imported with the current directory first on ``sys.path`` and its top-level code runs, and the class (or any callable without arguments) is called to make the design: run ``build`` and ``emulate`` only on designs you trust, in a directory you trust, and never pass a ``DESIGN`` that comes from untrusted input. (The chip is checked before the module is imported, so a missing ``--chip`` does not run any code.) It writes the project to ``OUTDIR/project`` and prints ``EMIT {"path": ".../NAME.pio"}``, which is what a bitweave ``generator = [...]`` expects. Options:

.. list-table::
   :header-rows: 1

   * - Option
     -
   * - ``--chip CHIP``
     - ``rp2040``, ``rp2350``, ``rp2354``, ``rp2350a``, ``rp2350b``, ``rp2354a``, ``rp2354b`` (needed for your own designs)
   * - ``--project pio|pico-c|micropython``
     - default ``pio``
   * - ``--clock-hz N``
     - the state machine clock the C and MicroPython projects ask for
   * - ``--cycles N``, ``--stim GPIO=0101...``
     - the reference simulation: ``N`` from 1 to 1,000,000, a waveform is a non-empty string of 0 and 1 for a GPIO from 0 to 47
   * - ``--max-cycles N``
     - raise the ceiling of ``--cycles`` (default 1,000,000; the run takes time and memory in proportion to the cycles: 1,000,000 cycles of ``emulate`` took 6 s and 109 MB on the machine of the audit, 10,000,000 took 58 s and 814 MB)
   * - ``--tx W,W,...``, ``--tx-feed CYCLE=WORD``, ``--no-drain``
     - the host side of the data path (words are 32-bit numbers, decimal or ``0x...``)
   * - ``--name NAME``
     - the program name (default: the example or the lower-cased class name); see below

Names, errors and what is overwritten
-------------------------------------

The name is pasted into file names, ``bitweave.toml``, ``CMakeLists.txt``, ``main.c`` and the C symbols ``NAME_program``, so it must be a plain identifier: ASCII letters, digits and ``_``, not starting with a digit, at most 64 characters, and not a keyword of pioasm (``jmp``, ``in``, ``out``, ``wrap`` and so on), C (``int``, ``static`` and so on) or Python. A hyphen is not allowed (``my-prog`` becomes the symbol ``my_prog`` in pioasm's header but not in ``main.c``): use ``my_prog``. The same check runs in ``platform.build``, ``Block.build`` and ``write_project`` (``PioProjectError`` / ``BlockError``), before anything is written and also under ``python -O``; it does not use ``assert``. ``pioweave new`` accepts a hyphen in the project directory name but derives the module name from it, which must not be a keyword or the name of a standard-library module (``json.py`` would shadow ``json``).

Values from the command line or from ``SimOptions`` are checked as well, because they are written into ``bitweave.toml`` and ``main.c``: a waveform of anything but 0 and 1 (a newline in it would add a table to the TOML file), a GPIO above 47, a word that does not fit 32 bits, a cycle count below 1 or above ``--max-cycles``, a clock that is not a positive finite number. A mistake of yours (a bad option, an unknown chip or design, a module that cannot be imported, a file that cannot be written) prints ``error: ...`` on standard error and exits with status 2, with no traceback; set ``PIOWEAVE_TRACEBACK=1`` to see the traceback. Anything the design itself does wrong is a ``PioCompileError`` with the source line.

``build`` overwrites the files it writes (a rebuild into the same directory is what a bitweave generator does) and keeps no other file; edit a generated ``bitweave.toml`` in another place. It refuses to write through a symbolic link, because the write would follow the link and replace whatever it points to.

With bitweave
-------------

::

   [pio.uart_tx_am]
   generator = ["{root}/.bitweave-tools/bin/python", "-m", "pioweave", "build", "uart_tx", "{outdir}"]
   chip = "rp2040"
   [[pio.uart_tx_am.sim]]
   name = "amaranth_reference"
   cycles = 60
   # ... the expectations come from the generated bitweave.toml, or are written out like this

``bitweave pio uart_tx_am`` runs the generator, assembles the result with ``pioasm``, and runs the cases. ``bitweave install pioweave --yes`` puts pioweave into ``.bitweave-tools`` (see :doc:`getting started <getting-started>`).
