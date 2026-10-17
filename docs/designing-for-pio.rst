Designing for PIO in Amaranth
=============================

A PIO state machine executes one instruction per clock cycle, and its program counter is its state. A design written as one Amaranth FSM in which every state does what one PIO instruction does is therefore cycle-equivalent to the compiled program. pioweave compiles such an FSM and **refuses** (with the source line of your design) everything that would change the cycle behaviour; it never approximates.

The shape of a design
---------------------

- Derive from ``pioweave.PioDesign`` (an ``amaranth.lib.wiring.Component`` with no ports) or from any ``Elaboratable``.
- One module, one ``m.FSM()`` in the ``sync`` domain, no other logic: no ``m.d.comb`` statements of your own, no other clock domains, no extra submodules (only the pin buffers and the FIFO model, flags and IRQ model of ``platform.shifter()``).
- The first state you define is PIO address 0 (the program order is the order of the ``m.State`` blocks; Amaranth numbers states by first mention, but pioweave uses the order they are defined).
- At most 32 instructions (the program memory of one PIO block).
- Pins: ``platform.buffer(m, "o", "gpio", 3)`` gives an output group on GPIO 3 (``pin.o``), ``"i"`` an input (``pin.i``). ``platform.request(...)`` plus ``amaranth.lib.io.Buffer`` does the same; request with ``dir="-"``. Board definitions add named groups (``"led"``, ``"button"``, see :doc:`boards <boards>`).
- Registers: ``x = platform.scratch("x")`` and ``"y"`` are the 32-bit scratch registers. They reset to 0 as in the chip: never give them another reset value.

What one state may do, and the instruction it becomes
-----------------------------------------------------

.. list-table::
   :header-rows: 1

   * - In the state
     - PIO instruction
   * - ``pin.o.eq(C)`` on an output pin group, nothing else
     - ``set pins, C`` (the group is the ``set`` group: consecutive GPIOs, at most 5 bits)
   * - ``pin.o.eq(C)`` together with another operation, a test, a jump or a wait
     - side-set (the group, at most 4 bits; optional side-set, so a state that does not assign it leaves the pins alone, as in Amaranth)
   * - ``x.eq(C)`` with C in 0..31
     - ``set x, C`` (also ``y``)
   * - ``x.eq(y)``, ``x.eq(~y)``, ``x.eq(~x)``
     - ``mov x, y``, ``mov x, !y``, ``mov x, !x``
   * - ``x.eq(x - 1)`` and ``m.If(x != 0)`` selecting the jump
     - ``jmp x-- LABEL`` (decrements in both branches, like the instruction)
   * - ``x.eq(x - 1)`` and nothing else, going to the next state
     - ``jmp x-- NEXT``
   * - ``m.If(x == 0)``, ``y == 0``, ``x != y`` selecting a jump (the other branch is the next state)
     - ``jmp !x``, ``jmp !y``, ``jmp x!=y``
   * - ``m.If(pin.i)`` selecting a jump
     - ``jmp pin LABEL`` (one JMP pin per program)
   * - a state that stays until ``m.If(pin.i)`` or ``m.If(~pin.i)`` lets it go to the next state
     - ``wait 1 gpio N`` / ``wait 0 gpio N``
   * - the same with ``bus.i[k]`` on an input group of more than one pin
     - ``wait 1 pin k`` / ``wait 0 pin k`` (relative to the IN base)
   * - ``m.next = OTHER`` and nothing but side-set
     - ``jmp OTHER``
   * - FIFO and shifter operations
     - ``pull``, ``push``, ``out``, ``in``, ``mov`` with the OSR / ISR / pins / status, ``jmp !osre``, ``irq`` and ``wait irq``: see :doc:`the data path <data-path>`
   * - a state without any effect, after an instruction, that nothing jumps to
     - folded into the delay ``[n]`` of that instruction
   * - the last state going to a state other than the first, with a ``set`` / ``mov`` / data operation
     - ``.wrap_target`` at that state

Jumps take their layout from the next-state rule: PIO can jump on ``!x``, ``x--``, ``!y``, ``y--``, ``x!=y`` and on a pin that is high, so the compiler takes, of the two branches of your test, the one that is not the next state as the jump and negates the test when needed. A branch state cannot also carry a delay (the taken path would pay it too), so only ``set``, ``mov``, ``nop``, ``wait`` and data states take folded delays.

Delays
------

The instruction word has five bits shared by the delay and the side-set: ``[n]`` can be at most ``31`` with no side-set, and ``31 >> (side bits + 1)`` with an optional side-set. Longer waits become ``nop`` instructions. In the FSM a delay is just states with no effect: five hold states after a ``set`` are ``set ... [4]`` when nothing jumps to them.

Side-set and pin roles
----------------------

A pin group assigned alone in a state is a ``set`` pin; assigned in a state that also does something else it is a **side-set** pin. A program has at most one ``set`` group and one side-set group. When the automatic choice is wrong, force it: ``platform.pio_role(pin, "set")`` or ``"side"``. A pin used both ways is in both groups. The PIO applies side-set in the first cycle of the instruction, even when the instruction stalls: assign the side-set value *before* a blocking ``with sh.pull(m):`` so it applies in the waiting cycles too.

The 32-GPIO window
------------------

A PIO block sees 32 GPIOs at a time. On the 48-GPIO parts (RP2350B, RP2354B) pioweave picks the GPIO base (0 or 16) whose window holds every pin the program uses, and refuses programs whose pins do not fit one window (for example GPIO 5 and GPIO 40). The generated C project calls ``pio_set_gpio_base``.

What is refused
---------------

Each of these raises ``PioCompileError`` naming the Amaranth source line:

- more than one operation in a state (it would need two cycles: put them in separate states);
- compound tests (``&``, ``|``), ``Elif`` chains, tests on anything but ``x``, ``y``, an input pin, one bit of an input group (as a wait) or a flag of the data path (the FIFO flags, ``sh.osr_not_empty``), comparisons with constants other than 0, ``x == y`` as a jump condition, a jump on a low pin, a jump on one bit of an input group;
- pin or register assignments that are not a constant, the other register, its complement, itself minus 1 (or the helper calls of the data path); assignments to parts of a signal;
- ``m.Switch`` on multi-bit values; a decrement in only one branch; a decrement without the test that needs it;
- registers or pins with a non-zero reset value;
- a helper that can stall written as a plain call (``out`` with autopull, ``in`` with autopush, a blocking ``pull`` / ``push``, ``irq`` ``wait``, ``wait_irq``: use ``with``), and a stalling state that does not continue with the next state;
- ``jmp !osre`` written as a jump while the OSR is *empty* (PIO jumps while it is not); ``out`` / ``mov`` to ``pc`` or ``exec``, to ``pindirs`` named by the string instead of a direction register, and ``mov pindirs`` on PIO version 0 (``ValueError`` with the reason when the design is written); ``mov`` from pins on PIO version 0;
- more than one IN group (``in pins``, ``mov ..., pins`` and ``wait pin`` must name the same one), more than one OUT group;
- two ``set`` groups or two side-set groups (``set pins`` and ``set pindirs`` count as one SET group; a side-set that writes levels and one that writes directions cannot be in one program); a direction register assigned anything but a constant, tested, or assigned in a state that jumps elsewhere; pin groups that are not consecutive GPIOs; other clock domains; more than 32 instructions.

Not built yet in the Amaranth front end (the destinations ``pc`` and ``exec`` of ``out`` and ``mov`` are refused on purpose, see the data path page; pin directions are described there too): ``irq`` with ``prev`` / ``next`` (other PIO blocks), the RP2350 joins that turn the RX FIFO into registers, several state machines. The Python :doc:`builder <builder>` has every instruction.

Timing between the two simulators
---------------------------------

The Amaranth simulation and bitweave's PIO simulator are aligned as follows: the FSM state in cycle *t* does what the instruction executed in cycle *t* does; outputs are registers, so the value sampled after the *t*-th clock edge is the pad level of cycle *t*; inputs set before the edge of cycle *t* are what the instruction of cycle *t* reads. The 2-flop input synchroniser of the chip is off in the model, so on a real pin an input reaches the program two cycles later [verify on a chip].
