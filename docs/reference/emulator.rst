The emulator
============

.. automodule:: pioweave.emulator
   :no-members:

The block and its state machines
--------------------------------

.. automodule:: pioweave.emulator.core
   :members: PioBlock, StateMachine, Fifo, Loaded, Config, EmulationError

Running a test case
-------------------

.. automodule:: pioweave.emulator.run
   :members:

Unit tests and waveforms
------------------------

.. automodule:: pioweave.emulator.trace
   :members: State, emulate, count_cycles, pin_history, waveform_text, plot_waveform

Checking a bitweave case
------------------------

.. automodule:: pioweave.emulator.check
   :members: check_case, main
