"""Tests of amaranth_rp_boards: the pins against the Pico SDK board headers (when an SDK checkout is found), the resources, and a build that uses a named pin."""
import json
import os
import re

import pytest
from amaranth.hdl import Elaboratable, Module

from amaranth_rp import compile_design
from amaranth_rp_boards.pico import PicoPlatform
from amaranth_rp_boards.pico2 import Pico2Platform
from amaranth_rp_boards.waveshare_core2350b import WaveshareCore2350BPlatform

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SDK = os.environ.get("PICO_SDK_PATH") or os.path.join(REPO, ".tools", "src", "pico-sdk")
BOARDS = [(PicoPlatform, "pico", "rp2040", 30), (Pico2Platform, "pico2", "rp2350a", 30), (WaveshareCore2350BPlatform, "waveshare_core2350b", "rp2350b", 48)]


def header(board):
    path = os.path.join(SDK, "src", "boards", "include", "boards", board + ".h")
    if not os.path.exists(path):
        pytest.skip("no Pico SDK checkout (set PICO_SDK_PATH)")
    with open(path) as fh:
        return {m.group(1): m.group(2).strip() for m in re.finditer(r"^#define\s+(\w+)\s+(.+?)\s*(?://.*)?$", fh.read(), re.M)}


def gpios(platform, name):
    return [int(n) for ios in platform.lookup(name, 0).ios for n in ios.names]


@pytest.mark.parametrize("cls,board,chip,count", BOARDS)
def test_pins_equal_the_sdk_header(cls, board, chip, count):
    h = header(board)
    p = cls()
    assert p.sdk_board == board and p.chip == chip
    want = {"led": [h["PICO_DEFAULT_LED_PIN"]], "uart_tx": [h["PICO_DEFAULT_UART_TX_PIN"]], "uart_rx": [h["PICO_DEFAULT_UART_RX_PIN"]],
            "i2c_sda": [h["PICO_DEFAULT_I2C_SDA_PIN"]], "i2c_scl": [h["PICO_DEFAULT_I2C_SCL_PIN"]], "spi_sck": [h["PICO_DEFAULT_SPI_SCK_PIN"]],
            "spi_tx": [h["PICO_DEFAULT_SPI_TX_PIN"]], "spi_rx": [h["PICO_DEFAULT_SPI_RX_PIN"]], "spi_csn": [h["PICO_DEFAULT_SPI_CSN_PIN"]]}
    for name, pins in want.items():
        assert gpios(p, name) == [int(x) for x in pins], name
    assert h.get("PICO_RP2350A", "1") == ("0" if chip == "rp2350b" else "1") or chip == "rp2040"


@pytest.mark.parametrize("cls,board,chip,count", BOARDS)
def test_resources_and_chip_gpios(cls, board, chip, count):
    p = cls()
    assert gpios(p, "gpio") == [0] and gpios(p, "gpio") is not None
    assert len([r for r in cls.resources if r.name == "gpio"]) == count
    assert {r.name for r in cls.resources} == {"gpio", "led", "uart_tx", "uart_rx", "i2c_sda", "i2c_scl", "spi_sck", "spi_tx", "spi_rx", "spi_csn"}


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


@pytest.mark.parametrize("cls,board,chip,count", BOARDS)
def test_a_named_pin_compiles_and_the_board_reaches_the_project(cls, board, chip, count, tmp_path):
    p = cls()
    led = gpios(p, "led")[0]
    c = compile_design(Blink(), cls(), "blink")
    cfg = c.sim_config()
    assert cfg["set_base"] + cfg.get("gpio_base", 0) == led, cfg     # the pin is relative to the GPIO window the program uses
    out = tmp_path / "p"
    cls().build(Blink(), "blink", str(out), project="pico-c")
    cfg = (out / "bitweave.toml").read_text()
    assert 'board = "%s"' % board in cfg and 'chip = "%s"' % chip in cfg, cfg
