Several state machines: blocks
==============================

A PIO block has four state machines that share one 32-word instruction memory, the eight IRQ flags and the GPIOs. Real programs often use two or more machines that handshake through the flags: a transmitter and a clock or baud generator, a receiver and a framer, a ring of stages. ``pioweave.block`` runs several single-machine designs as the machines of one block, simulates them together, and writes one project for them. The design note is section 2f of :doc:`design-notes/amaranth-pio`; the API reference is in :doc:`reference/block`.

.. code:: python

   from pioweave.block import Block, BlockSim, Machine
   from pioweave.block_designs import BaudTick, BaudUartTx
   from pioweave.platform import RP2040PioPlatform

   block = Block([Machine("baud", BaudTick), Machine("tx", BaudUartTx)], RP2040PioPlatform, sm_clock_hz=1_000_000, name="uart_baud")
   block.build("uart_baud", "build/uart_baud", project="pico-c", sim=BlockSim(cycles=120, tx={"tx": [0x41]}))

Each ``Machine(name, design)`` takes a design class (or a function returning a design): the same single-machine designs as in the rest of this documentation, with their own ``platform.shifter()``, scratch registers and pin requests. The order is the state machine number: the first machine is state machine 0.

.. testcode::

   from pioweave.block import Block, BlockSim, Machine, block_run, emulate_block
   from pioweave.block_designs import RingStage
   from pioweave.platform import RP2040PioPlatform

   ring = Block([Machine("stage%d" % k, lambda k=k: RingStage(k)) for k in range(4)], RP2040PioPlatform, name="ring")
   bc = ring.compile()
   print(bc.describe())
   sim = BlockSim(cycles=24, irq_set=((0, 0),))
   print(emulate_block(bc, sim)["pads"][1])
   print(block_run(ring, sim, bc)["pads"][1])

.. testoutput::

   sm0 stage0       offset  0,  4 instructions
   sm1 stage1       offset  4,  4 instructions
   sm2 stage2       offset  8,  4 instructions
   sm3 stage3       offset 12,  4 instructions
   000001100000000000000000
   000001100000000000000000

What is shared
--------------

.. list-table::
   :header-rows: 1

   * - Shared by the machines
     - In the reference simulation and the emulator
   * - IRQ flags
     - One set of eight flags. A flag that one machine sets and another clears in the same cycle ends up set. All machines see the flags as they were after the previous cycle, so a flag raised in cycle t is seen in cycle t + 1. The host raises or lowers flags with ``BlockSim(irq_set=..., irq_clear=...)``.
   * - Pins
     - One pad per GPIO. If several machines write a GPIO in the same cycle, the highest-numbered machine wins; a GPIO nobody writes keeps its level. A machine that reads a GPIO another machine drives sees its level from the previous cycle. GPIOs no machine drives come from ``BlockSim.stim``.
   * - Instruction memory
     - The programs are placed one after the other from address 0 in machine order; together at most 32 instructions. Every program keeps its own wrap range and side-set configuration.
   * - GPIO window
     - ``GPIOBASE`` belongs to the block: on the 48-GPIO parts all machines must fit one window of 32 GPIOs (GPIO 0 to 31 or 16 to 47).
   * - Clock
     - One state machine clock for the block (``sm_clock_hz``); fractional clock dividers per machine are not modelled.

Not shared: the FIFOs, the OSR and ISR, X and Y, the program counter, the side-set and the pin groups (SET, OUT, IN, JMP), which are the machine's own. Words in the TX FIFO, host writes and the draining of the RX FIFO are given per machine in ``BlockSim(tx={"name": ...}, tx_feed={"name": ...}, drain={"name": ...})``.

The generated project
---------------------

``project="pio"`` writes ``NAME.pio`` (one ``.program`` per machine, named ``NAME_MACHINE`` so that a machine called ``tx`` cannot collide with a pioasm keyword), ``bitweave.toml`` and ``README.md``. ``project="pico-c"`` adds ``CMakeLists.txt`` and ``main.c``, which claims state machine k for machine k, loads each program at its offset with ``pio_add_program_at_offset``, configures it (pins, shifts, FIFO join, clock divider), puts the TX words, sets the pin directions of the driven GPIOs, and starts all machines together with ``pio_enable_sm_mask_in_sync``. ``project="micropython"`` is refused for a block.

bitweave's simulator runs one state machine per case, so ``bitweave.toml`` has **one case per machine**: each case replays the machine's environment (the waveforms of the GPIOs it reads, and the IRQ flag changes made by the other machines and the host as ``irq_set`` / ``irq_clear`` events) and expects the machine's own output waveforms and the words it pushed in the Amaranth simulation of the whole block. The combined behaviour is checked against the pioweave emulator and the Amaranth simulation only.

What is refused
---------------

``Block`` and ``Block.compile`` raise ``BlockError`` (a ``PioCompileError``) with the reason for:

- more than four machines, no machine, a machine name that is not a C identifier or is used twice, a design instance instead of a class or function;
- programs that together are longer than 32 instructions (the message lists the length of each);
- machines that need different GPIO windows;
- ``irq ... rel`` in any machine: the flag number would depend on the state machine number, which the flag model does not track (write the flag number without ``rel``);
- a GPIO written by two machines where one of them writes it with an instruction that can stall (``wait``, blocking ``pull`` / ``push``, ``irq wait``, ``in`` with autopush, ``out`` with autopull): on the chip a side-set is written on the first cycle of a stall only, while the Amaranth state assigns the pin in every cycle, so the model of a shared pin would differ from the chip. A pin shared by instructions that do not stall (``set pins``, ``jmp`` with side-set, and so on) is allowed; the priority of the machine number decides;
- a ``BlockSim`` entry for a machine that does not exist;
- the MicroPython project.

Errors inside one machine's design are the usual ``PioCompileError`` messages, prefixed with ``machine NAME:``.

The examples
------------

``pioweave.block_designs.BLOCKS`` holds four examples, available from the command line (``python -m pioweave list``, ``build NAME OUTDIR``, ``emulate NAME``):

.. list-table::
   :header-rows: 1

   * - Block
     - Machines
   * - ``uart_baud``
     - a baud tick generator (raises flag 0 every 8 cycles and pulses GPIO 1 by side-set) and an 8N1 UART transmitter on GPIO 0 that waits for the tick before every bit
   * - ``handshake``
     - a producer that puts nibbles on GPIO 0 to 3 and raises REQ (flag 1), and a consumer that waits for REQ, reads the bus the other machine drives, raises ACK (flag 2) and pushes a byte for every two nibbles (autopush)
   * - ``ring``
     - four stages, stage k waits for flag k, pulses GPIO k for k + 1 cycles and raises flag k + 1 (mod 4); the host raises flag 0 before cycle 0
   * - ``shared_pin``
     - two machines that write GPIO 2 with different periods; the higher-numbered one wins when both write in the same cycle

``tests/example_blocks.json`` holds their instruction words, load addresses and configuration, so that a change of the compiler shows. ``examples/block`` is a runnable script with a baud generator and a UART on the Pico's named pins.

Nothing in this part has run on a chip. The hardware behaviour that the model follows (the priority of the pins, the order of set and clear of a flag, ``pio_enable_sm_mask_in_sync``) is taken from the datasheets as cited in the design note and from the emulator, which was written from the same datasheets; it is [verify] until it is tried on a board.
