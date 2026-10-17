"""Tests of pioweave.boards: the pins against the Pico SDK board headers (when an SDK checkout is found), the resources, and a build that uses a named pin."""
import json
import os
import re
import unittest
from amaranth.hdl import Elaboratable, Module

from . import support
from pioweave import compile_design
from pioweave.boards.pico import PicoPlatform
from pioweave.boards.pico2 import Pico2Platform
from pioweave.boards.waveshare_core2350b import WaveshareCore2350BPlatform
from pioweave.boards.weact_studio_rp2350b_core import WeActStudioRP2350BCorePlatform

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("BITWEAVE_REPO") or os.path.abspath(os.path.join(HERE, "..", "..", "bitweave"))     # a bitweave checkout beside this repository
SDK = os.environ.get("PICO_SDK_PATH") or os.path.join(REPO, ".tools", "src", "pico-sdk")
BOARDS = [(PicoPlatform, "pico", "rp2040", 30), (Pico2Platform, "pico2", "rp2350a", 30), (WaveshareCore2350BPlatform, "waveshare_core2350b", "rp2350b", 48),
          (WeActStudioRP2350BCorePlatform, "weact_studio_rp2350b_core", "rp2350b", 48)]


def header(board):
    path = os.path.join(SDK, "src", "boards", "include", "boards", board + ".h")
    if not os.path.exists(path):
        raise unittest.SkipTest("no Pico SDK checkout (set PICO_SDK_PATH)")
    with open(path) as fh:
        return {m.group(1): m.group(2).strip() for m in re.finditer(r"^#define\s+(\w+)\s+(.+?)\s*(?://.*)?$", fh.read(), re.M)}


def gpios(platform, name):
    return [int(n) for ios in platform.lookup(name, 0).ios for n in ios.names]


@support.cases
class BoardPinTests(support.TestCase):
    @support.parametrize("cls,board,chip,count", BOARDS, ids=[b[1] for b in BOARDS])
    def test_pins_equal_the_sdk_header(self, cls, board, chip, count):
        h = header(board)
        p = cls()
        self.assertTrue(p.sdk_board == board and p.chip == chip)
        want = {"led": [h["PICO_DEFAULT_LED_PIN"]], "uart_tx": [h["PICO_DEFAULT_UART_TX_PIN"]], "uart_rx": [h["PICO_DEFAULT_UART_RX_PIN"]],
                "i2c_sda": [h["PICO_DEFAULT_I2C_SDA_PIN"]], "i2c_scl": [h["PICO_DEFAULT_I2C_SCL_PIN"]], "spi_sck": [h["PICO_DEFAULT_SPI_SCK_PIN"]],
                "spi_tx": [h["PICO_DEFAULT_SPI_TX_PIN"]], "spi_rx": [h["PICO_DEFAULT_SPI_RX_PIN"]], "spi_csn": [h["PICO_DEFAULT_SPI_CSN_PIN"]]}
        if "WEACT_STUDIO_RP2350B_USER_SW_PIN" in h:
            want["button"] = [h["WEACT_STUDIO_RP2350B_USER_SW_PIN"]]
        for name, pins in want.items():
            self.assertEqual(gpios(p, name), [int(x) for x in pins], name)
        self.assertTrue(h.get("PICO_RP2350A", "1") == ("0" if chip == "rp2350b" else "1") or chip == "rp2040")

    @support.parametrize("cls,board,chip,count", BOARDS, ids=[b[1] for b in BOARDS])
    def test_resources_and_chip_gpios(self, cls, board, chip, count):
        p = cls()
        self.assertTrue(gpios(p, "gpio") == [0] and gpios(p, "gpio") is not None)
        self.assertEqual(len([r for r in cls.resources if r.name == "gpio"]), count)
        names = {"gpio", "led", "uart_tx", "uart_rx", "i2c_sda", "i2c_scl", "spi_sck", "spi_tx", "spi_rx", "spi_csn"}
        self.assertEqual({r.name for r in cls.resources}, names | ({"button"} if board.startswith("weact") else set()))


class Blink(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        led = platform.buffer(m, "o", "led", 0)
        with m.FSM():
            with m.State("ON"):
                m.d.sync += led.o.eq(1)
                m.next = "OFF"
            with m.State("OFF"):
                m.d.sync += led.o.eq(0)
                m.next = "ON"
        return m


@support.cases
class BoardTests(support.TestCase):
    @support.parametrize("cls,board,chip,count", BOARDS, ids=[b[1] for b in BOARDS])
    def test_a_named_pin_compiles_and_the_board_reaches_the_project(self, cls, board, chip, count):
        p = cls()
        led = gpios(p, "led")[0]
        c = compile_design(Blink(), cls(), "blink")
        cfg = c.sim_config()
        self.assertEqual(cfg["set_base"] + cfg.get("gpio_base", 0), led, cfg)     # the pin is relative to the GPIO window the program uses
        out = self.tmp_path / "p"
        cls().build(Blink(), "blink", str(out), project="pico-c")
        cfg = (out / "bitweave.toml").read_text()
        self.assertTrue('board = "%s"' % board in cfg and 'chip = "%s"' % chip in cfg, cfg)

    def test_weact_board_matches_its_schematic(self):
        """RP2350B_SCH.pdf (WeAct Studio RP2350B V1.0, read 2026-10-17): the blue LED on GPIO 25, the user key on GPIO 23, and the secondary flash / PSRAM select on GPIO 0."""
        p = WeActStudioRP2350BCorePlatform()
        self.assertTrue(gpios(p, "led") == [25] and gpios(p, "button") == [23])
        self.assertEqual(len([r for r in WeActStudioRP2350BCorePlatform.resources if r.name == "gpio"]), 48)
        h = header("weact_studio_rp2350b_core")
        self.assertTrue(h["WEACT_STUDIO_RP2350B_PSRAM_CS_PIN"] == "0" and h["PICO_DEFAULT_LED_PIN"] == "25")
        self.assertIn("GPIO 0 is the chip select", WeActStudioRP2350BCorePlatform.__doc__)

    def test_waveshare_board_matches_its_schematic(self):
        """Core2350B.pdf (Waveshare, read 2026-10-17): the red LED on GPIO 39, the PSRAM chip select on GPIO 47, no user key."""
        p = WaveshareCore2350BPlatform()
        self.assertTrue(gpios(p, "led") == [39] and "button" not in {r.name for r in WaveshareCore2350BPlatform.resources})
        h = header("waveshare_core2350b")
        self.assertEqual(h["WAVESHARE_CORE2350B_PSRAM_CS_PIN"], "47")
        self.assertIn("GPIO 47 is the PSRAM chip select", WaveshareCore2350BPlatform.__doc__)
