# amaranth-rp-boards

Board definitions for amaranth-rp, one module per board like amaranth-boards: named pins for the LED, UART, I2C and SPI of the Raspberry Pi Pico (`pico`), Pico 2
(`pico2`), Waveshare Core2350B (`waveshare_core2350b`) and WeAct Studio RP2350B core (`weact_studio_rp2350b_core`; both RP2350B boards, 48 GPIOs, which also have a `button` where the board has one). The chip's own `gpio` resources stay available. Pins come from the Pico SDK board
headers (read 2026-10-17); the LED on GP25 and the flash sizes (Pico 2 MB, Pico 2 4 MB) are also stated on raspberrypi.com/documentation/microcontrollers/pico-series.html (read 2026-10-17, which gives no default UART/I2C/SPI pins); the board name is passed on as `board = "..."` in the generated `[firmware.NAME]` section, so a Pico SDK build uses the right header
(the B part needs one with `PICO_RP2350A 0`).

    from amaranth_rp_boards.pico import PicoPlatform
    platform = PicoPlatform(sm_clock_hz=1_000_000)
    led = platform.buffer(m, "o", "led", 0)          # inside elaborate(); GPIO 25

The Waveshare Core2350B page (waveshare.com/core2350b.htm, read 2026-10-17) confirms RP2350B, 48 GPIOs and 16 MB flash; the LED GPIO 39 and the default UART/I2C/SPI pins come from the SDK header only [verify on the board].

The WeAct Studio RP2350B V1.0 schematic (RP2350B_SCH.pdf, supplied by the owner, read 2026-10-17; not stored in the repository) shows the blue LED on GPIO 25, the user key on GPIO 23 and the
secondary flash / PSRAM chip select on GPIO 0 (through R14, 0 ohm). It has no default UART, I2C or SPI pins: those come from the Pico SDK header (`weact_studio_rp2350b_core.h`).

Owner's boards (2026-10-17): the Raspberry Pi Pico (RP2040 target), the WeAct Studio RP2350B core and the Waveshare Core2350B.

Not included: the Pico W and Pico 2 W (the LED is on the wireless chip, not on a GPIO). Further boards are added by copying one module and the header's numbers.
Nothing here has run on a board.
