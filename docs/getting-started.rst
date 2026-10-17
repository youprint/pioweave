Getting started
===============

.. testsetup:: *

   import os, shutil, tempfile
   _olddir = os.getcwd()
   _tmpdir = tempfile.mkdtemp()
   os.chdir(_tmpdir)

.. testcleanup:: *

   os.chdir(_olddir)
   shutil.rmtree(_tmpdir)

What you need
-------------

.. list-table::
   :header-rows: 1

   * - Needed for
     - Tool
   * - everything
     - Python 3.9 or newer, Amaranth 0.5.x (installed by pip)
   * - checking a design against a simulator, and building the generated projects
     - `bitweave <https://github.com/youprint/bitweave>`__ (a Rust binary) and ``pioasm`` (from the Raspberry Pi Pico SDK)
   * - building the generated C projects
     - the Pico SDK, ``cmake`` and ``arm-none-eabi-gcc``
   * - loading a board
     - ``picotool``, run by bitweave (``bitweave program --pico``); pioweave never loads a board

Only Python and Amaranth are needed to write designs and generate ``.pio`` files. ``pioasm`` is needed to generate MicroPython projects and for the tests that compare with bitweave.

Install
-------

::

   python3 -m venv .venv && . .venv/bin/activate
   pip install pioweave                                          # or: pip install git+https://github.com/youprint/pioweave.git
   python -m pioweave doctor                                     # Amaranth version, optional tools, a smoke test
   python -m pioweave list                                       # the example designs

From a checkout (development): ``git clone https://github.com/youprint/pioweave && cd pioweave && pip install -e '.[test]'``.

``pip`` pulls in ``amaranth>=0.5.1,<0.6``. To use the 0.6 development version (see :doc:`compatibility <compatibility>`), install Amaranth from its repository first and then pioweave with ``--no-deps``.

With bitweave in a project directory: ``bitweave install pioweave --yes`` creates ``.bitweave-tools`` and installs Amaranth and pioweave into it. The component takes pioweave from a checkout; point bitweave at it with ``PIOWEAVE_DIR=/path/to/pioweave`` (or keep the checkout beside the bitweave checkout). Then use ``{root}/.bitweave-tools/bin/python -m pioweave build ...`` as a generator.

Your first design
-----------------

A design is an Amaranth module with one ``m.FSM()``; every state does what one PIO instruction does. Pins and registers come from the platform:

.. testcode::

   from amaranth.hdl import Module
   from pioweave import PioDesign, RP2040PioPlatform, SimOptions

   class Blink(PioDesign):                        # a square wave on GPIO 0: two cycles high, two low
       def elaborate(self, platform):
           m = Module()
           pin = platform.buffer(m, "o", "gpio", 0)       # an output pin group, as an amaranth.lib.io.Buffer
           with m.FSM():
               with m.State("HIGH"):
                   m.d.sync += pin.o.eq(1)
                   m.next = "HIGH_HOLD"
               with m.State("HIGH_HOLD"):                 # no effect: folded into the delay of the instruction before
                   m.next = "LOW"
               with m.State("LOW"):
                   m.d.sync += pin.o.eq(0)
                   m.next = "LOW_HOLD"
               with m.State("LOW_HOLD"):
                   m.next = "HIGH"
           return m

   factory = lambda: (Blink(), RP2040PioPlatform())
   design, platform = factory()
   platform.build(design, "blink", "build/blink", project="pio", sim=SimOptions(factory=factory, cycles=24))

This writes ``build/blink/blink.pio``:

.. testcode::
   :hide:

   print(open("build/blink/blink.pio").read().strip())

.. testoutput::

   .program blink
   .pio_version 0
       set pins, 1 [1]
       set pins, 0 [1]

(The program wraps from the last instruction to the first by default, so no ``.wrap`` directives are written.)

Why two delay states and not one ``set`` per level: a PIO ``set`` takes one cycle, and ``[1]`` adds one more, so each level lasts two cycles. In your FSM that is two states per level, and the compiler folds the state without an effect into the delay of the instruction before it.

Build and check
---------------

``build()`` writes, into ``build/blink/``:

- ``blink.pio``: the program;
- ``bitweave.toml``: a simulation case whose expected pin waveform is taken from the Amaranth simulation of your design;
- ``README.md``: which FSM state became which instruction.

Then bitweave runs the compiled program in its own PIO simulator and compares:

::

   cd build/blink
   bitweave --config bitweave.toml pio blink        # needs pioasm; [pass] pio:blink  sim:amaranth_reference

Without ``pioasm`` on the ``PATH`` bitweave prints ``[skip] ... not found: pioasm``. Put its directory on the ``PATH`` for the call (``PATH=/path/to/pioasm-dir:$PATH bitweave ...``) or add it to the ``bitweave.toml`` that ``build()`` wrote: ``[tools]`` ``bin_dirs = ["/path/to/pioasm-dir"]`` (the key is described in bitweave's guide; ``.bitweave-tools/bin`` next to the config is searched by default).

A pass means the program generated from your FSM behaves, cycle for cycle, like your FSM did in the Amaranth simulation. It says nothing about a chip: PIO timing on hardware also depends on the state machine clock and on the pad (see :doc:`testing <testing>`).

Other project kinds
-------------------

``project="pico-c"`` adds a Pico SDK C project (``CMakeLists.txt``, ``main.c``) and ``project="micropython"`` a MicroPython program. See :doc:`projects <projects>`.

From the command line
---------------------

::

   python -m pioweave list                                         # the example designs
   python -m pioweave build uart_tx build/ --project pico-c        # an example
   python -m pioweave build mydesigns:Blink build/ --chip rp2040   # your own module:Class

Where to go next: :doc:`designing for PIO <designing-for-pio>` for what the compiler accepts, :doc:`the data path <data-path>` for FIFOs and shifters, :doc:`boards <boards>` for real pin names.
