Study: a Python / Amaranth package for the PIO tooling
======================================================

Rename (owner, 2026-10-17, after this study): the packages amaranth-rp, amaranth-rp-builder and amaranth-rp-boards are now called pioweave, pioweave-builder and pioweave-boards (imports pioweave, pioweave.builder, pioweave.boards; the install component is ``pioweave``; the environment variable is PIOWEAVE_ALLOW_UNTESTED). The names were free on PyPI on 2026-10-17. The rest of this note keeps the old names.

File names in this study: the study was written before the code moved to this repository, so "today" and ``<bitweave>/python`` describe the old layout. The old names are now ``pioprog.py`` = ``pioweave/builder/program.py``, ``platform_rp.py`` = ``pioweave/platform.py``, ``pio_compile.py`` = ``pioweave/compile.py``, ``pio_project.py`` = ``pioweave/project.py``, ``pio_designs.py`` = ``pioweave/designs.py`` and ``<bitweave>/python`` = the root of this repository.

Question (owner, 2026-10-17): can the PIO tooling become a proper Python Amaranth module, and how would it be structured? This note records how Amaranth packages are built, three things "module" can mean, five proposals, and a recommendation. Decision (owner, 2026-10-17): P2 (two packages) with P4 (local install), named amaranth-rp-builder and amaranth-rp, no boards package now, ``bitweave install rp-pio`` allowed. The MCP server and the Rust flows keep the name Bitweave. Implemented 2026-10-17; the rest of this note is the study that led to it.

1. How Amaranth packages are structured (read from the installed 0.5.10 and from amaranth-boards 0.0.21)
--------------------------------------------------------------------------------------------------------

.. list-table::
   :header-rows: 1

   * - Fact
     - Where
   * - The core distribution ``amaranth`` contains the language (``amaranth.hdl``), the standard library (``amaranth.lib``), the build system (``amaranth.build``: ``Platform``, ``TemplatedPlatform``, ``Resource``, ``Pins``, ``BuildPlan``), one module per FPGA vendor (``amaranth.vendor``: ``XilinxPlatform``, ``LatticeICE40Platform``, ``GowinPlatform``, ... exported lazily with PEP 562) and the simulator (``amaranth.sim``).
     - ``amaranth-0.5.10.dist-info``, ``amaranth/vendor/__init__.py``
   * - There is no plugin registry for platforms. A third-party target is an ordinary Python package that imports ``amaranth`` and subclasses ``amaranth.build.Platform``. The only entry-point groups of the core are ``amaranth.lib.meta`` (component metadata) and one console script (``amaranth-rpc``).
     - ``entry_points.txt``
   * - ``amaranth-boards`` (board and connector definitions): distribution name ``amaranth-boards``, import name ``amaranth_boards``, one flat module per board (81 files), each defining a class that subclasses a vendor platform and lists ``resources`` built with helpers from ``amaranth_boards/resources``; it depends on ``amaranth<0.7,>=0.5`` and Python ``~=3.9``.
     - wheel metadata, ``icestick.py``
   * - Naming convention in the ecosystem: distribution ``amaranth-<thing>`` (hyphen), import ``amaranth_<thing>`` (underscore): ``amaranth-boards``, ``amaranth-stdio``, ``amaranth-soc``, ``amaranth-yosys``. Version policy: a range such as ``amaranth>=0.5,<0.7`` in ``Requires-Dist``.
     - PyPI metadata
   * - The core uses ``pdm-backend``-style PEP 621 metadata (``pyproject.toml``); any PEP 517 backend works for a third-party package.
     - [verify the backend of the core; not needed for a decision]

What this means for the PIO tooling: it is not an FPGA toolchain, so it does not belong in ``amaranth.vendor``; it is a third-party platform package in the style of ``amaranth-boards``. It reads Amaranth's intermediate representation (``amaranth.hdl._ast``, ``amaranth.hdl._ir``), which is not a public API, so the package must pin the Amaranth range it was tested with (today: 0.5.10) and keep that access in one module.

2. Three things "module" can mean here
--------------------------------------

1. **A platform module** (what exists): ``platform_rp.py``, a ``Platform`` subclass per chip. It is what ``amaranth-boards`` calls a platform; it is the right unit for "one module per target".
2. **A Python package** (installable with pip, importable from anywhere): the same code plus metadata, versioning and tests, so a project writes ``from bitweave_pio import RP2350BPioPlatform`` instead of adding paths.
3. **A component library** (``amaranth.lib.wiring`` components): reusable hardware blocks. The idea of a PIO-compatible state machine for an FPGA would be this kind of module; it is a different package from the one that writes PIO programs and is not part of this study.

3. Proposals
------------

P1: one package, ``bitweave-pio``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

   python/                       (today; becomes the package root)
     pyproject.toml              PEP 621; Requires-Dist: amaranth>=0.5.10,<0.6; Requires-Python >=3.9
     src/bitweave_pio/
       __init__.py               public API: Program, PioError, the platforms, compile_design, write_project
       program.py                the builder and its encoder (today pioprog.py)
       platform.py               RP2040PioPlatform ... RP2354BPioPlatform (today platform_rp.py)
       compile.py                FSM -> PIO instructions (today pio_compile.py)
       project.py                project writers + reference simulation (today pio_project.py)
       _compat.py                the only place that touches amaranth.hdl._ast / _ir; checks the Amaranth version at import
       __main__.py               python -m bitweave_pio build pkg.module:Design --chip rp2350b --project pico-c
     examples/                   pio_designs.py, pio_examples.py
     tests/  docs/

Pros: one thing to install and version. Cons: the plain builder (no Amaranth needed) drags the Amaranth dependency along.

P2: two packages in one repository (recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

   bitweave-pio-builder   (import bitweave_pio_builder)   pure Python, no dependencies: Program, encoder, pioasm text
   bitweave-pio           (import bitweave_pio)            depends on the builder and on amaranth: platforms, compiler, project writers

Pros: the builder is usable without Amaranth (scripts, MicroPython users, other tools) and is the part that can be checked alone against pioasm; the Amaranth package has a narrow job. Cons: two ``pyproject.toml`` files and two version numbers to keep in step (a monorepo with ``pip install -e python/builder -e python/amaranth`` handles that).

P3: add a boards package, ``bitweave-pio-boards``, like ``amaranth-boards``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

One module per board (``pico``, ``pico2``, ...) defining a platform class with the board's named resources (LED, buttons, headers) built with resource helpers. The chip platforms stay generic (``gpio`` resources). Needs facts per board (pin numbers, polarity); the owner has RP2040, RP2350 and RP2354 boards of unknown models, so this waits for the models [confirm]. Pin facts come from the board's published pinout or schematic and are tagged [verify] until measured.

P4: keep it inside the bitweave repository, install by path (recommended with P2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``pip install -e <bitweave>/python`` into any environment (bitweave's own ``.tools`` environment already has Amaranth 0.5.10). No publication. bitweave's ``install`` command could get a component that runs this install (a local path package is not on PyPI, so it is a deliberate addition to the catalogue [confirm]). The licence is proprietary (LICENSE), so publishing to PyPI is not planned and would need the owner's decision.

P5: contribute it to Amaranth
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Not suitable: ``amaranth.vendor`` holds FPGA toolchains; a PIO target is not one, and the compiler depends on private IR that upstream does not promise to keep. Rejected.

4. Naming
---------

Convention says ``amaranth-<thing>`` / ``amaranth_<thing>``; that prefix suggests an official package of the Amaranth project, so a name under our own prefix is clearer: ``bitweave-pio`` / ``bitweave_pio`` (builder: ``bitweave_pio_builder``). Alternative if the owner prefers the ecosystem look: ``amaranth-rp`` / ``amaranth_rp`` [confirm].

5. Technical points common to all proposals
-------------------------------------------

- Version gate: ``_compat.py`` imports the private IR names, checks ``amaranth.__version__`` against the tested range and fails with a clear message (the compiler's tests then cover each supported Amaranth version in a matrix [estimate: one extra environment per version]).
- Public entry points: the builder API and ``RP2040PioPlatform ... RP2354BPioPlatform``; ``Program`` stays free of Amaranth imports in P2.
- Command line: ``python -m bitweave_pio build DESIGN --chip ... --project ...`` replaces the two generator scripts and is what a bitweave ``generator = [...]`` calls.
- Tests move to ``tests/`` of each package; the co-simulation needs the bitweave binary and pioasm, and is skipped when they are missing (as today).
- Typing and docs: docstrings (no ``py.typed``: the code is not annotated), the two design notes as the manual.
- Licence and headers: as in LICENSE; no third-party code is copied (the grammar facts of pioasm and the datasheets are read as facts).

6. Migration steps and effort for P2 + P4 [estimate]
----------------------------------------------------

.. list-table::
   :header-rows: 1

   * - Step
     - Effort
   * - move files into ``src/`` layout, split builder / amaranth, add the two ``pyproject.toml``, fix imports
     - 0.5 day
   * - ``_compat.py`` with the version gate and a test that the gate refuses other versions
     - 0.5 day
   * - ``python -m bitweave_pio build ...`` command and bitweave examples switched to it
     - 0.5 day
   * - docs and tests rewired (``pip install -e``, no ``sys.path`` edits), run everything again
     - 0.5 day
   * - ``bitweave install`` component for the local package
     - 0.5 day [confirm]

7. Decisions needed from the owner
----------------------------------

1. P1 or P2 (recommended: P2).
2. Names: ``bitweave-pio`` or ``amaranth-rp``.
3. Whether boards (P3) are wanted now, and the board models.
4. Whether ``bitweave install`` may install the local package (P4).
5. Publication stays off unless the owner decides otherwise.
