Testing pioweave programs and the emulator on real boards (a plan)
===================================================================

**This is a plan. Nothing in it has been run on a chip or a board.** No step has a result, and no document of this package claims one. The plan says how the owner's own equipment can check the emulator (:doc:`emulator`) and the programs that pioweave produces, what each step proves, and which questions this equipment cannot answer. Facts that are not read from a document carry ``[verify]``; owner decisions carry ``[confirm]``.

Boards and equipment
--------------------

.. list-table::
   :header-rows: 1

   * - Item
     - What is known
   * - Raspberry Pi Pico
     - RP2040 (PIO version 0), LED on GPIO 25 (:doc:`boards`)
   * - WeAct Studio RP2350B core
     - RP2350B (PIO version 1, 48 GPIOs), blue LED on GPIO 25, key on GPIO 23, GPIO 0 is the chip select of the secondary flash or PSRAM while R14 is fitted
   * - Waveshare Core2350B
     - RP2350B, red LED on GPIO 39, GPIO 47 is the PSRAM chip select
   * - multimeter
     - the owner's; type and accuracy unknown ``[confirm]``
   * - oscilloscope
     - the owner's; bandwidth unknown ``[confirm]``; step HW-02 estimates it
   * - four WeAct DLA Mini logic analysers
     - 8 channels each, 24 MHz maximum sample rate (owner); driven by bitweave through ``sigrok-cli``; the sigrok driver name (``fx2lafw``) and the input voltage range of the DLA Mini are ``[verify]``
   * - host
     - the PC with pioweave, bitweave and sigrok-cli; loading with ``bitweave program --load FILE.uf2 --yes --pico`` (picotool; not tested on hardware: bitweave guide), or BOOTSEL and copying the UF2 by hand

What the analyser can and cannot resolve
----------------------------------------

The analyser samples every 1 / 24 MHz = 41.7 ns. bitweave judges a waveform cycle at its middle sample and asks for at least 4 samples per cycle (bitweave guide, "Logic analyser"), so the state machine clock to check a waveform at is at most 24 MHz / 4 = 6 MHz. The system clock is far faster than that (125 MHz is the default of the Pico SDK on the RP2040 and 150 MHz on the RP2350 ``[verify]``): the sampled waveform shows state machine cycles at 1 to 6 MHz, never single system clock cycles of 8 ns or 6.7 ns.

.. list-table::
   :header-rows: 1

   * - State machine clock
     - Samples per cycle (24 MHz / clock)
     - Divider from a 125 MHz system clock (125 MHz / clock)
     - Use
   * - 1 MHz
     - 24
     - 125
     - waveform comparison, duty and frequency
   * - 4 MHz
     - 6
     - 31.25
     - edge timing, 1-cycle pulses
   * - 6 MHz
     - 4
     - 20.8333
     - the limit; cycles are judged at one sample each

To see single system clock cycles (the output delay, the input synchroniser, the fractional divider jitter) the plan slows the **system clock** instead: setting it to the 12 MHz crystal clock ``[verify]`` (the clock API names are in the installed Pico SDK ``[verify]``; the generated ``main.c`` does not do this, so the step edits it) makes one system cycle 83.3 ns = 2 samples. The 12 MHz crystal of the Pico is documented by Raspberry Pi; the crystals of the two RP2350B boards are ``[verify]`` on their schematics.

The analyser's own time base is a crystal of unknown accuracy ``[verify]``. Absolute frequencies are therefore compared with a tolerance of 2 % (the bitweave default) and ratios between two signals of the same capture are compared much tighter, because both use the same time base.

Each step names its **instrument** and its **pass criterion**. The ``logic`` checks are those of ``bitweave logic NAME`` (``[logic.NAME]`` with ``waveform``, ``measure`` and ``decode``); a capture is also kept as a ``.sr`` file for re-checking with ``bitweave logic NAME --from FILE``.

Steps
-----

Preparation
~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - ID
     - Step
     - Instrument
     - Pass criterion
   * - HW-00
     - ``python -m pioweave doctor``; ``bitweave install sigrok --yes``; ``sigrok-cli --scan`` with one analyser attached
     - the PC
     - the analyser is listed and ``sigrok-cli`` can open it (it may need a udev rule or root); otherwise stop and record the driver message
   * - HW-01
     - supply: with a board on USB, measure the 3.3 V rail (Pico pin 36 ``3V3(OUT)``; the pin of the two RP2350B boards from their pinouts ``[verify]``) against GND
     - multimeter
     - 3.3 V +/- 0.1 V ``[confirm]``; a board that fails is not used for the later steps
   * - HW-02
     - estimate the oscilloscope bandwidth: a PIO program toggles a pin every cycle at a system-clock divider that gives 1, 5, 10, 20 MHz; read the amplitude on the scope
     - oscilloscope, PIO program (``pioweave.designs.SquareWave`` at a chosen ``sm_clock_hz``)
     - the frequency at which the displayed amplitude falls to 70 % of the 1 MHz amplitude is a rough upper bound of the usable bandwidth; the value is an estimate ``[estimate]`` (a square wave also contains harmonics) and is written down in this document
   * - HW-03
     - analyser check: a PIO square wave at 1 MHz on a GPIO, probe on D0 and ground connected; ``[logic.sq]`` with ``measure freq_hz = 1000000`` and ``duty_pct = 50``
     - analyser, then the oscilloscope on the same pin to cross-check
     - frequency within 2 %, duty within 2 points; if the oscilloscope frequency differs from the analyser's by more than 2 % the analyser time base is not trusted for absolute values

Programs against the emulator
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - ID
     - Step
     - Instrument
     - Pass criterion
   * - HW-10
     - build each example design that needs no input (``square_wave``, ``pulse_train``, ``clocked_bits`` and the others from ``python -m pioweave list``) as a ``pico-c`` project at ``sm_clock_hz`` 1 MHz, load it, capture the output GPIO(s) at 24 MHz; ``[[logic.NAME.wave]]`` with ``pio = ..., case = "wave", gpio = N, cycle_hz = 1000000, align = "edge", search_cycles = 2``
     - analyser; the expectation is the emulator's and the Amaranth simulation's waveform from the generated bitweave case
     - 0 mismatching cycles (``max_mismatch = 0``) over the captured window; a shift by up to 2 cycles is accepted because the start of the capture is not synchronised with the state machine
   * - HW-11
     - the same for the designs with inputs (``uart_rx``, ``pin_branch``, ``button_pulse``): a second state machine on the same chip, wired by a jumper to the input pin, plays the input waveform (a ``pins_in`` pattern of the case), or the key of the board where it fits (WeAct GPIO 23)
     - analyser on both wires
     - the output waveform equals the emulator's for the same input; the jumper wire is the only extra hardware
   * - HW-12
     - delays and side-set: a program whose pulse widths are 1, 2, 3, 5 and 8 state machine cycles (``set pins`` with delays, side-set), at 4 MHz
     - analyser, ``[[logic.NAME.measure]] pulses = ...`` and the pulse widths from the VCD
     - every width equals N cycles of 250 ns within one sample (41.7 ns) plus the time base error (2 %)
   * - HW-13
     - PUSH/PULL, autopull and autopush, thresholds: the host writes a known word list; the program shifts it out (``out pins, n`` with autopull at thresholds 8, 16, 32) as a bit stream with a clock
     - analyser with ``decode`` (the SPI decoder of sigrok, ``protocol = "spi"`` ``[verify]`` for the decoder name and options)
     - the decoded bytes equal the written words in order
   * - HW-14
     - UART TX example against a decoder: ``uart_tx`` at 115200 baud
     - analyser, ``[[logic.uart.decode]] protocol = "uart"``
     - decoded bytes equal the transmitted bytes (``expect_bytes``)
   * - HW-15
     - the full set at once: the differential test of the emulator already agrees with bitweave's simulator; here the same random cases (``tests/emu_random.py``, output pins only) are run on the chip and compared with the emulator. Choose cases without input waveforms and without undefined behaviour (those where the emulator and the simulator do not stop); at most 16 cases per session
     - analyser (8 channels per analyser; use up to 4 analysers when more pins are needed)
     - the captured pad levels equal the emulator's ``Result.wave`` for each case, with the same tolerance as HW-10

Facts the datasheets state, checked on the chip
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

These steps need single system clock cycles to be visible, so they run with the system clock slowed to 12 MHz (see above); one system cycle is 2 analyser samples. Each says which emulator test states the same fact.

.. list-table::
   :header-rows: 1

   * - ID
     - Question
     - Step
     - Pass criterion
   * - HW-20
     - priority between state machines writing one pin (RP2040 3.5.6.1; ``test_per_pin_priority_highest_state_machine_levels_and_directions_apart``)
     - SM0 and SM1 both ``set pins`` the same GPIO in the same cycle, values 1 and 0, started together with one write of the CTRL register (the SDK has a call to enable several state machines in sync ``[verify]``); then SM1 writes 1 and SM0 0
     - the pin follows SM1 (the highest-numbered) in both cases
   * - HW-21
     - side-set beats SET on the same pin (RP2040 3.5.1)
     - ``set pins, 1 side 0`` on one pin, side-set and SET mapped to it
     - the pin is 0
   * - HW-22
     - the input synchroniser: 2 system cycles of latency, none with INPUT_SYNC_BYPASS set (RP2040 3.5.6.3; ``test_synchroniser_is_two_flops_and_bypassed_per_gpio``)
     - SM0 toggles pin A; a jumper joins A to B; SM1 ``wait 1 pin`` on B then ``set`` pin C; measure A rising to C rising, with the bypass bit for B cleared and then set (INPUT_SYNC_BYPASS is a register of the block; how the Pico SDK sets it is ``[verify]`` in the installed SDK)
     - the latency with the bypass cleared exceeds the one with the bypass set by 2 system cycles = 167 ns = 4 samples (+/- 1 sample); the absolute numbers are compared with the emulator's (``sync_cycles`` 2 and 0, ``loopback="datasheet"``)
   * - HW-23
     - the output-to-input loop of one state machine: 2 cycles with the bypass, 4 with the synchroniser (RP2040 3.5.6.1; ``test_own_outputs_seen_after_two_or_four_cycles_in_datasheet_mode``)
     - a program sets a pin and ``jmp pin`` back-to-back on the same pin, then copies the observed level to a second pin; count the cycles between the two pin edges
     - the count equals the emulator's with ``loopback="datasheet"`` (2 and 4); on the RP2350 the datasheet does not state it: record the measured value, it settles the ``[verify]`` in :doc:`emulator`
   * - HW-24
     - the fractional divider period pattern (RP2040 3.5.5; ``test_fractional_divider_extends_some_periods_by_one``)
     - divider 2.5 at the 12 MHz system clock, the program toggles a pin every state machine cycle; read the half-periods from the capture
     - the half-periods alternate 2 and 3 system cycles (167 ns and 250 ns); which of them comes first after enabling is ``[verify]`` in :doc:`emulator`: record it. At 125 MHz the 8 ns steps are below the analyser's resolution: check only the mean (HW-25)
   * - HW-25
     - the mean fractional rate at full speed
     - two state machines of the same block with dividers 2.5 and 2.0 toggling two pins; compare the two frequencies in one capture
     - the ratio is 1.25 within 0.2 % (both use the analyser's time base)
   * - HW-26
     - forced instructions ignore delay, divider and enable (RP2040 3.5.7)
     - the host writes SMx_INSTR with a ``set pins`` that carries a delay of 9 while the state machine is disabled and with a divider of 1000
     - the pin changes immediately (at the next system cycle, 83 ns at 12 MHz) and the next program instruction is not delayed
   * - HW-27
     - OUT EXEC and MOV EXEC (delay of the OUT ignored, the executee's own delay applies)
     - the host sends an instruction word in the TX FIFO; the program ``out exec, 16 [3]``; the executee sets a pin with delay 1
     - from the OUT to the pin edge 1 state machine cycle; the pin pulse and the following instruction as the emulator's trace says
   * - HW-28
     - IRQ flags: set wins over clear in the same cycle; ``irq wait`` stalls until cleared (RP2350 11.2.8)
     - SM0 ``irq set 1`` and SM1 ``irq clear 1`` in the same cycle, then SM2 ``wait 1 irq 1`` followed by a pin write; the flag is read from the pin write
     - the pin write occurs (the flag stayed set)
   * - HW-29
     - FIFO joins and the RX FIFO registers (RP2350 11.4.8, 11.4.9)
     - on the RP2350B boards: ``mov rxfifo[...], isr`` and ``mov osr, rxfifo[...]`` in a program that outputs the OSR to pins; the host reads the registers through the PUTGET registers (a Pico SDK program; the access function names are ``[verify]``)
     - values written by the state machine are read by the host and the reverse, as the emulator says; the PUSH on the joined FIFO stalls forever
   * - HW-30
     - OUT_STICKY and INLINE_OUT_EN (RP2040 Table 382)
     - SM0 writes a pin every cycle, SM1 has OUT_STICKY set and writes once; with INLINE_OUT_EN the enable bit deasserts
     - settles the ``[verify]`` in :doc:`emulator`: record the pin, then change the emulator if it differs

The 48-GPIO window
~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - ID
     - Step
     - Instrument
     - Pass criterion
   * - HW-40
     - ``high_pins`` (rp2350b) on the WeAct and Waveshare boards: the output pins in GPIO 32 to 47 that the board brings out ``[verify]`` against the board pinout; with GPIOBASE 16
     - analyser on the brought-out pins; GPIO 47 (Waveshare) and GPIO 0 (WeAct) stay free of the analyser load because they select the memory
     - the waveform equals the emulator's with ``gpio_base=16``
   * - HW-41
     - the same on the Pico (RP2040): ``high_pins`` must be refused by the compiler before any hardware step
     - the PC
     - the compiler raises its error (already a test); no board involved
   * - HW-42
     - the ``uart_baud`` block (:doc:`blocks`) built with ``project="pico-c"``: state machine 0 raises IRQ flag 0 every 8 cycles, state machine 1 sends one bit per tick; the board's UART pin and the tick pin (GPIO 1) on the analyser
     - analyser at a rate of at least 4 times the state machine clock (`bitweave logic`), 1 MHz state machine clock
     - the frame decodes as the TX word (0x41) and the tick period is 8 state machine clock cycles
   * - HW-43
     - the ``shared_pin`` block: the two machines write GPIO 2 in the same cycle with different values (the machines start together with ``pio_enable_sm_mask_in_sync`` ``[verify]``)
     - analyser on GPIO 2
     - the pad follows the higher-numbered machine in the cycles both write, as the simulation shows; the same as HW-20 for programs generated by ``Block``
   * - HW-44
     - a flag that one machine sets and another clears in the same cycle (two ``IrqEveryOther`` machines of ``tests/test_block.py``): ``mov x, status`` with ``status="irq"`` on RP2350, or a third machine that waits on the flag
     - the pins of the third machine
     - the flag stays set (RP2350 datasheet 11.2.7 as cited in the design note ``[verify]``)

What this equipment cannot verify
---------------------------------

.. list-table::
   :header-rows: 1

   * - Question
     - Why it cannot be answered
     - Cheapest alternative
   * - single system clock cycles at 125 MHz or 150 MHz
     - 24 MHz sampling; the scope bandwidth is unknown
     - run the system clock at 12 MHz (above); or a scope whose measured bandwidth (HW-02) is at least 5 times the toggling frequency
   * - the jitter of the fractional divider at full speed
     - 8 ns steps against 41.7 ns samples
     - HW-24 at the slow system clock, and the mean at full speed (HW-25)
   * - rise time, overshoot, slew rate of the pads
     - the analyser is digital and the scope bandwidth is unknown
     - a scope measurement only if HW-02 gives a bandwidth of at least 5 times 0.35 / rise time ``[estimate]``; otherwise not measured
   * - input threshold, hysteresis, pull resistors
     - not modelled by the emulator and not testable without a variable source
     - a multimeter reading of the idle level with the internal pull-up on and nothing attached (about the supply for a pull-up): a rough check only
   * - metastability of the synchroniser
     - rare, statistical
     - not tested; the emulator does not model it
   * - the emulator on the RP2350 output-to-input delay
     - the datasheet does not state it
     - HW-23 on an RP2350B board, recorded as a measurement
   * - jitter or drift between the two PIO blocks of the RP2350 (IRQ NEXT / PREV)
     - needs two synchronised blocks and a scope
     - HW-28 with the SDK call that restarts the clock dividers in sync ``[verify]``; check only the functional result

Recording
---------

For every step keep the commit of pioweave, the bitweave version, the board, the system clock, the state machine clock, the capture file (``.sr``), the ``bitweave logic`` record, and the date. A step is *passed* only when the criterion above holds on the capture; a step that was not run is *not run*. A failed step is a finding about either the emulator, the program or the equipment: record it in :doc:`emulator` (the list of ``[verify]`` items) and change the emulator only when the datasheet agrees with the measurement.

A configuration sketch for HW-10 (not run; the driver name and the pin names are ``[verify]``)::

   [logic.sq]
   driver = "fx2lafw"
   samplerate = "24m"
   samples = 240000
   [logic.sq.channels]
   led = "D0"
   [[logic.sq.measure]]
   signal = "led"
   freq_hz = 250000
   [[logic.sq.wave]]
   signal = "led"
   pio = "square_wave"
   case = "wave"
   gpio = 0
   cycle_hz = 1000000
   align = "edge"
   search_cycles = 2
