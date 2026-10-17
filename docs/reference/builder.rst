The PIO program builder
=======================

.. automodule:: pioweave.builder.program
   :members: Program, write_pio, PioError, CHIP_VERSION, MEMORY_WORDS

Operands
--------

The operands are constants of ``pioweave.builder``: the registers and destinations ``PINS``, ``X``, ``Y``, ``NULL``, ``ISR``, ``OSR``, ``PC``, ``EXEC``, ``PINDIRS``
and ``STATUS``; the jump conditions ``ALWAYS``, ``NOT_X``, ``X_DEC``, ``NOT_Y``, ``Y_DEC``, ``X_NE_Y``, ``PIN`` and ``NOT_OSRE``; the move operations ``NONE``,
``INVERT`` and ``REVERSE``; and the IRQ modes ``SET``, ``WAIT`` and ``CLEAR``. They compare and print by name.

Example programs
----------------

.. automodule:: pioweave.builder.examples
   :members:
