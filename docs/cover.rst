pioweave
########

.. testsetup::

   import os, shutil, tempfile
   _olddir = os.getcwd()
   _tmpdir = tempfile.mkdtemp()
   os.chdir(_tmpdir)

.. testcleanup::

   os.chdir(_olddir)
   shutil.rmtree(_tmpdir)

Write programs for the PIO blocks of the Raspberry Pi RP2040, RP2350 and RP2354 in Python or in Amaranth, check them in simulation, and get a ready project.

PIO (programmable I/O) is a small state machine inside these chips, with its own instruction set of nine instructions, a 32-word program memory, two FIFOs and two shift registers. It is normally programmed in ``pioasm`` assembly. pioweave offers two other ways to write the same programs:

- **In Python**, with one method per instruction (``pioweave.builder``). Errors are raised when you build the program, not when the chip misbehaves.
- **In Amaranth**, as an ordinary finite state machine (``pioweave``). One FSM state is one PIO instruction, so the Amaranth simulation of your design *is* the reference for the program that is generated. The compiler refuses anything that would change the cycle behaviour; it never approximates.

- **In an emulator**: ``pioweave.emulator`` is a Python model of the PIO blocks (four state machines, shared instruction memory, FIFOs, IRQ flags, pins) that runs any program cycle by cycle, without a chip.

From either, pioweave writes a ``.pio`` file, and on request a Pico SDK C project or a MicroPython project, together with a test case for `bitweave <https://github.com/youprint/bitweave>`__ that checks the compiled program against the simulation of your design.

.. testcode::

   from amaranth.hdl import Module
   from pioweave import PioDesign, RP2040PioPlatform, SimOptions

   class UartTx(PioDesign):                       # 8N1 transmitter, two clock cycles per bit
       def elaborate(self, platform):
           m = Module()
           tx = platform.buffer(m, "o", "gpio", 0)
           sh = platform.shifter(m, out_shift="right")
           x = platform.scratch("x")
           with m.FSM():
               with m.State("PULL"):
                   m.d.sync += tx.o.eq(1)         # idle level
                   with sh.pull(m):               # pull block: stays while the TX FIFO is empty
                       m.next = "PULL_HOLD"
               with m.State("PULL_HOLD"):         # no effect: becomes the delay of the pull
                   m.next = "START"
               with m.State("START"):
                   m.d.sync += [tx.o.eq(0), x.eq(7)]
                   m.next = "BIT"
               with m.State("BIT"):
                   sh.out(m, tx)                  # out pins, 1
                   m.next = "LOOP"
               with m.State("LOOP"):
                   m.d.sync += x.eq(x - 1)
                   with m.If(x != 0):
                       m.next = "BIT"
                   with m.Else():
                       m.next = "PULL"
           return m

   factory = lambda: (UartTx(), RP2040PioPlatform(sm_clock_hz=16_000_000))
   design, platform = factory()
   platform.build(design, "uart_tx", "build/uart_tx", project="pico-c", sim=SimOptions(factory=factory, cycles=60, tx=[0x55, 0xC3]))

This compiles to

.. testcode::
   :hide:

   print(open("build/uart_tx/uart_tx.pio").read().replace(".pio_version 0\n", "").strip())

.. testoutput::

   .program uart_tx
   .side_set 1 opt
       pull block side 1 [1]
       set x, 7 side 0
   s_BIT:
       out pins, 1
       jmp x-- s_BIT

.. toctree::
   :maxdepth: 1

   index
