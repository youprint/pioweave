# Board definitions (`pioweave.boards`)

One module per board, as `amaranth-boards` does: `from pioweave.boards.pico import PicoPlatform`. A board class is a chip platform (`RP2040PioPlatform`, `RP2350APioPlatform`, `RP2350BPioPlatform`) plus the named pins of
the board, so a design can ask for `"led"` instead of a GPIO number. The chip's own `gpio` resources stay available next to them.

```python
from amaranth.hdl import Module
from pioweave import PioDesign, SimOptions
from pioweave.boards.pico import PicoPlatform

class Blink(PioDesign):
    def elaborate(self, platform):
        m = Module()
        led = platform.buffer(m, "o", "led", 0)       # GPIO 25 on a Pico
        with m.FSM():
            with m.State("ON"):
                m.d.sync += led.o.eq(1)
                m.next = "OFF"
            with m.State("OFF"):
                m.d.sync += led.o.eq(0)
                m.next = "ON"
        return m

factory = lambda: (Blink(), PicoPlatform(sm_clock_hz=1_000_000))
design, platform = factory()
platform.build(design, "blink", "build/blink", project="pico-c", sim=SimOptions(factory=factory, cycles=16))
```

## The boards

| Module / class | Chip | Named pins | Notes |
|---|---|---|---|
| `pico` / `PicoPlatform` | RP2040, 2 MB flash | `led` GPIO 25; `uart_tx` 0, `uart_rx` 1; `i2c_sda` 4, `i2c_scl` 5; `spi_sck` 18, `spi_tx` 19, `spi_rx` 16, `spi_csn` 17 | the RP2040 target of the project owner |
| `pico2` / `Pico2Platform` | RP2350A, 4 MB flash | the same pins as the Pico | |
| `waveshare_core2350b` / `WaveshareCore2350BPlatform` | RP2350B (QFN-80, 48 GPIOs), 16 MB flash | `led` GPIO 39 (red, active high); UART 0/1, I2C 4/5, SPI 18/19/16/17 | GPIO 47 is the PSRAM chip select; BOOTSEL is the only key |
| `weact_studio_rp2350b_core` / `WeActStudioRP2350BCorePlatform` | RP2350B (QFN-80, 48 GPIOs), 16 MB flash | `led` GPIO 25 (blue, active high); `button` GPIO 23 (to ground, pulled up); UART 12/13, I2C 8/9, SPI 18/19/16/17 | GPIO 0 is the chip select of the secondary flash / PSRAM while resistor R14 is fitted |

Every resource is a single pin group (`name`, number 0); `led` and `button` follow the `amaranth-boards` names. A board also sets `sdk_board`, which `build()` writes as `board = "pico"` (and so on) into the
generated `[firmware.NAME]` section so that a Pico SDK build uses the right board header; the RP2350B parts need a header with `PICO_RP2350A 0`.

## Where the pins come from

| Fact | Source (all read 2026-10-17) |
|---|---|
| all default pins (LED, UART, I2C, SPI), flash size, PSRAM select | the Pico SDK board headers `src/boards/include/boards/pico.h`, `pico2.h`, `waveshare_core2350b.h`, `weact_studio_rp2350b_core.h` (the tests compare the classes with the headers when an SDK checkout is found) |
| Pico and Pico 2: LED on GP25, flash 2 MB and 4 MB | raspberrypi.com/documentation/microcontrollers/pico-series.html (which gives no default UART, I2C or SPI pins) |
| Waveshare Core2350B: RP2350B, 48 GPIOs, 16 MB flash | waveshare.com/core2350b.htm |
| Waveshare Core2350B: red LED on GPIO 39 (470 ohm), PSRAM select on GPIO 47, BOOTSEL as the only key | the vendor schematic, files.waveshare.com/wiki/Core2350B0/Core2350B.pdf |
| WeAct Studio RP2350B V1.0: blue LED on GPIO 25, key on GPIO 23, secondary flash / PSRAM select on GPIO 0 through R14 (0 ohm) | the vendor schematic RP2350B_SCH.pdf (supplied by the owner; not stored in this repository) |

The two vendor schematics draw no default UART, I2C or SPI pins; those come from the SDK headers only. Nothing here has run on a board: treat every pin as [verify on the board]. The chip select of the PSRAM
(GPIO 47 on the Waveshare, GPIO 0 on the WeAct) should stay free in a PIO program unless you know the PSRAM is not used. The PSRAM part number is not stated in either schematic (the SDK header of the WeAct
board says 8 MB; the Waveshare header detects the size at run time).

Not included: the Pico W and Pico 2 W, whose LED is on the wireless chip, not on a GPIO.

## Adding a board

Copy one module, change the class name, `sdk_board` and the numbers from the board's SDK header or schematic, and add the board to `tests/test_boards.py` (which compares the pins with the SDK header and
compiles a blink design on the board's LED pin).

```python
from pioweave import RP2040PioPlatform
from pioweave.boards.resources import sdk_resources

class MyBoardPlatform(RP2040PioPlatform):
    sdk_board = "my_board"                       # the board's name in the Pico SDK
    resources = sdk_resources(30, led=16, button=15, uart=(0, 1), i2c=(4, 5), spi=(18, 19, 16, 17))
```
