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

``pioweave.amaranth_run(factory, cycles, stim, tx, tx_feed, drain)`` runs the same simulation and returns ``{"waves": {gpio: waveform}, "rx": [(cycle, word)], "rx_left": n, "tx_left": n}``.

The simulation case
-------------------

``bitweave.toml`` carries ``[pio.NAME]`` and ``[[pio.NAME.sim]]`` configured like the project (set / side-set / JMP / IN / OUT pin bases and counts, pin directions, shift directions, ``gpio_base`` on the 48-GPIO parts), the ``tx`` words, ``tx_feed`` entries, ``rx_drain``, the input waveforms (``pins_in``), and the expectations from the Amaranth simulation: ``expect`` (pad levels, one character per cycle) and ``expect_rx`` (the words pushed). ``bitweave pio NAME`` runs the compiled program in its own simulator and compares.

The generated C project (``pico-c``)
------------------------------------

``main.c`` claims a state machine, loads the program, sets the pin groups and their directions (``set``, side-set, OUT, IN, JMP pin), the shift directions, the clock divider (from ``sm_clock_hz``), puts the ``tx`` words into the TX FIFO with ``pio_sm_put_blocking``, starts the machine, writes the ``tx_feed`` words, and when the program pushes, prints every received word (``printf``, USB stdio). On the 48-GPIO parts it calls ``pio_set_gpio_base``. ``bitweave firmware NAME`` builds it (needs the Pico SDK, ``cmake``, ``arm-none-eabi-gcc``). Built to a UF2 with the real SDK in the tests; never run on a chip.

The generated MicroPython project
---------------------------------

``NAME_pio.py`` has the ``@rp2.asm_pio(...)`` decorator with the initial pin directions, shift directions (``out_shiftdir``, ``in_shiftdir``); ``main.py`` creates ``rp2.StateMachine(0, NAME, freq=..., set_base=..., ...)``, puts the words, activates it and prints received words. The ``rp2`` API names come from the MicroPython documentation and have not been run on a device [verify]. Programs that need the GPIO base 16 (pins above GPIO 31) may not work in MicroPython [verify].

The command line
----------------

::

   python -m pioweave list                                          # the example designs and their chips
   python -m pioweave doctor [--strict]                             # check this environment
   python -m pioweave build DESIGN OUTDIR [options]

``DESIGN`` is an example name or ``module:Class`` of your own ``PioDesign`` / ``Elaboratable`` (found through the current directory). It writes the project to ``OUTDIR/project`` and prints ``EMIT {"path": ".../NAME.pio"}``, which is what a bitweave ``generator = [...]`` expects. Options:

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
     - the reference simulation
   * - ``--tx W,W,...``, ``--tx-feed CYCLE=WORD``, ``--no-drain``
     - the host side of the data path
   * - ``--name NAME``
     - the program name (default: the example or the lower-cased class name)

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
