Testing a PIO program in the emulator
=====================================

A ``unittest`` file for a program written with the builder: ``run_case`` runs it in the emulator, the tests check the pin levels cycle by cycle and count cycles with ``Result.executed``, ``stall_cycles`` and ``delay_cycles``.

::

   python -m unittest examples/emulator_test/test_pulse.py -v     # from the repository root
   cd examples/emulator_test && python -m unittest -v

Copy the file, replace ``pulse_program`` with your program and its configuration (``set_base``, ``out_base``, ``sideset_base``, ``autopull`` and so on) and adapt the expected values. The emulator is a model, not a chip: a passing test says the program does what the datasheet describes, not that it ran on a board.
