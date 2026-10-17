# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Several state machines in one design: the block layer (design note docs/design-notes/amaranth-pio.rst, section 2g; user guide docs/blocks.rst).

A PIO block has four state machines that share one 32-word instruction memory, the eight IRQ flags and the GPIOs. A `Block` is a list of `Machine` objects, each one an
ordinary single-machine design of this package (one Amaranth FSM, its own `platform.shifter()`, scratch registers and pin requests), run as state machine 0, 1, 2 and 3 in the
order they are listed::

    block = Block([Machine("baud", BaudGenerator), Machine("tx", Transmitter)], RP2040PioPlatform, sm_clock_hz=1_000_000)
    block.build("uart_baud", "build/uart", project="pico-c", sim=BlockSim(cycles=200, tx={"tx": (0x41,)}))

What is shared and how the reference simulation models it:

- **IRQ flags**: one set of eight flags; the machines change them through toggle registers like a single machine does (`IrqHub`, `IrqPort`). A flag that one machine sets and
  another clears in the same cycle ends up set (RP2040 datasheet 3.4.9, RP2350 datasheet 11.2.7). All machines see the flags as they were after the previous cycle.
- **pins**: one pad per GPIO. A GPIO that several machines write in the same cycle gets the level of the highest-numbered state machine (RP2040 datasheet 3.5.6.1, RP2350 datasheet
  11.5.6.1); a GPIO nobody writes in a cycle keeps its level. A machine that reads a GPIO that another machine drives sees the level written in the previous cycle.
  The directions (design note section 2g, ``platform.directions``) are per GPIO as well: the initial direction is the OR of the machines' initial directions, an instruction
  that writes a direction (``set`` / ``out`` / ``mov pindirs``, a side-set that writes directions) sets it for all machines, the highest-numbered writer wins in a cycle, and
  a GPIO that is an input reads the stimulus.
- **memory**: the programs are placed one after the other from address 0 in machine order (`BlockCompiled.offsets`); at most 32 instructions in all.

Nothing generated here has run on a chip.
"""
import dataclasses
import re

from amaranth.hdl import Cat, ClockDomain, Const, Module, Signal
from amaranth.sim import Simulator

from ._compat import Fragment, ast, clock_period, fsm_state_names
from .compile import PioCompileError, compile_design
from .data import IrqModel
from .platform import RP2040PioPlatform
from .project import PioProjectError, _c_sm_config, _c_sync_bypass, _cmake, _toml_case

__all__ = ["BlockError", "Machine", "Block", "BlockSim", "BlockCompiled", "IrqHub", "IrqPort", "block_run", "emulate_block", "machine_cases", "MAX_MACHINES", "MEMORY_WORDS"]

MAX_MACHINES = 4
MEMORY_WORDS = 32


class BlockError(PioCompileError):
    """A block of state machines that cannot be built: too many machines, too many instructions, a feature a block does not support, or an error in one machine's design
    (the message then starts with the machine name). It is a `PioCompileError`."""


# ----------------------------------------------------------------------------- shared IRQ flags
class IrqHub:
    """The IRQ flag toggles of every machine of a block and the host event signals; it creates the `IrqPort` of each machine.

    Parameters
    ----------
    machines : int
        The number of state machines.
    init_set : int
        Flags the host raises before cycle 0.
    """

    def __init__(self, machines, init_set=0):
        self.n = machines
        self.init_set = init_set & 0xFF
        self.set_tog = [[Signal(name="sm%d_irq_set_tog%d" % (k, i)) for i in range(8)] for k in range(machines)]
        self.clr_tog = [[Signal(name="sm%d_irq_clr_tog%d" % (k, i)) for i in range(8)] for k in range(machines)]
        self.host_set, self.host_clr = Signal(8, name="host_irq_set"), Signal(8, name="host_irq_clr")
        self.ports = {}

    def port(self, k):
        """The `IrqPort` of state machine `k` (the same object on every call).

        Parameters
        ----------
        k : int
            The state machine number.

        Returns
        -------
        IrqPort
            The machine's view of the shared flags.
        """
        if k not in self.ports:
            self.ports[k] = IrqPort(self, k)
        return self.ports[k]

    @property
    def used(self):
        """True when at least one machine uses an IRQ flag."""
        return bool(self.ports)

    @property
    def after(self):
        """The flags after the program's effects of the previous cycle (what the simulators report at the end of that cycle); valid when `used`."""
        return self.ports[min(self.ports)].after


class IrqPort(IrqModel):
    """The IRQ model as one state machine of a block sees it: it drives the toggles of its own machine and reads those of all machines.

    Every port keeps its own copy of the flag register, which is identical in all ports (each computes ``reg <= view``); the combined effect of a cycle is the OR of the sets
    and the OR of the clears of every machine, a set winning over a clear. `IrqModel` is the base class so that the compiler accepts it like the single-machine model.

    Parameters
    ----------
    hub : IrqHub
        The shared toggles and host signals.
    k : int
        The state machine number.
    """

    def __init__(self, hub, k):
        self.hub, self.k = hub, k
        self.init_set = hub.init_set
        self.set_tog, self.clr_tog = hub.set_tog[k], hub.clr_tog[k]
        self.host_set, self.host_clr = hub.host_set, hub.host_clr
        self.flag = [Signal(name="sm%d_irq_flag%d" % (k, i)) for i in range(8)]
        self.view, self.after = Signal(8, name="sm%d_irq_view" % k), Signal(8, name="sm%d_irq_after" % k)
        self._MustUse__silence = True

    def elaborate(self, platform):
        """Build the flags as this machine sees them (Amaranth's ``elaborate``); `platform` is not used."""
        m = Module()
        hub = self.hub
        reg = Signal(8)
        set_seen = [Signal(8, name="seen_set%d" % j) for j in range(hub.n)]
        clr_seen = [Signal(8, name="seen_clr%d" % j) for j in range(hub.n)]
        hs, hc = Signal(8, init=self.init_set), Signal(8)
        pend_set, pend_clr = Const(0, 8), Const(0, 8)
        for j in range(hub.n):
            pend_set = pend_set | (Cat(*hub.set_tog[j]) ^ set_seen[j])
            pend_clr = pend_clr | (Cat(*hub.clr_tog[j]) ^ clr_seen[j])
        m.d.comb += [
            self.after.eq((reg & ~pend_clr) | pend_set),            # a flag that is set and cleared in the same cycle ends up set
            self.view.eq((self.after & ~hc) | hs),
        ]
        m.d.comb += [self.flag[i].eq(self.view[i]) for i in range(8)]
        m.d.sync += [reg.eq(self.view), hs.eq(self.host_set), hc.eq(self.host_clr)]
        m.d.sync += [set_seen[j].eq(Cat(*hub.set_tog[j])) for j in range(hub.n)] + [clr_seen[j].eq(Cat(*hub.clr_tog[j])) for j in range(hub.n)]
        return m


# ----------------------------------------------------------------------------- description of a block
@dataclasses.dataclass(frozen=True)
class Machine:
    """One state machine of a block.

    Parameters
    ----------
    name : str
        A C identifier; it names the machine's program (``NAME_program`` in the generated C code) and its simulation case.
    design : callable
        Returns a fresh single-machine design (an Amaranth Elaboratable): a class or a function. A design is elaborated more than once (compile, reference simulation), so an
        instance is not accepted.
    """
    name: str
    design: object

    def __post_init__(self):
        if not isinstance(self.name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name):
            raise BlockError("a machine name is a C identifier (letters, digits and _, not starting with a digit), not %r" % (self.name,))
        if not callable(self.design):
            raise BlockError("machine %r: give the design class or a function that returns a new design, not an instance (the design is elaborated several times)" % self.name)


@dataclasses.dataclass
class BlockSim:
    """What the reference simulation of a block needs.

    Parameters
    ----------
    cycles : int
        Length of the simulation (all machines run at the same clock, one cycle per instruction cycle).
    stim : dict
        Input waveforms ``{gpio: "0101..."}`` for GPIOs no machine drives; the last character repeats.
    tx : dict
        ``{machine name: words}`` in that machine's TX FIFO at cycle 0.
    tx_feed : dict
        ``{machine name: [(cycle, word)]}`` written by the host so that the instruction of that cycle sees them.
    drain : dict
        ``{machine name: bool}``: the host empties that machine's RX FIFO every cycle (default True).
    irq_set, irq_clear : tuple of tuple
        ``(cycle, flag)`` pairs: the host raises or lowers a flag of the shared flag register at the start of that cycle.
    """
    cycles: int = 64
    stim: dict = dataclasses.field(default_factory=dict)
    tx: dict = dataclasses.field(default_factory=dict)
    tx_feed: dict = dataclasses.field(default_factory=dict)
    drain: dict = dataclasses.field(default_factory=dict)
    irq_set: tuple = ()
    irq_clear: tuple = ()

    def __post_init__(self):
        for name, feed in self.tx_feed.items():
            cycles = [c for c, _ in feed]
            for c in cycles:
                if cycles.count(c) > 1:
                    raise BlockError("tx_feed of machine %s has two words in cycle %d; the reference simulation applies one host write per cycle\n  hint: move one of the words to another cycle" % (name, c))


def _decode_writes(word, cfg, prog, dirs=False):
    """The window pins an instruction writes: the levels (``dirs=False``: ``set pins`` the SET group, ``out pins`` / ``mov pins`` the OUT group, and the side-set field when
    it is enabled and writes levels) or the directions (``dirs=True``: ``set pindirs``, ``out pindirs``, ``mov pindirs`` and a side-set that writes pin directions), as the emulator does."""
    op, a = word >> 13, word & 0xFF
    d = (a >> 5) & 7
    pins = set()
    if op == 7 and d == (4 if dirs else 0):
        pins.update(range(cfg.get("set_base", 0), cfg.get("set_base", 0) + cfg.get("set_count", 5)))
    elif (op == 3 and d == (4 if dirs else 0)) or (op == 5 and d == (3 if dirs else 0)):
        pins.update(range(cfg.get("out_base", 0), cfg.get("out_base", 0) + cfg.get("out_count", 0)))
    bits = prog.side_count + (1 if prog.side_opt and prog.side_count else 0)
    if bits and bool(prog.side_pindirs) == dirs:
        field = (word >> 8) & 0x1F
        if not prog.side_opt or (field >> 4) & 1:
            vb = bits - (1 if prog.side_opt else 0)
            pins.update(range(cfg.get("sideset_base", 0), cfg.get("sideset_base", 0) + vb))
    return {p & 31 for p in pins}


def _can_stall(word, cfg):
    """True for an instruction that can stall: wait, a blocking push / pull, ``irq wait``, ``in`` with autopush, ``out`` with autopull (conservative)."""
    op, a = word >> 13, word & 0xFF
    return (op == 1 or (op == 2 and cfg.get("autopush", False)) or (op == 3 and cfg.get("autopull", False)) or (op == 4 and bool((a >> 5) & 1))
            or (op == 6 and bool((a >> 5) & 1) and not (a >> 6) & 1))


class BlockCompiled:
    """The result of `Block.compile`: every machine compiled, and where the programs sit in the instruction memory.

    Attributes
    ----------
    block : Block
        The block.
    names : list of str
        The machine names, in state machine number order.
    compiled : list of pioweave.compile.Compiled
        One per machine.
    offsets : list of int
        The load address of each machine's program: the programs are placed one after the other from address 0.
    gpio_base : int
        The GPIO base of the block (0, or 16 on the 48-GPIO parts): one for all machines, because GPIOBASE belongs to the block.
    chip : str
        The chip name.
    """

    def __init__(self, block, compiled, gpio_base):
        self.block, self.names, self.compiled, self.gpio_base = block, [m.name for m in block.machines], compiled, gpio_base
        self.chip = block.platform.chip
        self.offsets, off = [], 0
        for c in compiled:
            self.offsets.append(off)
            off += len(c.program.words())
        self.instructions = off
        self._writes = [self._state_writes(c) for c in compiled]

    @staticmethod
    def _state_writes(c):
        """{state name: (set of GPIOs the instruction writes the level of, can stall, set of GPIOs it writes the direction of)} for every state that is an instruction."""
        words = [w & 0xFFFF for w in c.program.words()]
        cfg = c.sim_config()
        out = {}
        for st, idx in c.states:
            if idx is not None:
                out[st] = ({p + c.gpio_base for p in _decode_writes(words[idx], cfg, c.program)}, _can_stall(words[idx], cfg),
                           {p + c.gpio_base for p in _decode_writes(words[idx], cfg, c.program, True)})
        return out

    @property
    def dirs(self):
        """The GPIOs any machine drives, as a sorted list."""
        return sorted({g for c in self.compiled for g in c.output_gpios})

    def program_text(self):
        """All the programs, one ``.program`` block each, as one pioasm file.

        Returns
        -------
        str
            The text.
        """
        return "\n".join(c.program.pio_text() for c in self.compiled)

    def describe(self):
        """A short table: machine, state machine number, load address, instructions.

        Returns
        -------
        str
            One line per machine.
        """
        return "\n".join("sm%d %-12s offset %2d, %2d instructions" % (k, n, o, len(c.program.words())) for k, (n, o, c) in enumerate(zip(self.names, self.offsets, self.compiled)))


class Block:
    """Several state machines of one PIO block (see the module documentation).

    Parameters
    ----------
    machines : iterable of Machine
        1 to 4 machines; the first is state machine 0. The order matters: the highest-numbered machine wins a pin that several write.
    platform : type or RP2040PioPlatform
        A platform class of this package (or an instance, whose class and `sm_clock_hz` are used).
    sm_clock_hz : int, float or None
        The state machine clock of the C project; one for all machines (the reference simulation runs them at the same clock).
    name : str or None
        A default project name.

    Raises
    ------
    BlockError
        If there are no machines or more than four, a name is used twice, or a name or a design is not valid.
    """

    def __init__(self, machines, platform=RP2040PioPlatform, sm_clock_hz=None, name=None):
        machines = list(machines)
        if isinstance(platform, type):
            self.platform = platform
        else:
            self.platform, sm_clock_hz = type(platform), sm_clock_hz if sm_clock_hz is not None else platform.sm_clock_hz
        if not machines:
            raise BlockError("a block needs at least one state machine")
        if len(machines) > MAX_MACHINES:
            raise BlockError("a PIO block has %d state machines; %d were given (%s)" % (MAX_MACHINES, len(machines), ", ".join(m.name for m in machines)))
        for m in machines:
            if not isinstance(m, Machine):
                raise BlockError("the machines are Machine(name, design) objects, not %r" % (m,))
        names = [m.name for m in machines]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise BlockError("machine names must be different (the name is the program and the case name): %s" % ", ".join(dup))
        self.machines, self.sm_clock_hz, self.name = machines, sm_clock_hz, name

    # ---- instances
    def instantiate(self, init_set=0, simulation=False, gpio_bases=None):
        """New designs and platforms for every machine, sharing one `IrqHub`.

        Parameters
        ----------
        init_set : int
            Flags the host raises before cycle 0.
        simulation : bool
            Make the platforms return simulation ports.
        gpio_bases : tuple of int or None
            Restrict the GPIO bases the compiler may choose.

        Returns
        -------
        tuple
            ``(hub, [(Machine, design, platform)])``.
        """
        hub = IrqHub(len(self.machines), init_set)
        out = []
        for k, m in enumerate(self.machines):
            pl = self.platform(sm_clock_hz=self.sm_clock_hz)
            pl.irq_hub, pl.sm_index, pl.simulation = hub, k, simulation
            if gpio_bases is not None:
                pl.gpio_bases = tuple(gpio_bases)
            out.append((m, m.design(), pl))
        return hub, out

    # ---- compile
    def compile(self, name=None):
        """Compile every machine and place the programs in the instruction memory.

        The program of a machine is called ``NAME_MACHINE`` (so ``NAME_MACHINE_program`` in the C code): the prefix keeps a machine called ``tx`` or ``x`` from colliding with a
        keyword of pioasm, which cannot be a program name.

        The GPIO base is chosen for the whole block: the smallest base whose 32-GPIO window holds the pins of all machines. Each machine is compiled with a platform of its own
        (own pin requests, scratch registers and data path) that shares the IRQ flags.

        Parameters
        ----------
        name : str or None
            The prefix of the program names; defaults to the block's `name`, else ``"block"``.

        Returns
        -------
        BlockCompiled
            The compiled machines and their load addresses.

        Raises
        ------
        BlockError
            If a machine's design is refused (the message starts with its name), the pins do not fit one GPIO window, the programs together are longer than 32 instructions,
            a program uses an IRQ number with ``rel``, or a GPIO written by two machines is written by an instruction that can stall.
        """
        window = "do not fit one 32-GPIO window"
        prefix = name or self.name or "block"
        results = {}
        for base in self.platform.gpio_bases:
            row = []
            for m in self.machines:
                _, [(_, design, pl)] = self._one(m, base)
                try:
                    row.append((compile_design(design, pl, "%s_%s" % (prefix, m.name)), None))
                except PioCompileError as e:
                    row.append((None, str(e)))
            results[base] = row
            if all(c is not None for c, _ in row):
                return self._check([c for c, _ in row], base)
        for row in results.values():            # a machine that is refused for a reason other than the window
            for m, (c, err) in zip(self.machines, row):
                if err is not None and window not in err:
                    raise BlockError("machine %s: %s" % (m.name, err))
        feasible = {m.name: [b for b in results if results[b][i][0] is not None] for i, m in enumerate(self.machines)}
        for i, m in enumerate(self.machines):
            if not feasible[m.name]:
                raise BlockError("machine %s: %s" % (m.name, results[min(results)][i][1]))
        raise BlockError("the machines need different GPIO windows (GPIOBASE belongs to the whole block): %s" % "; ".join(
            "machine %s fits only GPIO base %s" % (n, " or ".join(str(b) for b in bs)) for n, bs in feasible.items()))

    def _one(self, m, base):
        k = self.machines.index(m)
        hub = IrqHub(len(self.machines))
        pl = self.platform(sm_clock_hz=self.sm_clock_hz)
        pl.irq_hub, pl.sm_index, pl.gpio_bases = hub, k, (base,)
        return hub, [(m, m.design(), pl)]

    def _check(self, compiled, base):
        total = sum(len(c.program.words()) for c in compiled)
        if total > MEMORY_WORDS:
            raise BlockError("the programs need %d instructions together (%s) but the instruction memory of a PIO block has %d words" % (
                total, ", ".join("%s %d" % (m.name, len(c.program.words())) for m, c in zip(self.machines, compiled)), MEMORY_WORDS))
        for m, c in zip(self.machines, compiled):
            if c.irq_rel:
                raise BlockError("machine %s uses `irq ... rel`: the flag number then depends on the state machine number, which the single-machine IRQ model does not track; "
                                 "write the flag number without rel in a block" % m.name)
        bc = BlockCompiled(self, compiled, base)
        writers = {}
        for k, c in enumerate(compiled):
            for g in c.output_gpios:
                writers.setdefault(g, []).append(k)
        for g, ks in sorted(writers.items()):
            if len(ks) < 2:
                continue
            for k in ks:
                for st, (pins, stalls, dpins) in bc._writes[k].items():
                    if g in (pins | dpins) and stalls:
                        raise BlockError("GPIO %d is written by machines %s; machine %s writes it in state %s with an instruction that can stall (wait, blocking pull / push, irq wait, "
                                         "autopull / autopush): the hardware writes a side-set on the first cycle of a stall only, which the model of a shared pin does not reproduce. "
                                         "Give the shared GPIO to one machine, or write it from instructions that do not stall" % (
                                             g, " and ".join(self.machines[j].name for j in ks), self.machines[k].name, st))
        return bc

    # ---- build
    def prepare(self, name, project="pio", sim=None):
        """Compile the block and return the ``BuildPlan`` holding the project files.

        Parameters
        ----------
        name : str
            The project name (the ``.pio`` file and the bitweave section).
        project : str
            ``"pio"`` (the programs, ``bitweave.toml`` with one simulation case per machine, ``README.md``) or ``"pico-c"`` (and a Pico SDK C project that claims the state
            machines, loads the programs at their offsets, configures each machine and starts them together). ``"micropython"`` is refused for a block.
        sim : BlockSim or None
            Feeds the reference simulation whose waveforms become the expectations.

        Returns
        -------
        amaranth.build.run.BuildPlan
            The plan.

        Raises
        ------
        BlockError
            If the block cannot be compiled.
        pioweave.project.PioProjectError
            If `project` is not valid.
        """
        from amaranth.build.run import BuildPlan
        if project == "micropython":
            raise PioProjectError("project='micropython' is not written for a block: rp2.StateMachine takes one decorated program per machine and has no call to start several machines in "
                                  "step or to raise IRQ flags [verify]; use project='pico-c' (pio_enable_sm_mask_in_sync starts them together)")
        if project not in ("pio", "pico-c"):
            raise PioProjectError("project must be 'pio' or 'pico-c' for a block")
        bc = self.compile(name)
        plan = BuildPlan(script="build_" + name)
        _write_block_project(plan, bc, name, project, sim or BlockSim())
        return plan

    def build(self, name=None, build_dir="build", do_build=True, *, project="pio", sim=None):
        """Compile the block and write the project (the counterpart of ``platform.build`` for a block).

        Parameters
        ----------
        name : str or None
            The project name; defaults to the block's `name`.
        build_dir : str
            The directory the files are written to.
        do_build : bool
            False returns the ``BuildPlan`` without writing it.
        project : str
            ``"pio"`` or ``"pico-c"``.
        sim : BlockSim or None
            Feeds the reference simulation.

        Returns
        -------
        amaranth.build.run.BuildPlan or amaranth.build.run.LocalBuildProducts
            The plan when `do_build` is false, else the products of the local build.
        """
        name = name or self.name
        if not name:
            raise BlockError("give the project name")
        plan = self.prepare(name, project, sim)
        return plan.execute_local(build_dir) if do_build else plan


# ----------------------------------------------------------------------------- the reference simulation (Amaranth)
def _check_sim(names, sim):
    for what in ("tx", "tx_feed", "drain"):
        bad = sorted(set(getattr(sim, what)) - set(names))
        if bad:
            raise BlockError("BlockSim.%s names a machine that is not in the block: %s (machines: %s)" % (what, ", ".join(bad), ", ".join(names)))


def _wave_at(w, t):
    return int(w[min(t, len(w) - 1)])


def block_run(block, sim=None, compiled=None):
    """Simulate all the machines of a block together with the Amaranth simulator.

    Alignment with the PIO model is that of `pioweave.project.amaranth_run`: the FSM state in cycle t does what the instruction executed in cycle t does and a register sampled
    after the t-th clock edge is the effect of cycle t. The pins are resolved after each cycle: a GPIO takes the level of the highest-numbered machine whose instruction writes it
    in that cycle (the testbench reads each machine's state and the pins its instruction writes from the compiled program), else it keeps its level; a machine that reads a GPIO
    some machine drives sees that level as it was after the previous cycle; a GPIO nobody drives is read from ``sim.stim``.

    Parameters
    ----------
    block : Block
        The block.
    sim : BlockSim or None
        The stimulus.
    compiled : BlockCompiled or None
        The result of ``block.compile()`` (compiled here when None).

    Returns
    -------
    dict
        ``"pads"``: ``{gpio: waveform}`` for every driven GPIO after the pin resolution (the level of the writer where the GPIO is an output, the stimulus where a direction
        register made it an input); ``"own"``: ``{machine: {gpio: waveform}}`` the level each machine's own output register holds; ``"own_dirs"``: ``{machine: {gpio: waveform}}`` the
        direction register of each machine's groups that have one (1 = output; the directions of the pads are per GPIO, written by whichever machine's instruction last wrote them); ``"view"``: ``{gpio: waveform}`` what the machines read (previous-cycle pad level for driven GPIOs, the stimulus otherwise) for every GPIO a machine reads;
        ``"rx"``: ``{machine: [(cycle, word)]}``; ``"rx_left"`` and ``"tx_left"``: ``{machine: words left}``; ``"irq"``: the eight flags as a mask after each cycle.
    """
    sim = sim or BlockSim()
    compiled = compiled or block.compile()
    names = compiled.names
    _check_sim(names, sim)
    init = sum({1 << f for c, f in sim.irq_set if c <= 0})
    hub, insts = block.instantiate(init_set=init, simulation=True, gpio_bases=(compiled.gpio_base,))
    top = Module()
    top.domains.sync = ClockDomain()
    mach = []
    for k, (m, design, pl) in enumerate(insts):
        feeds = sorted((c, w) for c, w in sim.tx_feed.get(m.name, ()) if c > 0)
        pl.tx_init = list(sim.tx.get(m.name, ())) + [w for c, w in sim.tx_feed.get(m.name, ()) if c <= 0]
        frag = Fragment.get(design, pl)
        pl.bind_buffers(frag)
        top.submodules["sm%d_%s" % (k, m.name)] = frag
        fsm = next(s for s in frag.statements.get("sync", []) if isinstance(s, ast.Switch) and isinstance(s.test, Signal) and fsm_state_names(s.test))
        state_names = {n: v.rsplit("/", 1)[0] for n, v in fsm_state_names(fsm.test).items()}
        ins, outs, dsig = {}, {}, {}
        for p in pl._pio_pins:
            if p.dirs is not None:
                for j, g in enumerate(p.gpios):
                    dsig[g] = (p.dirs, j)
            if p.i is not None:
                for j, g in enumerate(p.gpios):
                    ins[g] = (p.port.i, j)
            if p.o is not None:
                for j, g in enumerate(p.gpios):
                    outs[g] = (p.o, j)
        mach.append({"name": m.name, "pl": pl, "feeds": feeds, "state": fsm.test, "state_names": state_names, "ins": ins, "outs": outs, "dsig": dsig, "fifo": pl._shifter.fifo if pl._shifter else None,
                     "drain": sim.drain.get(m.name, True), "writes": compiled._writes[k]})
    dirs = compiled.dirs
    reads = sorted({g for mm in mach for g in mm["ins"]})
    pads = {g: [] for g in dirs}
    own = {mm["name"]: {g: [] for g in mm["outs"]} for mm in mach}
    own_dirs = {mm["name"]: {g: [] for g in mm["dsig"]} for mm in mach}
    view = {g: [] for g in reads}
    rx = {mm["name"]: [] for mm in mach}
    left = {}
    irq_trace = []

    async def tb(ctx):
        level = {g: 0 for g in dirs}
        init = 0
        for c in compiled.compiled:
            init |= c.dirs_init()
        isout = {g: (init >> (g - compiled.gpio_base)) & 1 for g in dirs}      # the direction of each driven GPIO: fixed outputs 1, a group with a direction register: its init until an instruction writes it

        def pad(g, t):
            return level[g] if g in level and isout[g] else _wave_at(sim.stim.get(g, "0"), t)

        for t in range(sim.cycles):
            for g in reads:
                view[g].append(str(pad(g, t)))
            for mm in mach:
                levels = {}
                for g, (sig, j) in mm["ins"].items():
                    levels[id(sig)] = (sig, levels.get(id(sig), (None, 0))[1] | (int(view[g][-1]) << j))
                for sig, value in levels.values():
                    ctx.set(sig, value)
                fifo = mm["fifo"]
                if fifo is not None:
                    due = [w for c, w in mm["feeds"] if c == t + 1]
                    ctx.set(fifo.host_tx_en, 1 if due else 0)
                    ctx.set(fifo.host_tx_data, due[0] if due else 0)
                    ctx.set(fifo.host_rx_pop, 0)
                    ctx.set(fifo.host_rx_drain, 1 if mm["drain"] else 0)
            if hub.used:
                ctx.set(hub.host_set, sum({1 << f for c, f in sim.irq_set if c == t + 1}))
                ctx.set(hub.host_clr, sum({1 << f for c, f in sim.irq_clear if c == t + 1}))
            executing = [mm["state_names"].get(ctx.get(mm["state"])) for mm in mach]
            await ctx.tick()
            for k, mm in enumerate(mach):
                bits = {g: (ctx.get(sig) >> j) & 1 for g, (sig, j) in mm["outs"].items()}
                for g, b in bits.items():
                    own[mm["name"]][g].append(str(b))
                written, _, dwritten = mm["writes"].get(executing[k], (set(), False, set()))
                for g in written:
                    if g in level and g in bits:
                        level[g] = bits[g]            # machines are visited in order, so the highest-numbered writer is the last one
                for g in dwritten:
                    if g in isout and g in mm["dsig"]:
                        isout[g] = (ctx.get(mm["dsig"][g][0]) >> mm["dsig"][g][1]) & 1
                for g, (sig, j) in mm["dsig"].items():
                    own_dirs[mm["name"]][g].append(str((ctx.get(sig) >> j) & 1))
            for g in dirs:
                pads[g].append(str(pad(g, t)))
            if hub.used:
                irq_trace.append(ctx.get(hub.after))
            for mm in mach:
                fifo = mm["fifo"]
                if fifo is not None and ctx.get(fifo.host_rx_pending):
                    rx[mm["name"]].append((t, ctx.get(fifo.host_rx_data)))
        for mm in mach:
            fifo = mm["fifo"]
            if fifo is not None:
                left[mm["name"]] = (ctx.get(fifo.host_tx_level), 0 if mm["drain"] else ctx.get(fifo.host_rx_level) + ctx.get(fifo.host_rx_pending))
            else:
                left[mm["name"]] = (0, 0)

    s = Simulator(top)
    s.add_clock(clock_period())
    s.add_testbench(tb)
    s.run()
    return {"pads": {g: "".join(v) for g, v in pads.items()}, "own": {n: {g: "".join(v) for g, v in w.items()} for n, w in own.items()},
            "own_dirs": {n: {g: "".join(v) for g, v in w.items()} for n, w in own_dirs.items()},
            "view": {g: "".join(v) for g, v in view.items()}, "rx": rx, "tx_left": {n: v[0] for n, v in left.items()}, "rx_left": {n: v[1] for n, v in left.items()}, "irq": irq_trace}


# ----------------------------------------------------------------------------- the emulator running the compiled programs together
def emulate_block(compiled, sim=None):
    """Run the compiled programs of a block together in the pioweave emulator (`PioBlock`: one `StateMachine` per machine, shared instruction memory, flags and pins), with the
    conventions of `block_run` (inputs, host words and host IRQ events at the start of the cycle, draining host).

    Parameters
    ----------
    compiled : BlockCompiled
        The result of ``block.compile()``.
    sim : BlockSim or None
        The stimulus.

    Returns
    -------
    dict
        The keys of `block_run` that the emulator can tell: ``"pads"``, ``"view"``, ``"rx"``, ``"rx_left"``, ``"rx_left_words"``, ``"tx_left"`` and ``"irq"``.
    """
    from .builder.program import CHIP_VERSION
    from .emulator.core import PioBlock
    sim = sim or BlockSim()
    _check_sim(compiled.names, sim)
    blk = PioBlock(version=CHIP_VERSION[compiled.chip], gpio_base=compiled.gpio_base, sync_cycles=0)
    sms = []
    for k, (c, off) in enumerate(zip(compiled.compiled, compiled.offsets)):
        loaded = blk.load(c.program, offset=off)
        cfg = dict(c.sim_config())
        cfg.pop("gpio_base", None)
        if "sideset_base" in cfg:
            cfg["side_base"] = cfg.pop("sideset_base")
        blk.dirs |= cfg.pop("dirs", 0)
        sm = blk.sm[k]
        sm.configure(program=loaded, **cfg)
        sm.enable()
        for w in sim.tx.get(compiled.names[k], ()):
            sm.put(w)
        sms.append(sm)
    dirs, reads = compiled.dirs, sorted({g for c in compiled.compiled for g in c.input_gpios})
    base = compiled.gpio_base
    pads = {g: [] for g in dirs}
    view = {g: [] for g in reads}
    rx = {n: [] for n in compiled.names}
    irq = []
    for t in range(sim.cycles):
        for k, n in enumerate(compiled.names):
            for c, w in sorted(sim.tx_feed.get(n, ())):
                if c == t:
                    sms[k].put(w)
        for c, f in sim.irq_clear:
            if c == t:
                blk.irq &= ~(1 << f)
        for c, f in sim.irq_set:
            if c == t:
                blk.irq |= 1 << f
        ext = 0
        for g, w in sim.stim.items():
            if 0 <= g - base <= 31 and w[min(t, len(w) - 1)] == "1":
                ext |= 1 << (g - base)
        blk.ext = ext
        seen = blk.pads()                               # what the machines read in this cycle: the driven pins as they were after the previous cycle, the stimulus elsewhere
        for g in reads:
            view[g].append(str((seen >> (g - base)) & 1))
        blk.step()
        for k, n in enumerate(compiled.names):
            if sim.drain.get(n, True):
                while len(sms[k].rx):
                    rx[n].append((t, sms[k].get()))
        p = blk.pads()
        for g in dirs:
            pads[g].append(str((p >> (g - base)) & 1))
        irq.append(blk.irq)
    return {"pads": {g: "".join(v) for g, v in pads.items()}, "view": {g: "".join(v) for g, v in view.items()}, "rx": rx,
            "rx_left": {n: len(sms[k].rx) for k, n in enumerate(compiled.names)}, "rx_left_words": {n: list(sms[k].rx.q) for k, n in enumerate(compiled.names)},
            "tx_left": {n: len(sms[k].tx) for k, n in enumerate(compiled.names)}, "irq": irq}


def _host_masks(sim, t):
    """(set mask, clear mask) of the host IRQ events the flags see at the start of cycle t (events at or before cycle 0 are the initial state)."""
    s = sum({1 << f for c, f in sim.irq_set if c == t or (t == 0 and c <= 0)})
    c_ = sum({1 << f for c, f in sim.irq_clear if c == t}) if t > 0 else 0
    return s, c_


def machine_cases(compiled, sim, run):
    """For each machine, the environment that makes a single-machine simulation behave as the machine did in the block: the waveforms of the GPIOs it reads, and the IRQ events
    that stand in for the other machines and the host.

    The other machines are not part of a one-machine case of bitweave's simulator, so their effect is replayed: the GPIOs the machine reads (not its own outputs) are driven with
    what the block showed it, and before each cycle the flags are forced to the value the machine saw in the block (the differences against the single machine's own flags become
    ``irq_set`` / ``irq_clear`` host events). The result is checked against the block's own waveforms in the tests; what is not reproduced is the effect of the machine on the others.

    Parameters
    ----------
    compiled : BlockCompiled
        The result of ``block.compile()``.
    sim : BlockSim
        The stimulus of the block run.
    run : dict
        The result of `block_run` (or of `emulate_block`: only ``view`` and ``irq`` are used).

    Returns
    -------
    list of dict
        One per machine: ``"stim"`` ({window pin: waveform}), ``"irq_set"`` and ``"irq_clear"`` (lists of ``(cycle, flag)``), and the solo emulator's ``"pads"``
        ({gpio: waveform} of the machine's own outputs; for a pin with a direction register the pad: the output where the pin is an output, the block's view elsewhere),
        ``"dirs"`` ({gpio: waveform} of the pins with a direction register), ``"rx"`` and ``"rx_left"``.
    """
    from .builder.program import CHIP_VERSION
    from .emulator.core import PioBlock
    out = []
    seen = []
    prev = 0
    for t in range(sim.cycles):
        s, c = _host_masks(sim, t)
        v = ((prev & ~c) | s) & 0xFF
        seen.append(v)
        prev = run["irq"][t] if t < len(run["irq"]) else prev
    for k, (name, c) in enumerate(zip(compiled.names, compiled.compiled)):
        cfg = dict(c.sim_config())
        cfg.pop("gpio_base", None)
        if "sideset_base" in cfg:
            cfg["side_base"] = cfg.pop("sideset_base")
        dirs = cfg.pop("dirs", 0)
        blk = PioBlock(version=CHIP_VERSION[compiled.chip], gpio_base=compiled.gpio_base, sync_cycles=0)
        loaded = blk.load(c.program, offset=0)
        sm = blk.sm[0]
        sm.configure(program=loaded, **cfg)
        sm.enable()
        blk.dirs = dirs
        for w in sim.tx.get(name, ()):
            sm.put(w)
        dyn = set(c.dynamic_gpios)         # a pin with a direction register can become an input later: it is read from the block's view whatever its initial direction
        env = {c.pin(g): run["view"][g] for g in c.input_gpios if ((dirs >> c.pin(g)) & 1 == 0 or g in dyn) and g in run["view"]}
        sets, clears, pads, rx = [], [], {g: [] for g in c.output_gpios}, []
        dirs_seen = {g: [] for g in dyn}
        drain = sim.drain.get(name, True)
        for t in range(sim.cycles):
            for cyc, w in sorted(sim.tx_feed.get(name, ())):
                if cyc == t:
                    sm.put(w)
            diff = seen[t] ^ blk.irq
            for f in range(8):
                if (diff >> f) & 1:
                    (sets if (seen[t] >> f) & 1 else clears).append((t, f))
            blk.irq = seen[t]
            blk.ext = sum(1 << p for p, w in env.items() if w[min(t, len(w) - 1)] == "1")
            blk.step()
            if drain:
                while len(sm.rx):
                    rx.append((t, sm.get()))
            pv = blk.pads()
            for g in c.output_gpios:
                pads[g].append(str((pv >> c.pin(g)) & 1))
            for g in dyn:
                dirs_seen[g].append(str((blk.dirs >> c.pin(g)) & 1))
        out.append({"stim": env, "irq_set": sets, "irq_clear": clears, "pads": {g: "".join(v) for g, v in pads.items()}, "rx": rx, "rx_left": list(sm.rx.q),
                    "dirs": {g: "".join(v) for g, v in dirs_seen.items()}})
    return out


def solo_expectations(compiled, k, run, env):
    """What machine `k` alone must show in the one-machine case of `machine_cases` (the expectations written to ``bitweave.toml``).

    Parameters
    ----------
    compiled : BlockCompiled
        The result of ``block.compile()``.
    k : int
        The machine number.
    run : dict
        The result of `block_run`.
    env : dict
        The entry `machine_cases` returned for machine `k`.

    Returns
    -------
    tuple
        ``(pads, dirs)``, both ``{gpio: waveform}``: the machine's own output register for a GPIO whose direction the host fixes; for a pin with a direction register the pad (the
        register where the pin is an output, the environment `env` shows elsewhere), and in `dirs` the direction register.
    """
    c, name = compiled.compiled[k], compiled.names[k]
    dyn = set(c.dynamic_gpios)
    pads, dirs = {}, {}
    for g, w in run["own"][name].items():
        if g not in c.output_gpios:
            continue
        if g in dyn:
            d, e = run["own_dirs"][name][g], env["stim"].get(c.pin(g), "0")
            pads[g] = "".join(w[t] if d[t] == "1" else e[min(t, len(e) - 1)] for t in range(len(w)))
            dirs[g] = d
        else:
            pads[g] = w
    return pads, dirs


# ----------------------------------------------------------------------------- project files
def _write_block_project(plan, bc, name, project, sim):
    block = bc.block
    _check_sim(bc.names, sim)
    run = block_run(block, sim, bc)
    envs = machine_cases(bc, sim, run)
    plan.add_file(name + ".pio", bc.program_text())
    cases = []
    for k, (mname, c) in enumerate(zip(bc.names, bc.compiled)):
        env = envs[k]
        pads_k, dirs_k = solo_expectations(bc, k, run, env)
        golden = {c.pin(g): w for g, w in pads_k.items()}
        golden_dirs = {c.pin(g): w for g, w in dirs_k.items()}
        rx_k = run["rx"][mname]
        drain = sim.drain.get(mname, True)
        text = _toml_case(c, name, sim.cycles, env["stim"], golden, bc.chip, tuple(sim.tx.get(mname, ()))[:c.tx_depth], tuple(sim.tx_feed.get(mname, ())), drain,
                          rx_k if (c.uses_rx or rx_k) else None, tuple(env["irq_set"]), tuple(env["irq_clear"]), None, golden_dirs, case=mname, program=c.program.name, header=k == 0)
        cases.append(text)
    cfg = "\n".join(cases)
    if project == "pico-c":
        plan.add_file("CMakeLists.txt", _cmake(name, any(c.uses_rx for c in bc.compiled)))
        plan.add_file("main.c", _c_block_main(bc, name, sim))
        board = ('board = "%s"\n' % block.platform.sdk_board) if block.platform.sdk_board else ""
        cfg = ("[firmware.%s]\nsource_dir = \"{root}\"\nchip = \"%s\"\n" % (name, bc.chip)) + board + "# sdk_path = \"/path/to/pico-sdk\"        # or set PICO_SDK_PATH\n# toolchain_dir = \"/path/to/arm-none-eabi/bin\"\n\n" + cfg
    plan.add_file("bitweave.toml", cfg)
    plan.add_file("README.md", _block_readme(bc, name, project))
    plan.add_file("build_%s.sh" % name, "#!/bin/sh\nset -e\n# the files are the product; assemble and check them with bitweave (see README.md)\n")


def _c_block_main(bc, name, sim):
    block = bc.block
    pl = block.platform(sm_clock_hz=block.sm_clock_hz)
    lines = ['#include <stdio.h>', '#include "pico/stdlib.h"', '#include "hardware/clocks.h"', '#include "hardware/pio.h"', '#include "%s.pio.h"' % name, "",
             "// Generated from the Amaranth block design by pioweave.block. Not run on a chip [verify].", "// State machine k runs machine k of the block: the highest-numbered machine wins a pin",
             "// that several write, so the numbers are claimed explicitly and the programs are loaded at the offsets the simulation used.", ""]
    if block.sm_clock_hz:
        lines += ["#define SM_CLOCK_HZ %.1ff" % float(block.sm_clock_hz), ""]
    lines += ["int main(void) {"]
    if any(c.uses_rx for c in bc.compiled):
        lines.append("    stdio_init_all();")
    lines.append("    PIO pio = pio0;")
    if bc.gpio_base:
        lines.append("    pio_set_gpio_base(pio, %d);     // the programs use GPIO %d to %d; the SDK pin numbers below are real GPIO numbers" % (bc.gpio_base, bc.gpio_base, bc.gpio_base + 31))
    lines += _c_sync_bypass(bc.compiled)
    for k, (mname, c, off) in enumerate(zip(bc.names, bc.compiled, bc.offsets)):
        lines += ["    {   // state machine %d: %s (%d instructions at offset %d)" % (k, mname, len(c.program.words()), off),
                  "        uint sm = %d;" % k, "        pio_sm_claim(pio, sm);", "        uint offset = %d;" % off, "        pio_add_program_at_offset(pio, &%s_program, offset);" % c.program.name]
        lines += ["    " + ln for ln in _c_sm_config(c, pl, c.program.name)]
        lines += ["        pio_sm_put_blocking(pio, sm, 0x%08xu);" % w for w in tuple(sim.tx.get(mname, ()))[:c.tx_depth]]
        lines.append("    }")
    owner = {}
    for k, c in enumerate(bc.compiled):
        for g in c.output_gpios:
            owner.setdefault(g, k)
    if owner:
        init = 0
        for c in bc.compiled:
            init |= c.dirs_init()
        lines.append("    // the directions are per GPIO, not per state machine: set the outputs last so that a machine that only reads a GPIO another machine drives cannot turn it into an input")
        for g, k in sorted(owner.items()):
            if (init >> bc.compiled[k].pin(g)) & 1:
                lines.append("    pio_sm_set_consecutive_pindirs(pio, %d, %d, 1, true);" % (k, g))
            else:      # a pin whose direction register starts as an input
                lines.append("    pio_sm_set_consecutive_pindirs(pio, %d, %d, 1, false);       // initial direction of GPIO %d: input (a direction register sets it later)" % (k, g, g))
    lines.append("    pio_enable_sm_mask_in_sync(pio, 0x%x);     // all machines start in the same cycle" % ((1 << len(bc.names)) - 1))
    events = []
    for k, mname in enumerate(bc.names):
        events += [(c, 1, k, ("put", w)) for c, w in sim.tx_feed.get(mname, ()) if bc.compiled[k].tx_depth]
    events += [(c, 0, -1, ("clear", f)) for c, f in sim.irq_clear] + [(c, 1, -1, ("set", f)) for c, f in sim.irq_set]
    if any(e[3][0] in ("set", "clear") for e in events):
        lines.append("    // host events of the simulation case; the CPU cannot hit the cycle, so the order is kept and the time is approximate")
    for c, _, k, (kind, v) in sorted(events, key=lambda e: (e[0], e[1], e[2])):
        if kind == "put":
            lines.append("    pio_sm_put_blocking(pio, %d, 0x%08xu);       // written by the host after cycle %d in the simulation case (machine %s)" % (k, v, c, bc.names[k]))
        elif kind == "set":
            lines.append("    pio->irq_force = 1u << %d;       // host raises flag %d (simulation: at the start of cycle %d)" % (v, v, c))
        else:
            lines.append("    pio_interrupt_clear(pio, %d);     // host lowers flag %d (simulation: at the start of cycle %d)" % (v, v, c))
    rx = [k for k, c in enumerate(bc.compiled) if c.uses_rx]
    if rx:
        lines += ["    while (true) {"]
        for k in rx:
            lines.append('        if (!pio_sm_is_rx_fifo_empty(pio, %d)) printf("%s rx %%08lx\\n", (unsigned long)pio_sm_get(pio, %d));' % (k, bc.names[k], k))
        lines += ["    }", "}", ""]
    else:
        lines += ["    while (true) {", "        tight_loop_contents();", "    }", "}", ""]
    return "\n".join(lines)


def _block_readme(bc, name, project):
    out = ["# %s" % name, "", "Generated by `Block.build(..., project=%r)` of pioweave.block for the %s: %d state machines in one PIO block." % (project, bc.chip, len(bc.names)), "",
           "Machines, load addresses and sizes (the instruction memory has %d words, %d used):" % (MEMORY_WORDS, bc.instructions), ""]
    out += ["- %s" % ln for ln in bc.describe().splitlines()]
    out += ["", "The shared IRQ flags and pins are modelled as described in docs/blocks.rst. bitweave runs one state machine per case: each case of `bitweave.toml` is one machine, with",
            "the effect of the other machines and the host replayed as input waveforms and IRQ events, and the expectations taken from the Amaranth simulation of the whole block.",
            "The combined behaviour is checked against the pioweave emulator and the Amaranth simulation only.", "",
            "    bitweave --config bitweave.toml pio %s" % name]
    if project == "pico-c":
        out += ["    bitweave --config bitweave.toml firmware %s     # needs the Pico SDK and arm-none-eabi-gcc" % name]
    out += ["", "Nothing here has run on a chip."]
    return "\n".join(out) + "\n"
