Releasing
=========

The steps to cut a release of pioweave. Nothing here has been run as a release yet: no wheel has been published and the repository is private.

Version and tag
---------------

The version comes from the git tag (``pdm-backend`` with ``source = "scm"`` in ``pyproject.toml``, as in amaranth-soc): there is no version string to edit. A commit that is not on a tag gets a development version (``0.1.1.dev3+g1234abc`` and so on), and ``pioweave.__version__`` and ``python -m pioweave --version`` show what the installed metadata says (``unknown`` when running from a source tree that is not installed).

::

   git tag -a v0.1.0 -m "pioweave 0.1.0"
   git push origin v0.1.0

A tag ``v0.1.0`` already exists in this repository and points at an early commit (546e094, "Ignore pdm build artefacts"), not at the current state. Before releasing 0.1.0 decide whether to move it (``git tag -f``, then force-push the tag: only if nothing was built from it) or to release the current state as 0.1.1 [confirm]. ``pdm_build.py`` points the ``Documentation`` URL at the tag for a release and at ``main`` for a development version.

Before a release
----------------

Run these in this order, in a clean checkout of the commit to be tagged. The tools for the co-simulation tests are described in :doc:`testing <testing>`; without them those tests are skipped, not passed, and a release needs them.

1. The full test suite, with warnings as errors, with the tools::

      PIOASM=/path/to/pioasm BITWEAVE=/path/to/bitweave PYTHONWARNINGS=error python -m unittest discover -t . -s tests

   It takes about 11 minutes with the tools. ``PIO_BUILD_FIRMWARE=1`` additionally builds the generated Pico SDK projects (needs the SDK and ``arm-none-eabi-gcc``).
2. The Amaranth version matrix (needs network; see :doc:`compatibility <compatibility>`)::

      scripts/matrix.sh 0.5.1 0.5.10 git:90449f1e6d5abdfb8cac7519ac7b34d83a9f42b7
      MATRIX_QUICK=1 scripts/matrix.sh 0.5.2 0.5.3 0.5.4 0.5.5 0.5.6 0.5.7 0.5.8 0.5.9

   Every version ends with one line ``result VERSION: OK``, ``result VERSION: FAILED (...)`` (it cannot be installed, or the tests fail) or ``result VERSION: refused (...)`` (the version gate refuses it); the exit status is 0 only when every version is OK, 1 otherwise and 2 when no version was given. The script installs each Amaranth version into a fresh virtual environment, installs pioweave with ``--no-deps`` (so that pip does not refuse a version outside the declared range) and prints what the version gate says when it refuses. The commit in ``TESTED_DEV`` in ``pioweave/_gate.py`` is the development version the suite was last run on.
3. The documentation, which must build without warnings and whose examples must produce the printed output::

      sphinx-build -W docs docs/_build
      sphinx-build -W -b doctest docs docs/_build/doctest

4. The runnable examples (:doc:`examples <examples>`) are part of the suite (``tests/test_examples.py``); run ``python -m pioweave doctor`` too.
5. Update ``CHANGELOG.rst``: move the entries from the unreleased heading to the version and the date, and check that the numbers in README.md and the documentation (test counts, versions) are the numbers of this run.
6. Check the supported Amaranth range in four places: ``pyproject.toml`` (``dependencies``), ``pioweave/_gate.py`` (``TESTED``, ``TESTED_DEV``), ``.github/workflows/main.yaml`` and ``docs/compatibility.rst``. They must say the same.

The version gate
----------------

``import pioweave`` raises ``UnsupportedAmaranth`` (from the metadata of the installed Amaranth; the compiler modules check ``amaranth.__version__`` again) for an Amaranth version outside the tested set (see :doc:`compatibility <compatibility>`). When a new Amaranth release appears: run ``scripts/matrix.sh`` with it, and only if the suite passes add it to ``TESTED`` (or to ``TESTED_DEV`` for a development commit), to the dependency range, the CI matrix and the documentation. Never widen the range without a run. ``PIOWEAVE_ALLOW_UNTESTED=1`` lets a user try an untested version; the error message says so.

Building the wheel and the source distribution
----------------------------------------------

::

   pip install build            # or: pdm build
   python -m build              # writes dist/pioweave-VERSION-py3-none-any.whl and dist/pioweave-VERSION.tar.gz

``pyproject.toml`` uses ``pdm-backend`` (``requires = ["pdm-backend>=2.4"]``); ``includes = ["pioweave"]`` puts the package in the wheel and ``source-includes`` adds ``tests``, ``docs``, ``examples``, ``CHANGELOG.rst``, ``LICENSE.txt``, ``CONTRIBUTING.txt`` and ``README.md`` to the sdist. Build from the tagged commit with the tag fetched (``git fetch --tags``; CI uses ``fetch-depth: 0``), otherwise the version is wrong. The version comes from the git tag. In a tree without ``.git`` (an exported archive) set it yourself, ``PDM_BUILD_SCM_VERSION=0.1.0 python -m build`` (an environment variable pdm-backend reads); without it the build uses the placeholder ``0.0.0.dev0+nogit`` from ``fallback_version`` in ``pyproject.toml``, which says in the file name that it is not a release. Building from the sdist reads the version from its ``PKG-INFO``. The licence is MIT (``LICENSE.txt``); the package no longer carries the ``Private :: Do Not Upload`` classifier, so an index would accept an upload. After the build:

- check that the version in the file names is the tag's, not a ``.dev`` version;
- install the wheel into a fresh virtual environment (``pip install dist/*.whl``) and run ``python -m pioweave --version``, ``python -m pioweave doctor`` and ``python -m pioweave list``;
- unpack the sdist and run the quick suite there::

     python -m unittest tests.test_refusals tests.test_program tests.test_boards tests.test_emulator tests.test_emulator_amaranth

Publishing
----------

The repository is private and no package index upload has been done. ``twine`` or ``pdm publish`` would be the usual tools; both need an account and a token, which this project does not hold in the repository. Publishing to PyPI makes the code public, whatever the licence says; that is the owner's decision [confirm before the first upload].

The licence
-----------

The package is under the MIT licence, decided by the owner on 2026-10-18 (``LICENSE.txt``, ``license = "MIT"`` and ``license-files`` in ``pyproject.toml``, the copyright line ``Copyright (c) 2026 youprint. MIT licence`` at the top of every source file). The copyright holder is the name ``youprint``; change it in ``LICENSE.txt`` and the headers together if a legal name is wanted.
