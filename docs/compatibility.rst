Compatibility with Amaranth
===========================

pioweave is a normal Amaranth package: a platform (``amaranth.build.Platform`` subclasses), designs written with ``amaranth.hdl`` and ``amaranth.lib.wiring``, pins through ``amaranth.lib.io.Buffer``, and the Amaranth simulator for the reference runs. It differs in one respect: the compiler reads the **statements** of the elaborated design, and Amaranth does not document an interface for that.

The API reference of this part, generated from the docstrings, is in :doc:`reference/compat`.

What the compiler reads
-----------------------

.. list-table::
   :header-rows: 1

   * - Need
     - Where it comes from
   * - ``Fragment`` and ``Fragment.get(obj, platform)``
     - ``amaranth.hdl`` (exported, but its attributes ``statements`` and ``subfragments`` are not documented)
   * - ``Assign``, ``Switch``, ``Operator``, ``Concat`` (the statement and expression classes)
     - the private module ``amaranth.hdl._ast`` (not exported by ``amaranth.hdl``; the old ``amaranth.hdl.ast`` shim is removed in 0.6)
   * - the names of the states of an FSM
     - ``Signal.decoder`` (0.5.x) or the signal's enum format object (0.6 development versions)
   * - the simulator clock period
     - ``Period(us=1)`` where ``amaranth.sim.Period`` exists (0.6), else seconds as a float

The imports are all behind ``pioweave/_compat.py``; nothing else imports Amaranth internals. The compiler (``compile.py``) and the block module (``block.py``) do read undocumented attributes of the objects Amaranth hands them (``Fragment.statements`` and ``subfragments``, ``Switch.cases``, the ``test`` and ``rhs`` of statements, ``operands``, ``origins``, ``src_loc``); a change to those breaks them with an ``AttributeError`` or, for the state names of an FSM, with ``PioCompileError: Amaranth internals changed``, which is why only tested versions are accepted. How this was investigated, and the alternatives that were considered, are in :doc:`design-notes/amaranth-ir-study`.

Supported versions
------------------

.. list-table::
   :header-rows: 1

   * - Amaranth
     - Status (2026-10-17)
   * - 0.5.1, 0.5.10
     - 0.5.10: the whole test suite passes (547 tests with ``PIO_COSIM_N=60``, with real Pico SDK builds); 0.5.1: the default run passes (367 tests, without the SDK builds)
   * - 0.5.2 to 0.5.9
     - the quick suite passes (106 tests plus the instruction words of all fifteen example designs); last run before the autopull, IRQ and ``wait pin`` support
   * - 0.6.0.dev140 (GitHub main, commit ``90449f1e6d5abdfb8cac7519ac7b34d83a9f42b7``)
     - the default run passes (367 tests, without the SDK builds)
   * - 0.5.0
     - refused: it has no ``io.SimulationPort``, which the reference simulation needs
   * - any other version
     - refused (see below)

There is **no 0.6 beta release**: PyPI has no pre-release and the newest upstream tag is ``v0.6.0.dev0``. The 0.6 development version is a moving target; a later commit may break the compiler, and one run of ``scripts/matrix.sh`` on that commit shows it.

The programs a design compiles to are identical on all of these versions: ``tests/example_programs.json`` holds the instruction words and the simulator configuration of the seventeen example designs, and a test compares them.

The version gate
----------------

``import pioweave`` checks the installed Amaranth against the tested versions and raises ``UnsupportedAmaranth`` with a clear message for anything else (0.4.x, 0.5.11, other 0.6 development versions). It reads the version from the package metadata of the installed ``amaranth`` distribution (``importlib.metadata``, in ``pioweave/_gate.py``), so it does not import Amaranth: ``import pioweave.builder`` and ``import pioweave.emulator`` still load no Amaranth. The modules that use Amaranth (the compiler, the project writers, the blocks; ``pioweave/_compat.py``) check ``amaranth.__version__`` again when they are imported. To try an untested version anyway, set ``PIOWEAVE_ALLOW_UNTESTED=1`` (exactly ``1``); it issues an ``UntestedAmaranthWarning``, so a run with ``-W error`` still stops. ``pip install`` additionally refuses versions outside ``amaranth>=0.5.1,<0.6`` through the package metadata; to use the development version install Amaranth first and pioweave with ``--no-deps``. ``pioweave.check_version(version, allow=False)`` returns whether a version is accepted.

The test matrix
---------------

::

   scripts/matrix.sh 0.5.1 0.5.10 git:90449f1e6d5abdfb8cac7519ac7b34d83a9f42b7      # the whole suite in a fresh environment per version
   MATRIX_QUICK=1 scripts/matrix.sh 0.5.2 0.5.3 0.5.4 ...                           # the quick suite
   PIO_BUILD_FIRMWARE=1 scripts/matrix.sh 0.5.10                                    # also build the generated Pico SDK projects

``git:COMMIT`` installs Amaranth from its repository at that commit. Run the matrix before a release and whenever Amaranth publishes a new version; add the version to ``_gate.py`` (``TESTED``, ``TESTED_DEV``) when it passes.

Standards followed and not followed
-----------------------------------

Followed: a ``Platform`` subclass per target with ``resources``, ``connectors``, ``required_tools``; ``build()`` with the standard signature; ``amaranth.lib.io.Buffer`` with ``request(..., dir="-")``; ``amaranth.lib.wiring`` components for designs and for the FIFO model; one board module per board; a flat package directory (no ``src/``), ``tests/`` and ``docs/``, as ``amaranth-soc`` and ``amaranth-boards`` have (no ``py.typed``: the code has no type annotations, and amaranth-soc ships no marker either); a PEP 621 ``pyproject.toml`` with the ``pdm-backend`` build backend; an Amaranth version range in the metadata.

Not followed: the compiler uses Amaranth internals (above); the platform's ``build()`` takes ``sim=SimOptions(...)`` and ``project=`` beyond the standard arguments; the package name does not carry the ``amaranth-`` prefix, which would suggest an official Amaranth package.
