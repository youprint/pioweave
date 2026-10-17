# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Amaranth platforms for the PIO blocks of the Raspberry Pi RP2040, RP2350 (A, B) and RP2354 (A, B) (design note docs/amaranth-pio.md).

Amaranth has one platform module per target (amaranth.build.Platform; the vendor modules are in amaranth.vendor). This is the one for the
PIO blocks of the RP chips. A design is an ordinary Amaranth Elaboratable written in a restricted style (one FSM; one FSM state is one PIO
instruction; pins come from `platform.request(name, n, dir="-")` wrapped in `amaranth.lib.io.Buffer`, or from `platform.buffer(m, "o", name, n)`; the
scratch registers X and Y from `platform.scratch()`). `platform.build(design, name,
build_dir, project=...)` compiles the FSM to PIO instructions (pio_compile.py) and writes the project the user asked for (pio_project.py):

    project="pio"          the PIO program and the bitweave simulation cases (expectations from the Amaranth simulation)
    project="pico-c"       a Pico SDK C project (CMakeLists.txt, main, the .pio file, bitweave.toml)
    project="micropython"  a MicroPython file using rp2.asm_pio, written by pioasm, plus the glue

Nothing generated here has run on a chip. Facts used: the RP2040 datasheet (chapter 3) and the RP2350 datasheet (chapter 11); GPIO numbers are
the pin names. The PIO block sees 32 GPIOs at a time. The RP2040 and the A variants (QFN-60) have GPIO 0 to 29; the B variants (QFN-80) have GPIO 0 to 47 and the PIO
window starts at GPIO 0 or 16 (GPIOBASE, RP2350 datasheet); the compiler picks the window from the pins the design uses. The A and B variants of the RP2350
and RP2354 are the same PIO, only the pinout differs (owner, 2026-10-17).
"""
from amaranth.build import Platform, Pins, Resource
from amaranth.build.run import BuildPlan
from amaranth.hdl import ClockDomain, Signal
from amaranth.lib import io


PIO_GPIOS = 30


class PinInfo:
    """A requested resource: its GPIO numbers (LSB first) and its Amaranth signals."""

    def __init__(self, name, number, gpios, direction, port):
        self.name, self.number, self.gpios, self.dir = name, number, gpios, direction
        self.port = port
        self.o = self.i = None     # the signals of the io.Buffer wrapped around the port
        self.role = None          # None: inferred by the compiler; or "set", "side"

    def __repr__(self):
        return "PinInfo(%s_%s gpio %s dir %s)" % (self.name, self.number, self.gpios, self.dir)


class _RPPioPlatform(Platform):
    chip = None
    pio_version = None
    gpio_count = 30
    gpio_bases = (0,)
    resources = []
    connectors = []
    required_tools = ["pioasm"]
    default_clk = None
    default_rst = None
    sdk_board = None          # Pico SDK board name (PICO_BOARD) of a board definition; None for a bare chip

    def __init__(self, sm_clock_hz=None):
        """`sm_clock_hz`: the state machine clock the C and MicroPython projects ask for (the PIO clock after the divider); None keeps the
        system clock (divider 1). One Amaranth clock cycle is one cycle of this clock."""
        super().__init__()
        self.sm_clock_hz = sm_clock_hz
        self.simulation = False      # True: request() returns io.SimulationPort objects, which the Amaranth simulator can drive (pio_project.amaranth_waves)
        self._pio_pins = []
        self._scratch = {}

    # ---- resources
    def request(self, name, number=0, *, dir="-", xdr=0):
        """Request a pin group as a port (Amaranth's current idiom: `dir="-"`); wrap it in `amaranth.lib.io.Buffer`, or use `buffer()` below."""
        if dir != "-" or xdr:
            raise ValueError('request pins with dir="-" and wrap the port in amaranth.lib.io.Buffer (the other forms are deprecated in Amaranth), or use platform.buffer()')
        res = self.lookup(name, number)
        gpios = []
        for ios in res.ios:
            gpios.extend(int(n) for n in ios.names)
        if self.simulation:
            port = io.SimulationPort("io", len(gpios), name="%s_%s" % (name, number))
        else:
            port = super().request(name, number, dir="-", xdr=0)
        self._pio_pins.append(PinInfo(name, number, gpios, res.ios[0].dir, port))
        return port

    def buffer(self, m, direction, name, number=0):
        """Request a pin group and add its `io.Buffer` to module `m`; returns the buffer (`.o` for outputs, `.i` for inputs)."""
        port = self.request(name, number)
        buf = io.Buffer(direction, port)
        m.submodules["%s_%s" % (name, number)] = buf
        self._bind(buf)
        return buf

    def _bind(self, buf):
        for p in self._pio_pins:
            if p.port is buf.port:
                p.o = getattr(buf, "o", None) if buf.direction.name in ("Output", "Bidir") else None
                p.i = getattr(buf, "i", None) if buf.direction.name in ("Input", "Bidir") else None
                return p
        return None

    def bind_buffers(self, fragment):
        """Find the io.Buffer submodules of an elaborated design and attach their signals to the requested pins."""
        bound = []
        for sf in fragment.subfragments:
            for origin in getattr(sf[0], "origins", None) or []:
                if isinstance(origin, io.Buffer):
                    info = self._bind(origin)
                    if info is None:
                        raise ValueError("an io.Buffer uses a port that was not requested from this platform")
                    bound.append(origin)
        return bound

    def pin_info(self, signal):
        """The PinInfo that owns `signal` (the `o` or `i` of its buffer), or None."""
        for p in self._pio_pins:
            if p.o is signal or p.i is signal:
                return p
        return None

    def pio_role(self, pin, role):
        """Force the PIO role of a requested output: "set" (set pins) or "side" (side-set). Default: inferred from how the design drives it."""
        if role not in ("set", "side"):
            raise ValueError("role must be 'set' or 'side'")
        for p in self._pio_pins:
            if p.port is getattr(pin, "port", None):
                p.role = role
                return
        raise ValueError("%r was not requested from this platform" % (pin,))

    def scratch(self, name):
        """The PIO scratch register X or Y as a 32-bit Amaranth signal (reset value 0, as in the chip)."""
        if name not in ("x", "y"):
            raise ValueError("the scratch registers are 'x' and 'y'")
        if name not in self._scratch:
            self._scratch[name] = Signal(32, name="pio_" + name)
        return self._scratch[name]

    def create_missing_domain(self, name):
        return ClockDomain(name, reset_less=True)

    # ---- build
    def build(self, elaboratable, name="top", build_dir="build", do_build=True, program_opts=None, do_program=False, **kwargs):
        """Same signature as amaranth.build.Platform.build. `project` selects what is written (pio, pico-c, micropython); `factory`, `cycles` and `stim`
        feed the reference simulation (see pio_project.py). Loading a board is not done here: bitweave does it (program --pico)."""
        if do_program:
            raise NotImplementedError("loading a board is done by bitweave (program --pico --yes), never by the platform")
        plan = self.prepare(elaboratable, name, **kwargs)
        if not do_build:
            return plan
        return plan.execute_local(build_dir)

    def prepare(self, elaboratable, name="top", project="pio", **kwargs):
        from .compile import compile_design
        from .project import write_project
        compiled = compile_design(elaboratable, self, name)
        plan = BuildPlan(script="build_" + name)
        write_project(plan, compiled, self, name, project, elaboratable=elaboratable, **kwargs)
        return plan

    def toolchain_prepare(self, fragment, name, **kwargs):     # prepare() above does not go through the generic path
        raise NotImplementedError("use platform.build(design, name, build_dir, project=...)")

    def toolchain_program(self, products, name, **kwargs):
        raise NotImplementedError("loading a board is done by bitweave (program --pico), never by the platform")


class RP2040PioPlatform(_RPPioPlatform):
    chip = "rp2040"
    pio_version = 0
    gpio_count = 30
    gpio_bases = (0,)
    resources = [Resource("gpio", n, Pins(str(n), dir="io")) for n in range(30)]


class RP2350PioPlatform(_RPPioPlatform):
    """The RP2350 with the 30-GPIO pinout of the A variant (the name without A or B is kept for it)."""
    chip = "rp2350"
    pio_version = 1
    gpio_count = 30
    gpio_bases = (0,)
    resources = [Resource("gpio", n, Pins(str(n), dir="io")) for n in range(30)]


class RP2350APioPlatform(RP2350PioPlatform):
    """RP2350A: QFN-60, 30 GPIOs (RP2350 datasheet, Table 1)."""
    chip = "rp2350a"


class RP2350BPioPlatform(RP2350PioPlatform):
    """RP2350B: QFN-80, 48 GPIOs. A PIO block still sees 32 GPIOs at a time: GPIOBASE 0 (GPIO 0-31) or 16 (GPIO 16-47), chosen by the compiler from the
    pins the design uses."""
    chip = "rp2350b"
    gpio_count = 48
    gpio_bases = (0, 16)
    resources = [Resource("gpio", n, Pins(str(n), dir="io")) for n in range(48)]


class RP2354PioPlatform(RP2350PioPlatform):
    """The RP2354 is an RP2350 with 2 MB of flash in the package (owner, 2026-10-17): the same PIO version 1 target."""
    chip = "rp2354"


class RP2354APioPlatform(RP2354PioPlatform):
    chip = "rp2354a"


class RP2354BPioPlatform(RP2350BPioPlatform):
    chip = "rp2354b"


PLATFORMS = {"rp2040": RP2040PioPlatform, "rp2350": RP2350PioPlatform, "rp2350a": RP2350APioPlatform, "rp2350b": RP2350BPioPlatform,
             "rp2354": RP2354PioPlatform, "rp2354a": RP2354APioPlatform, "rp2354b": RP2354BPioPlatform}


def gpio_resources(*groups):
    """Extra named resources over GPIO numbers, for example gpio_resources(("led", 0, "25"), ("bus", 0, "2 3 4"))."""
    return [Resource(n, i, Pins(g, dir="io")) for n, i, g in groups]
