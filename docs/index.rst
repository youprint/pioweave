pioweave
########

Write programs for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 in Python or in Amaranth, run them in an emulator, check them against the Amaranth simulation of your design, and get a ready project. Conventions: unverified facts carry ``[verify]``, owner decisions ``[confirm]``; nothing in this documentation claims a result on hardware unless it says so, and nothing has run on a chip yet.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   getting-started
   examples
   designing-for-pio
   data-path
   builder
   emulator
   hardware-testing
   boards
   projects
   compatibility
   testing
   releasing
   reference/index
   design-notes/README

.. toctree::
   :maxdepth: 1
   :caption: Design notes

   design-notes/pio-dsl
   design-notes/amaranth-pio
   design-notes/amaranth-package-study
   design-notes/amaranth-ir-study
   design-notes/emulator-comparison
