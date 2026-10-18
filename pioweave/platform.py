# Copyright (c) 2026 youprint. MIT licence (see ../LICENSE.txt)
"""Amaranth platforms for the PIO blocks of the Raspberry Pi RP2040, RP2350 (A, B) and RP2354 (A, B) (design note docs/design-notes/amaranth-pio.rst).

Amaranth has one platform module per target (amaranth.build.Platform; the vendor modules are in amaranth.vendor). This is the one for the
PIO blocks of the RP chips. A design is an ordinary Amaranth Elaboratable written in a restricted style (one FSM; one FSM state is one PIO
instruction; pins come from `platform.request(name, n, dir="-")` wrapped in `amaranth.lib.io.Buffer`, or from `platform.buffer(m, "o", name, n)`; the
scratch registers X and Y from `platform.scratch()`). `platform.build(design, name,
build_dir, project=...)` compiles the FSM to PIO instructions (pioweave.compile) and writes the project the user asked for (pioweave.project):

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

__all__ = [
    "PIO_GPIOS", "PinInfo", "RP2040PioPlatform", "RP2350PioPlatform", "RP2350APioPlatform", "RP2350BPioPlatform", "RP2354PioPlatform", "RP2354APioPlatform",
    "RP2354BPioPlatform", "PLATFORMS", "gpio_resources", "DirPin",
]

PIO_GPIOS = 30


class PinInfo:
    """A requested resource: its GPIO numbers (LSB first) and its Amaranth signals.

    Parameters
    ----------
    name : str
        The resource name.
    number : int
        The resource number.
    gpios : list of int
        The GPIO numbers of the pins, least significant bit first.
    direction : str
        The direction declared by the resource (``"i"``, ``"o"``, ``"io"``).
    port : amaranth.lib.io.PortLike
        The port returned by ``request``.

    Attributes
    ----------
    name, number, gpios, dir, port :
        The arguments as given (`direction` is `dir`).
    o, i : amaranth.hdl.Signal or None
        The output and input signals of the `io.Buffer` wrapped around the port; bound by ``buffer`` or ``bind_buffers``.
    role : str or None
        None when the compiler infers how the pin is driven; ``"set"`` or ``"side"`` when forced with ``pio_role``.
    dirs : amaranth.hdl.Signal or None
        The direction register of the group (bit 1 = output), created by ``platform.directions``; None for a group whose direction is fixed by the host.
    dirs_init : int
        The direction the host configures before the state machine starts (the reset value of `dirs`).
    """

    def __init__(self, name, number, gpios, direction, port):
        self.name, self.number, self.gpios, self.dir = name, number, gpios, direction
        self.port = port
        self.o = self.i = None     # the signals of the io.Buffer wrapped around the port
        self.role = None          # None: inferred by the compiler; or "set", "side"
        self.dirs = None          # the direction register (platform.directions), None: fixed by the host
        self.dirs_init = 0
        self.dir_pin = None       # the DirPin the compiler sees assignments to `dirs` through

    def __repr__(self):
        return "PinInfo(%s_%s gpio %s dir %s)" % (self.name, self.number, self.gpios, self.dir)


class DirPin:
    """The direction register of a pin group as the compiler sees an assignment to it: it looks like an output pin (``o`` is the register) of the same GPIOs.

    Attributes
    ----------
    info : PinInfo
        The pin group the register belongs to.
    o : amaranth.hdl.Signal
        The direction register (one bit per pin, 1 = output).
    gpios, name, number :
        As the group's.
    """

    def __init__(self, info):
        self.info, self.o, self.gpios, self.name, self.number = info, info.dirs, info.gpios, info.name, info.number
        self.role = None

    def __repr__(self):
        return "DirPin(%s_%s gpio %s)" % (self.name, self.number, self.gpios)


class _RPPioPlatform(Platform):
    """The common base of the PIO platforms: a pin group, scratch register and data path interface for one PIO state machine, and the build entry point.

    A design is elaborated with a platform of this kind as its `platform` argument; `build` compiles the FSM and writes the project.

    Parameters
    ----------
    sm_clock_hz : int, float or None
        The state machine clock the C and MicroPython projects ask for (the PIO clock after the divider); None keeps the system clock (divider 1). One Amaranth
        clock cycle is one cycle of this clock.

    Attributes
    ----------
    chip : str
        The chip name (``"rp2040"``, ``"rp2350"``, ``"rp2350a"``, ``"rp2350b"``, ``"rp2354"``, ``"rp2354a"`` or ``"rp2354b"``).
    pio_version : int
        The PIO version: 0 or 1.
    gpio_count : int
        The number of GPIOs of the package.
    gpio_bases : tuple of int
        The GPIO numbers that PIO pin 0 can map to (0, and 16 on the 48-GPIO parts).
    sdk_board : str or None
        The Pico SDK board name (``PICO_BOARD``) of a board definition; None for a bare chip.
    sm_clock_hz : int, float or None
        The state machine clock as given.
    simulation : bool
        True makes ``request`` return ``io.SimulationPort`` objects, which the Amaranth simulator can drive (set by `pioweave.project.amaranth_run`).
    tx_init : list of int
        Words already in the TX FIFO at cycle 0 (the reference simulation sets it; real hardware gets them from the host).
    """
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
    irq_hub = None            # set by pioweave.block.Block: the IRQ flags shared by the state machines of a block; None for a design with one state machine
    sm_index = 0              # the state machine number (0 to 3); set by pioweave.block.Block

    def __init__(self, sm_clock_hz=None):
        super().__init__()
        self.sm_clock_hz = sm_clock_hz
        self.simulation = False      # True: request() returns io.SimulationPort objects, which the Amaranth simulator can drive (pioweave.project.amaranth_run)
        self._pio_pins = []
        self._scratch = {}
        self._shifter = None
        self.tx_init = []        # words already in the TX FIFO at cycle 0 (the reference simulation sets it; real hardware gets them from the host)

    # ---- resources
    def request(self, name, number=0, *, dir="-", xdr=0):
        """Request a pin group as a port (Amaranth's current idiom: ``dir="-"``); wrap it in ``amaranth.lib.io.Buffer``, or use `buffer` below.

        Parameters
        ----------
        name : str
            The resource name, for example ``"gpio"``.
        number : int
            The resource number.
        dir : str
            Must be ``"-"``.
        xdr : int
            Must be 0.

        Returns
        -------
        amaranth.lib.io.PortLike
            The port (an ``io.SimulationPort`` when `simulation` is true).

        Raises
        ------
        ValueError
            If `dir` is not ``"-"`` or `xdr` is not 0.
        """
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
        """Request a pin group and add its ``io.Buffer`` to module `m`.

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module the buffer is added to, as a submodule named ``NAME_NUMBER``.
        direction : str
            ``"i"``, ``"o"`` or ``"io"``, as for ``amaranth.lib.io.Buffer``.
        name : str
            The resource name.
        number : int
            The resource number.

        Returns
        -------
        amaranth.lib.io.Buffer
            The buffer (``.o`` for outputs, ``.i`` for inputs).
        """
        port = self.request(name, number)
        buf = io.Buffer(direction, port)
        m.submodules["%s_%s" % (name, number)] = buf
        self._bind(buf)
        return buf

    def directions(self, pin, init=0):
        """The direction register of a pin group: a signal with one bit per pin (1 = output), so that the design can change the directions while it runs (design note 2f).

        Without this call a group has the direction the host configures and keeps it. With it the program can change the directions: ``sh.out(m, d, n)`` is ``out pindirs, n``,
        ``sh.mov(m, d, src)`` is ``mov pindirs, src`` (PIO version 1), and ``m.d.sync += d.eq(C)`` alone in a state is ``set pindirs, C`` (with other work in the state, or in a
        state with a test, it is the side-set when the program's side-set drives pin directions). The pad of a pin that is an input is not driven by ``pin.o``: it follows the
        outside world (the simulation takes it from the stimulus), and ``pin.i`` of a bidirectional buffer reads the pad.

        Parameters
        ----------
        pin : amaranth.lib.io.Buffer
            A buffer returned by `buffer` with the direction ``"o"`` or ``"io"``.
        init : int
            The directions the host configures before the state machine starts (also the reset value of the register); 0 to ``2**width - 1``.

        Returns
        -------
        amaranth.hdl.Signal
            The register; the same signal on every call for the same group.

        Raises
        ------
        ValueError
            If the buffer is not an output or bidirectional buffer of this platform, `init` does not fit the group, or the group already has a register with another `init`.
        """
        info = next((p for p in self._pio_pins if p.port is getattr(pin, "port", None)), None)
        if info is None:
            raise ValueError("%r was not requested from this platform" % (pin,))
        if getattr(getattr(pin, "direction", None), "name", None) not in ("Output", "Bidir"):
            raise ValueError("directions() needs a buffer that can drive the pins (direction \"o\" or \"io\"), not %r" % (pin,))
        width = len(info.gpios)
        if not isinstance(init, int) or isinstance(init, bool) or not 0 <= init < 1 << width:
            raise ValueError("init is the initial direction of %d pin(s): an integer from 0 to %d, not %r" % (width, (1 << width) - 1, init))
        if info.dirs is None:
            info.dirs, info.dirs_init = Signal(width, init=init, name="%s_%s_dirs" % (info.name, info.number)), init
            info.dir_pin = DirPin(info)
        elif info.dirs_init != init:
            raise ValueError("the direction register of %s_%s already exists with init %d, not %d" % (info.name, info.number, info.dirs_init, init))
        return info.dirs

    def dirs_info(self, signal):
        """The `DirPin` whose register is `signal`, or None.

        Parameters
        ----------
        signal : amaranth.hdl.Signal
            A signal.

        Returns
        -------
        DirPin or None
            The direction register's companion object.
        """
        for p in self._pio_pins:
            if p.dirs is signal:
                return p.dir_pin
        return None

    def _bind(self, buf):
        for p in self._pio_pins:
            if p.port is buf.port:
                p.o = getattr(buf, "o", None) if buf.direction.name in ("Output", "Bidir") else None
                p.i = getattr(buf, "i", None) if buf.direction.name in ("Input", "Bidir") else None
                return p
        return None

    def bind_buffers(self, fragment):
        """Find the ``io.Buffer`` submodules of an elaborated design and attach their signals to the requested pins.

        Parameters
        ----------
        fragment : amaranth.hdl.Fragment
            The elaborated design.

        Returns
        -------
        list of amaranth.lib.io.Buffer
            The buffers found.

        Raises
        ------
        ValueError
            If a buffer uses a port that was not requested from this platform.
        """
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
        """The `PinInfo` that owns `signal` (the ``o`` or ``i`` of its buffer), or None.

        Parameters
        ----------
        signal : amaranth.hdl.Signal
            A signal.

        Returns
        -------
        PinInfo or None
            The requested pin group the signal belongs to.
        """
        for p in self._pio_pins:
            if p.o is signal or p.i is signal:
                return p
        return None

    def pio_role(self, pin, role):
        """Force the PIO role of a requested output: ``"set"`` (set pins) or ``"side"`` (side-set). Default: inferred from how the design drives it.

        Parameters
        ----------
        pin : amaranth.lib.io.Buffer
            A buffer returned by `buffer`.
        role : str
            ``"set"`` or ``"side"``.

        Raises
        ------
        ValueError
            If `role` is not valid or the pin was not requested from this platform.
        """
        if role not in ("set", "side"):
            raise ValueError("role must be 'set' or 'side'")
        for p in self._pio_pins:
            if p.port is getattr(pin, "port", None):
                p.role = role
                return
        raise ValueError("%r was not requested from this platform" % (pin,))

    def scratch(self, name):
        """The PIO scratch register X or Y as a 32-bit Amaranth signal (reset value 0, as in the chip).

        Parameters
        ----------
        name : str
            ``"x"`` or ``"y"``.

        Returns
        -------
        amaranth.hdl.Signal
            The same signal on every call with the same name.

        Raises
        ------
        ValueError
            If `name` is not ``"x"`` or ``"y"``.
        """
        if name not in ("x", "y"):
            raise ValueError("the scratch registers are 'x' and 'y'")
        if name not in self._scratch:
            self._scratch[name] = Signal(32, name="pio_" + name)
        return self._scratch[name]

    def shifter(self, m, out_shift="right", in_shift="right", **config):
        """The OSR / ISR shifters and the two FIFOs of the state machine (`pioweave.data.Shifter`); call it once, inside ``elaborate()``.

        Parameters
        ----------
        m : amaranth.hdl.Module
            The module of the design.
        out_shift, in_shift : str
            ``"right"`` or ``"left"``, the shift direction of the OSR and the ISR.
        **config :
            ``autopull``, ``pull_thresh``, ``autopush``, ``push_thresh``, ``status``, ``status_n``, ``fifo_join`` (see `pioweave.data.Shifter`).

        Returns
        -------
        pioweave.data.Shifter
            The shifter, with the platform's `tx_init` words in the TX FIFO.

        Raises
        ------
        ValueError
            If a direction is not valid or the platform already has a shifter.
        """
        from .data import Shifter
        return Shifter(self, m, out_shift, in_shift, self.tx_init, **config)

    def block(self, *machines, name=None):
        """A block design with several state machines on this chip (`pioweave.block.Block`): ``platform.block(Machine("a", A), Machine("b", B)).build(...)``.

        Parameters
        ----------
        *machines : pioweave.block.Machine
            The state machines, in state machine number order (the first is state machine 0).
        name : str or None
            The default project name of the block.

        Returns
        -------
        pioweave.block.Block
            The block, for this platform class and this platform's `sm_clock_hz`.
        """
        from .block import Block
        return Block(machines, type(self), sm_clock_hz=self.sm_clock_hz, name=name)

    def create_missing_domain(self, name):
        return ClockDomain(name, reset_less=True)

    # ---- build
    _LEGACY_SIM = ("factory", "cycles", "stim", "tx", "tx_feed", "drain")

    def build(self, elaboratable, name="top", build_dir="build", do_build=True, program_opts=None, do_program=False, *, project="pio", sim=None, **legacy):
        """Compile the design and write the project.

        The signature is that of ``amaranth.build.Platform.build`` plus two keyword-only options. Loading a board is not done here: bitweave does it
        (``program --pico``).

        Parameters
        ----------
        elaboratable : amaranth.hdl.Elaboratable
            The design.
        name : str
            The program and project name.
        build_dir : str
            The directory the files are written to.
        do_build : bool
            False returns the ``BuildPlan`` without writing it.
        program_opts : dict or None
            Accepted for compatibility; not used.
        do_program : bool
            Must be False.
        project : str
            What is written: ``"pio"`` (the PIO program and the bitweave simulation cases), ``"pico-c"`` (a Pico SDK C project) or ``"micropython"``.
        sim : pioweave.project.SimOptions or None
            Feeds the reference simulation (see `pioweave.project`).
        **legacy :
            The keywords ``factory``, ``cycles``, ``stim``, ``tx``, ``tx_feed`` and ``drain`` of earlier versions still work and give a ``DeprecationWarning``.

        Returns
        -------
        amaranth.build.run.BuildPlan or amaranth.build.run.LocalBuildProducts
            The plan when `do_build` is false, else the products of the local build.

        Raises
        ------
        NotImplementedError
            If `do_program` is true.
        TypeError
            If an unknown keyword is given, or `sim` and the old keywords are both given.
        pioweave.compile.PioCompileError
            If the design is outside the subset the compiler accepts.
        pioweave.project.PioProjectError
            If `project` is not one of the three kinds, or `name` is not a plain identifier (ASCII letters, digits and ``_``, not starting with a digit, not a keyword).
        """
        if do_program:
            raise NotImplementedError("loading a board is done by bitweave (program --pico --yes), never by the platform")
        from .project import check_targets
        sim = self._sim_options(sim, legacy)
        plan = self.prepare(elaboratable, name, project=project, sim=sim)
        if not do_build:
            return plan
        check_targets(plan, build_dir)
        return plan.execute_local(build_dir)

    def _sim_options(self, sim, legacy):
        import warnings
        from .project import SimOptions
        unknown = sorted(set(legacy) - set(self._LEGACY_SIM))
        if unknown:
            raise TypeError("build() got unexpected keyword arguments: %s" % ", ".join(unknown))
        if legacy:
            if sim is not None:
                raise TypeError("pass either sim=SimOptions(...) or the old keywords %s, not both" % ", ".join(sorted(legacy)))
            warnings.warn("build(%s=...) is deprecated: pass sim=SimOptions(...)" % sorted(legacy)[0], DeprecationWarning, stacklevel=3)
            sim = SimOptions(**legacy)
        return sim

    def prepare(self, elaboratable, name="top", project="pio", sim=None):
        """Compile the design and return the ``BuildPlan`` holding the project files (what `build` does before it writes them).

        Parameters
        ----------
        elaboratable : amaranth.hdl.Elaboratable
            The design.
        name : str
            The program and project name.
        project : str
            ``"pio"``, ``"pico-c"`` or ``"micropython"``.
        sim : pioweave.project.SimOptions or None
            Feeds the reference simulation.

        Returns
        -------
        amaranth.build.run.BuildPlan
            The plan.
        """
        from ._validate import check_name
        from .compile import compile_design
        from .project import PioProjectError, write_project
        check_name(name, PioProjectError)
        compiled = compile_design(elaboratable, self, name)
        plan = BuildPlan(script="build_" + name)
        write_project(plan, compiled, self, name, project, sim)
        return plan

    def toolchain_prepare(self, fragment, name, **kwargs):     # prepare() above does not go through the generic path
        raise NotImplementedError("use platform.build(design, name, build_dir, project=...)")

    def toolchain_program(self, products, name, **kwargs):
        raise NotImplementedError("loading a board is done by bitweave (program --pico), never by the platform")


class RP2040PioPlatform(_RPPioPlatform):
    """The RP2040: PIO version 0, GPIO 0 to 29."""
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
    """RP2354A: the RP2350A pinout (QFN-60, 30 GPIOs) with 2 MB of flash in the package."""
    chip = "rp2354a"


class RP2354BPioPlatform(RP2350BPioPlatform):
    """RP2354B: the RP2350B pinout (QFN-80, 48 GPIOs) with 2 MB of flash in the package."""
    chip = "rp2354b"


#: Maps a chip name to its platform class.
PLATFORMS = {"rp2040": RP2040PioPlatform, "rp2350": RP2350PioPlatform, "rp2350a": RP2350APioPlatform, "rp2350b": RP2350BPioPlatform,
             "rp2354": RP2354PioPlatform, "rp2354a": RP2354APioPlatform, "rp2354b": RP2354BPioPlatform}


def gpio_resources(*groups):
    """Extra named resources over GPIO numbers.

    Parameters
    ----------
    *groups : tuple of (str, int, str)
        ``(name, number, pins)`` where `pins` is a string of GPIO numbers separated by spaces.

    Returns
    -------
    list of amaranth.build.Resource
        The resources, to be added to a platform's ``resources``.

    Examples
    --------
    >>> from pioweave.platform import gpio_resources
    >>> [(r.name, r.number) for r in gpio_resources(("led", 0, "25"), ("bus", 0, "2 3 4"))]
    [('led', 0), ('bus', 0)]
    """
    return [Resource(n, i, Pins(g, dir="io")) for n, i, g in groups]
