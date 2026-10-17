# amaranth-rp-boards

Board definitions for amaranth-rp, one module per board like amaranth-boards: named pins for the LED, UART, I2C and SPI of the Raspberry Pi Pico (`pico`), Pico 2
(`pico2`) and Waveshare Core2350B (`waveshare_core2350b`, an RP2350B board). The chip's own `gpio` resources stay available. Pins come from the Pico SDK board
headers (read 2026-10-17); the LED on GP25 and the flash sizes (Pico 2 MB, Pico 2 4 MB) are also stated on raspberrypi.com/documentation/microcontrollers/pico-series.html (read 2026-10-17, which gives no default UART/I2C/SPI pins); the board name is passed on as `board = "..."` in the generated `[firmware.NAME]` section, so a Pico SDK build uses the right header
(the B part needs one with `PICO_RP2350A 0`).

    from amaranth_rp_boards.pico import PicoPlatform
    platform = PicoPlatform(sm_clock_hz=1_000_000)
    led = platform.buffer(m, "o", "led", 0)          # inside elaborate(); GPIO 25

Not included: the Pico W and Pico 2 W (the LED is on the wireless chip, not on a GPIO). Further boards are added by copying one module and the header's numbers.
Nothing here has run on a board.
