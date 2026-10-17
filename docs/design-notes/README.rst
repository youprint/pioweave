Design notes
============

The notes the code was written from. They record decisions, facts and verification as of 2026-10-17 and keep the names that were current when they were written (the packages were ``amaranth-rp``, ``amaranth-rp-builder`` and ``amaranth-rp-boards`` before they became the single package ``pioweave`` with ``pioweave.builder`` and ``pioweave.boards``; file names in the notes may be older). The user documentation is in ``docs/*.rst``.

.. list-table::
   :header-rows: 1

   * - Note
     - What
   * - :doc:`pio-dsl`
     - the Python builder: API, the rules found by comparing with ``pioasm``, the two independent encoders
   * - :doc:`amaranth-pio`
     - the Amaranth front end: what is accepted and refused, the data path, the 32-GPIO window, cycle alignment of the two simulators, project kinds, verification
   * - :doc:`amaranth-package-study`
     - how Amaranth packages are structured and the packaging options that were considered
   * - :doc:`amaranth-ir-study`
     - how much of Amaranth's internals the compiler depends on, tested against Amaranth 0.6.0.dev140
