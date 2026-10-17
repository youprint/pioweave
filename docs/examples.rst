Examples
========

The ``examples/`` folder at the root of the repository holds complete, runnable designs and scripts to copy. Every one is run by ``tests/test_examples.py`` (in a temporary directory), so they stay working. Each folder has a ``README.rst``. All of them need Amaranth except ``ws2812``, ``square`` and ``emulator_test``, which use only the builder and the emulator. Everything runs in the emulator; **nothing was run on a board**.

Run an example from the repository root (or after ``pip install -e .``, from anywhere):

::

   python examples/blink/blink.py --board pico --out build/blink

.. list-table::
   :header-rows: 1

   * - Folder
     - What it shows
     - Written with
     - Command
   * - ``examples/blink``
     - blink the user LED of a board (``pico``, ``pico2``, ``weact`` for the WeAct Studio RP2350B core, ``waveshare`` for the Waveshare Core2350B) by asking the board for the pin named ``"led"``; a loop around a 30-state delay makes the half period 994 cycles
     - Amaranth FSM, ``pioweave.boards``
     - ``python examples/blink/blink.py --board weact --out build/blink``
   * - ``examples/uart``
     - an 8N1 UART transmitter and receiver, 8 cycles per bit, on the Pico's ``uart_tx`` and ``uart_rx`` pins; the transmitter's pin waveform is fed to the receiver in the emulator and the bytes must come back
     - Amaranth FSM, data path (``platform.shifter``)
     - ``python examples/uart/uart.py --out build/uart``
   * - ``examples/ws2812``
     - the serial waveform of WS2812-style LEDs: 10 cycles per bit, high for 7 (one) or 2 (zero), 24 bits taken by autopull; the waveform is decoded back into the bits
     - **builder**, because the front end refuses a jump whose two branches have different side-set values
     - ``python examples/ws2812/ws2812.py --out build/ws2812``
   * - ``examples/square``
     - a square wave with a chosen high and low time (1 to 32 cycles each), measured from the pin waveform
     - builder, emulator
     - ``python examples/square/square.py --high 5 --low 3 --out build/square``
   * - ``examples/emulator_test``
     - a ``unittest`` file that tests a builder program with ``run_case`` and counts cycles with ``executed`` and ``stall_cycles``
     - builder, emulator, ``unittest``
     - ``python -m unittest examples/emulator_test/test_pulse.py -v``

Notes on the examples
---------------------

- **Blink and the clock.** The PIO has no timer, so the blink example uses a state machine clock of 4 kHz (a divider of about 31,000 to 37,000 of a 125 or 150 MHz system clock [verify the clock and the divider range against the datasheets]); the half period is then about 0.25 s. The emulator counts cycles, not seconds.
- **UART.** One word in the TX FIFO is one frame; the FIFO holds four words, so the script sends four bytes. The receiver pushes a word with the byte in bits 31..24.
- **WS2812.** The timings are those of the common 800 kHz program structure (T1 = 2, T2 = 5, T3 = 3 cycles at 8 MHz); the datasheet limits of a particular LED are not checked [verify].
- **Unit tests.** ``run_case`` returns the pin waveforms (``Result.wave``), the words the host read, the registers and the counters; see :doc:`emulator <emulator>`.

Starting a project: ``pioweave new``
------------------------------------

::

   python -m pioweave new --list                     # the templates
   python -m pioweave new my-blink                   # the default template, blink
   python -m pioweave new my-uart --template uart-tx --dir projects

creates the directory ``my-uart`` with a design module (``my_uart.py``), a ``unittest`` file that compiles the design and runs it in the emulator (``test_my_uart.py``), a ``README.md`` and a ``bitweave.toml`` with a ``[pio.my_uart]`` entry whose generator runs ``python -m pioweave build my_uart:MyUart ...``. The templates:

.. list-table::
   :header-rows: 1

   * - Template
     - Design
   * - ``blink``
     - a square wave on GPIO 0 as an Amaranth FSM (RP2040)
   * - ``uart-tx``
     - an 8N1 UART transmitter, 2 cycles per bit, as an Amaranth FSM
   * - ``uart-rx``
     - an 8N1 UART receiver, 8 cycles per bit, as an Amaranth FSM
   * - ``builder-square``
     - a square wave with chosen times, written with the Python builder (no Amaranth); its generator is the module itself, which prints the ``EMIT`` line

Run the new project's tests with ``cd my-uart && python -m unittest``. The ``bitweave.toml`` entry assumes bitweave runs from the project directory and that ``.bitweave-tools`` holds pioweave [verify with a bitweave run; not run here].
