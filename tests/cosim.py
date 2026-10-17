# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Co-simulation helpers for tests/test_pio_compile.py run an Amaranth design in the Amaranth simulator and its compiled PIO program in
the bitweave PIO simulator, and return the pin waveforms of both as strings (one character per clock cycle).

Alignment: the Amaranth FSM state at clock cycle t does what the PIO instruction executed in cycle t does. Amaranth outputs are registers, so the value
sampled after the t-th clock edge is the effect of cycle t, which is the pad level the PIO model reports for cycle t. Inputs are set before the edge
of cycle t and are what the instruction of cycle t reads (the PIO model's input at cycle t, with the 2-flop synchroniser off).
"""
import json
import os
import shutil
import subprocess
import tempfile

from amaranth.hdl import ClockDomain, Module
from amaranth.hdl._ir import Fragment
from amaranth.sim import Simulator

HERE = os.path.dirname(os.path.abspath(__file__))


def find_bitweave():
    for c in (os.environ.get("BITWEAVE"), shutil.which("bitweave"), os.path.join(HERE, "..", "..", "bitweave", "target", "release", "bitweave")):
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None


def find_pioasm():
    for c in (os.environ.get("PIOASM"), shutil.which("pioasm"), os.path.join(HERE, "..", "..", "bitweave", ".tools", "bin", "pioasm")):
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None


from pioweave.project import amaranth_run, amaranth_waves  # noqa: E402,F401  (the reference simulation lives next to the project writers)


def _toml_value(v):
    return '"%s"' % v if isinstance(v, str) else ("true" if v is True else "false" if v is False else "%d" % v)


def emu_run(compiled, cycles, stim=None, tx=(), tx_feed=(), drain=True, irq_set=(), irq_clear=()):
    """Run the compiled program in the pioweave emulator like `pioweave.emulator.run_compiled` does, with the host IRQ events of a bitweave case (`irq_clear` first, then
    `irq_set`, at the start of the cycle, before the instruction). Returns {"waves", "rx" [(cycle, word)], "rx_left_words", "rx_left", "tx_left", "irq" (the flags after each cycle)}."""
    from pioweave.builder.program import CHIP_VERSION
    from pioweave.emulator.core import PioBlock
    cfg = dict(compiled.sim_config())
    window = {compiled.pin(g): w for g, w in (stim or {}).items() if 0 <= compiled.pin(g) <= 31}
    block = PioBlock(version=CHIP_VERSION[compiled.chip], gpio_base=cfg.pop("gpio_base", 0), sync_cycles=0)
    loaded = block.load(compiled.program)
    sm = block.sm[0]
    if "sideset_base" in cfg:
        cfg["side_base"] = cfg.pop("sideset_base")
    x, y, dirs = cfg.pop("x", 0), cfg.pop("y", 0), cfg.pop("dirs", 0)
    sm.configure(program=loaded, **cfg)
    sm.enable()
    sm.x, sm.y = x, y
    block.dirs = dirs
    for w in tx:
        sm.put(w)
    feeds = sorted(tx_feed)
    pads, rx, irq, dirs_seen = [], [], [], []
    for t in range(cycles):
        for c, w in feeds:
            if c == t:
                sm.put(w)
        for c, f in irq_clear:
            if c == t:
                block.irq &= ~(1 << f)
        for c, f in irq_set:
            if c == t:
                block.irq |= 1 << f
        v = 0
        for pin, w in window.items():
            if w[min(t, len(w) - 1)] == "1":
                v |= 1 << pin
        block.ext = v
        block.step()
        if drain:
            while len(sm.rx):
                rx.append((t, sm.get()))
        pads.append(block.pads())
        dirs_seen.append(block.dirs)
        irq.append(block.irq)
    return {"waves": {g: "".join(str((p >> compiled.pin(g)) & 1) for p in pads) for g in compiled.output_gpios}, "rx": rx, "rx_left_words": list(sm.rx.q),
            "rx_left": len(sm.rx), "tx_left": len(sm.tx), "irq": irq,
            "dirs": {g: "".join(str((d >> compiled.pin(g)) & 1) for d in dirs_seen) for g in compiled.dynamic_gpios}}


def pio_run(compiled, cycles, stim=None, extra=None, tx=(), tx_feed=(), drain=True, irq_set=(), irq_clear=()):
    """Run the compiled program in the bitweave PIO simulator; returns {"waves": {gpio: waveform} for the output GPIOs, "rx": [(cycle, word)], "rx_left", "tx_left"}
    or raises."""
    bw, pioasm = find_bitweave(), find_pioasm()
    d = tempfile.mkdtemp(prefix="pio_cosim_")
    try:
        src = os.path.join(d, "p.pio")
        with open(src, "w") as fh:
            fh.write(compiled.program.pio_text())
        case = dict(compiled.sim_config())
        case.update(extra or {})
        lines = ['[project]', 'name = "cosim"', 'build_dir = "build"', '[tools]', 'bin_dirs = ["%s"]' % os.path.dirname(pioasm),
                 '[pio.p]', 'source = "%s"' % src, 'chip = "%s"' % compiled.chip, '[[pio.p.sim]]', 'name = "c"', 'cycles = %d' % cycles, 'trace = true']
        for k, v in case.items():
            lines.append("%s = %s" % (k, _toml_value(v)))
        if tx:
            lines.append("tx = [%s]" % ", ".join(str(w) for w in tx))
        if not drain:
            lines.append("rx_drain = false")
        # the PIO's own pin numbers; an input outside the window cannot be read by this program, so its stimulus is not needed
        window = {compiled.pin(g): w for g, w in (stim or {}).items() if 0 <= compiled.pin(g) <= 31}
        if window:
            lines.append("[pio.p.sim.pins_in]")
            for pin, w in window.items():
                lines.append('%d = "%s"' % (pin, w))
        for c, w in tx_feed:
            lines += ["[[pio.p.sim.tx_feed]]", "cycle = %d" % c, "value = %d" % w]
        for key, events in (("irq_set", irq_set), ("irq_clear", irq_clear)):
            for c, f in events:
                lines += ["[[pio.p.sim.%s]]" % key, "cycle = %d" % c, "flag = %d" % f]
        with open(os.path.join(d, "bitweave.toml"), "w") as fh:
            fh.write("\n".join(lines) + "\n")
        r = subprocess.run([bw, "--config", os.path.join(d, "bitweave.toml"), "--json", "pio", "p"], capture_output=True, text=True)
        out = json.loads(r.stdout)
        with open(out["record"]) as fh:
            rec = json.load(fh)
        sim = rec["extra"]["pio:p"]["sim"][0]
        if sim.get("error"):
            raise RuntimeError("PIO simulation error: %s" % sim["error"])
        trace = sim["trace"]
        return {"waves": {g: "".join(str((t["pad"] >> compiled.pin(g)) & 1) for t in trace) for g in compiled.output_gpios},
                "rx": [(e["cycle"], e["value"]) for e in sim["rx"]], "rx_left": len(sim["rx_left"]), "rx_left_words": list(sim["rx_left"]), "tx_left": sim["tx_left"],
                "irq": [t["irq"] for t in trace],
                "dirs": {g: "".join(str((t["dirs"] >> compiled.pin(g)) & 1) for t in trace) for g in compiled.dynamic_gpios}}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def same_dirs(a, b):
    """The pin directions per cycle of two runs (`dirs`: {gpio: waveform} of the pins of groups with a direction register); the pads of those pins are in ``waves``."""
    return a["dirs"] == b["dirs"]


def same_rx(a, b, drain=True):
    """The RX traffic of the Amaranth run `a` and the PIO simulator run `b`: with draining, words and cycles; without, the words that are left in the FIFO."""
    return a["rx"] == b["rx"] if drain else [w for _, w in a["rx"]] == b["rx_left_words"]


def pio_waves(compiled, cycles, stim=None, extra=None):
    """The pin waveforms of `pio_run`."""
    return pio_run(compiled, cycles, stim, extra)["waves"]


# ----------------------------------------------------------------------------- random designs inside the accepted subset
import random  # noqa: E402

from amaranth.build import Pins, Resource  # noqa: E402
from amaranth.hdl import Elaboratable  # noqa: E402

KINDS = ["nop", "nop", "nop", "nop_side", "set", "set", "set_side", "setx", "sety", "mov", "jmp", "jmp_side", "jx", "jy", "jnx", "jny", "jxy", "jpin", "wait"]


class RandomDesign(Elaboratable):
    """A random FSM of the accepted subset; `spec` is a plain dict so a failing seed can be printed and replayed."""

    def __init__(self, spec):
        self.spec = spec

    def elaborate(self, platform):
        from amaranth.hdl import Module
        s = self.spec
        m = Module()
        setp = platform.buffer(m, "io" if "set_dirs" in s else "o", "setbus", 0) if s["set_w"] else None
        sd = platform.directions(setp, init=s["set_dirs"]) if "set_dirs" in s else None          # pin directions: `set pindirs` states
        side = platform.buffer(m, "o", "sidebus", 0) if s["side_w"] else None
        if side is not None:
            platform.pio_role(side, "side")
        x, y = platform.scratch("x"), platform.scratch("y")
        p16 = platform.buffer(m, "i", "gpio", s["in_pins"][0])
        p17 = platform.buffer(m, "i", "gpio", s["in_pins"][1])
        n = len(s["states"])
        with m.FSM():
            for i, st in enumerate(s["states"]):
                fall = "S%d" % ((i + 1) % n)
                tgt = "S%d" % st.get("target", 0)
                with m.State("S%d" % i):
                    k = st["kind"]
                    if "side" in st and side is not None:
                        m.d.sync += side.o.eq(st["side"])
                    if k.startswith("set") and k not in ("setx", "sety", "set_dirs") and setp is not None:
                        m.d.sync += setp.o.eq(st["value"])
                    if k == "setx":
                        m.d.sync += x.eq(st["value"])
                    if k == "sety":
                        m.d.sync += y.eq(st["value"])
                    if k == "set_dirs":
                        m.d.sync += sd.eq(st["value"])
                    if k == "mov":
                        src = {"x": x, "y": y}[st["src"]]
                        m.d.sync += {"x": x, "y": y}[st["dst"]].eq(~src if st["inv"] else src)
                    if k in ("nop", "nop_side", "set", "set_side", "setx", "sety", "mov", "set_dirs"):
                        m.next = fall
                    elif k in ("jmp", "jmp_side"):
                        m.next = tgt
                    elif k in ("jx", "jy"):
                        r = x if k == "jx" else y
                        m.d.sync += r.eq(r - 1)
                        if st["layout"]:
                            with m.If(r != 0):
                                m.next = tgt
                            with m.Else():
                                m.next = fall
                        else:
                            with m.If(r == 0):
                                m.next = fall
                            with m.Else():
                                m.next = tgt
                    elif k in ("jnx", "jny"):
                        r = x if k == "jnx" else y
                        if st["layout"]:
                            with m.If(r == 0):
                                m.next = tgt
                            with m.Else():
                                m.next = fall
                        else:
                            with m.If(r != 0):
                                m.next = fall
                            with m.Else():
                                m.next = tgt
                    elif k == "jxy":
                        if st["layout"]:
                            with m.If(x != y):
                                m.next = tgt
                            with m.Else():
                                m.next = fall
                        else:
                            with m.If(x == y):
                                m.next = fall
                            with m.Else():
                                m.next = tgt
                    elif k == "jpin":
                        if st["layout"]:
                            with m.If(p16.i):
                                m.next = tgt
                            with m.Else():
                                m.next = fall
                        else:
                            with m.If(~p16.i):
                                m.next = fall
                            with m.Else():
                                m.next = tgt
                    elif k == "wait":
                        pin = p16 if st["pin"] == 0 else p17
                        with m.If(pin.i if st["level"] else ~pin.i):
                            m.next = fall
        return m


def random_spec(seed):
    r = random.Random(seed)
    n = r.randint(2, 12)
    chip = r.choice(["rp2040", "rp2350", "rp2350a", "rp2350b", "rp2354b"])
    hi = chip.endswith("b") and r.random() < .5        # the 48-GPIO parts: use GPIO 20 to 45 so that the PIO window starts at GPIO 16
    spec = {"chip": chip, "set_w": r.choice([0, 1, 2, 3]), "side_w": r.choice([0, 0, 1, 2]), "set_base": r.randint(20, 24) if hi else r.randint(0, 3),
            "side_base": 40 if hi else 8, "in_pins": (44, 45) if hi else (16, 17), "states": []}
    for i in range(n):
        k = r.choice(KINDS)
        if k.startswith("set") and k not in ("setx", "sety") and not spec["set_w"]:
            k = "nop"
        if k in ("nop_side", "set_side", "jmp_side") and not spec["side_w"]:
            k = "nop"
        st = {"kind": k, "layout": r.random() < .5, "pin": r.choice([0, 1]), "level": r.random() < .5, "src": r.choice("xy"), "inv": r.random() < .4}
        st["dst"] = r.choice("xy")
        fall = (i + 1) % n
        st["target"] = r.choice([j for j in range(n) if j != fall] or [fall])
        if k in ("set", "set_side"):
            st["value"] = r.randrange(1 << spec["set_w"])
        if k in ("setx", "sety"):
            st["value"] = r.randrange(0, 6)
        if spec["side_w"] and (k in ("nop_side", "set_side", "jmp_side") or r.random() < .25):
            st["side"] = r.randrange(1 << spec["side_w"])
        if k in ("jmp", "jmp_side") and st["target"] == fall:
            st["kind"] = "nop"
        spec["states"].append(st)
    spec["stim"] = {g: "".join(r.choice("01") for _ in range(r.randint(10, 40))) for g in spec["in_pins"]}
    _extend_spec_dirs(spec, seed)
    return spec


def _extend_spec_dirs(spec, seed):
    """From a second random stream (so the designs of the earlier generator stay as they were): half of the designs with a set bus make it bidirectional, with a direction
    register, and some states become `set pindirs`. The pad of those pins is the output where the pin is an output and the stimulus elsewhere."""
    r = random.Random(130_000 + seed)
    if not spec["set_w"] or r.random() < .5:
        return
    w = spec["set_w"]
    spec["set_dirs"] = r.randrange(1 << w)
    for st in spec["states"]:
        if st["kind"] in ("nop", "nop_side", "setx", "sety", "mov") and r.random() < .5:
            st["kind"] = "set_dirs"
            st["value"] = r.randrange(1 << w)
    for k in range(w):
        spec["stim"][spec["set_base"] + k] = "".join(r.choice("01") for _ in range(r.randint(10, 40)))


def random_factory(spec):
    from pioweave.platform import PLATFORMS

    def factory():
        pl = PLATFORMS[spec["chip"]]()
        res = []
        if spec["set_w"]:
            res.append(Resource("setbus", 0, Pins(" ".join(str(spec["set_base"] + k) for k in range(spec["set_w"])), dir="io" if "set_dirs" in spec else "o")))
        if spec["side_w"]:
            res.append(Resource("sidebus", 0, Pins(" ".join(str(spec["side_base"] + k) for k in range(spec["side_w"])), dir="o")))
        pl.add_resources(res)
        return RandomDesign(spec), pl
    return factory


# ----------------------------------------------------------------------------- random designs with a data path (FIFOs, OSR, ISR)
OUT_IN_KINDS = ("out_x", "out_y", "out_null", "out_pins", "out_pindirs", "out_isr", "in_x", "in_y", "in_null", "in_pins", "in_isr", "in_osr")
DATA_KINDS = ["nop", "nop", "pull_b", "pull_b", "pull_nb", "push_b", "push_nb", "out_x", "out_y", "out_null", "out_pins", "out_pins", "in_x", "in_y", "in_null",
              "in_pins", "in_pins", "setx", "sety", "mov", "jx"]


class RandomDataDesign(Elaboratable):
    """A random FSM with PULL / PUSH / OUT / IN states, one operation per state, in the shape the compiler accepts."""

    def __init__(self, spec):
        self.spec = spec

    def elaborate(self, platform):
        from amaranth.hdl import Module
        s = self.spec
        m = Module()
        pd = s.get("pd")          # pin directions: the OUT group (and the side-set group) are bidirectional with a direction register
        outp = platform.buffer(m, "io" if pd else "o", "outbus", 0)
        od = platform.directions(outp, init=pd["out_init"]) if pd else None
        inp = platform.buffer(m, "i", "inbus", 0)
        side = platform.buffer(m, "io" if pd and pd["side_dirs"] else "o", "sidebus", 0) if s["side_w"] else None
        sdir = platform.directions(side, init=pd["side_init"]) if side is not None and pd and pd["side_dirs"] else None      # the side-set writes pin directions
        if side is not None and sdir is None:
            platform.pio_role(side, "side")
        sh = platform.shifter(m, out_shift=s["out_shift"], in_shift=s["in_shift"], **s.get("shifter", {}))
        x, y = platform.scratch("x"), platform.scratch("y")
        n = len(s["states"])
        with m.FSM():
            for i, st in enumerate(s["states"]):
                fall = "S%d" % ((i + 1) % n)
                with m.State("S%d" % i):
                    k = st["kind"]
                    if "side" in st and side is not None:
                        m.d.sync += (side.o if sdir is None else sdir).eq(st["side"])
                    if k == "pull_b":
                        with sh.pull(m):
                            m.next = fall
                    elif k == "pull_nb":
                        with sh.pull(m, block=False):
                            m.next = fall
                    elif k == "push_b":
                        with sh.push(m):
                            m.next = fall
                    elif k == "push_nb":
                        with sh.push(m, block=False):
                            m.next = fall
                    elif k in ("pull_ie", "pull_ie_nb"):
                        with sh.pull(m, block=k == "pull_ie", ifempty=True):
                            m.next = fall
                    elif k in ("push_if", "push_if_nb"):
                        with sh.push(m, block=k == "push_if", iffull=True):
                            m.next = fall
                    elif k == "movg":
                        ends = {"x": "x", "y": "y", "osr": "osr", "isr": "isr"}
                        sh.mov(m, outp if st["mdst"] == "pins" else od if st["mdst"] == "pindirs" else ends[st["mdst"]], inp if st["msrc"] == "pins" else {"null": "null", "status": "status", **ends}[st["msrc"]], st["mop"])
                        m.next = fall
                    elif k == "set_dirs":
                        m.d.sync += od.eq(st["value"])                  # set pindirs, N
                        m.next = fall
                    elif k == "wait_pin":
                        bit = inp.i[st["bit"]]
                        with m.If(bit if st["level"] else ~bit):          # wait 1|0 pin N (relative to the IN base)
                            m.next = fall
                    elif k in ("irq_set", "irq_clear"):
                        sh.irq(m, st["flag"], k[4:], rel=st["rel"])
                        m.next = fall
                    elif k == "irq_wait":
                        with sh.irq(m, st["flag"], "wait", rel=st["rel"]):
                            m.next = fall
                    elif k == "wait_irq":
                        with sh.wait_irq(m, st["flag"], st["pol"], rel=st["rel"]):
                            m.next = fall
                    elif k == "josre":
                        with m.If(sh.osr_not_empty):
                            m.next = "S%d" % st["target"]
                        with m.Else():
                            m.next = fall
                    elif k in OUT_IN_KINDS:
                        shift = sh.out if k.startswith("out") else sh.in_
                        target = {"x": "x", "y": "y", "null": "null", "isr": "isr", "osr": "osr", "pins": outp if k.startswith("out") else inp, "pindirs": od}[k.split("_")[1]]
                        auto = s.get("shifter", {}).get("autopull" if k.startswith("out") else "autopush")
                        if auto or st.get("with"):
                            with shift(m, target, st["n"]):          # the body runs when the instruction completes
                                m.next = fall
                        else:
                            shift(m, target, st["n"])
                            m.next = fall
                    else:
                        if k == "setx":
                            m.d.sync += x.eq(st["value"])
                        elif k == "sety":
                            m.d.sync += y.eq(st["value"])
                        elif k == "mov":
                            src = {"x": x, "y": y}[st["src"]]
                            m.d.sync += {"x": x, "y": y}[st["dst"]].eq(~src if st["inv"] else src)
                        if k == "jx":
                            m.d.sync += x.eq(x - 1)
                            with m.If(x != 0):
                                m.next = "S%d" % st["target"]
                            with m.Else():
                                m.next = fall
                        else:
                            m.next = fall
        return m


def random_data_spec(seed):
    r = random.Random(10_000 + seed)
    n = r.randint(3, 10)
    chip = r.choice(["rp2040", "rp2350", "rp2350b", "rp2354"])
    hi = chip.endswith("b") and r.random() < .5
    spec = {"chip": chip, "out_w": r.randint(1, 4), "in_w": r.randint(1, 3), "side_w": r.choice([0, 0, 1]), "out_base": r.randint(20, 24) if hi else r.randint(0, 3),
            "in_base": 33 if hi else 10, "side_base": 40 if hi else 8, "out_shift": r.choice(["left", "right"]), "in_shift": r.choice(["left", "right"]),
            "drain": r.random() < .7, "tx": [r.getrandbits(32) for _ in range(r.randint(0, 4))], "states": []}
    feed = sorted((r.randint(1, 90), r.getrandbits(32)) for _ in range(r.randint(0, 3)))
    spec["tx_feed"] = [e for k, e in enumerate(feed) if k == 0 or e[0] != feed[k - 1][0]]       # the Amaranth testbench writes one word per cycle
    for i in range(n):
        k = r.choice(DATA_KINDS)
        st = {"kind": k, "src": r.choice("xy"), "dst": r.choice("xy"), "inv": r.random() < .4, "value": r.randrange(0, 6)}
        if k in ("out_x", "out_y", "out_null", "in_x", "in_y", "in_null"):
            st["n"] = r.choice([1, 2, 5, 8, 16, 31, 32, r.randint(1, 32)])
        elif k == "out_pins":
            st["n"] = r.choice([spec["out_w"], spec["out_w"], r.randint(1, 8)])
        elif k == "in_pins":
            st["n"] = r.choice([spec["in_w"], r.randint(1, spec["in_w"])])
        if k == "jx":
            st["target"] = r.randint(0, i)
            if st["target"] == (i + 1) % n:         # both branches would go to the same state
                st["kind"] = "nop"
        if spec["side_w"] and r.random() < .3:
            st["side"] = r.randrange(1 << spec["side_w"])
        spec["states"].append(st)
    spec["stim"] = {spec["in_base"] + j: "".join(r.choice("01") for _ in range(r.randint(10, 40))) for j in range(spec["in_w"])}
    _extend_data_spec(spec, seed)
    _extend_dir_spec(spec, seed)
    return spec


def _extend_dir_spec(spec, seed):
    """Pin directions (a third random stream, so the designs of the earlier generators stay as they were): in 45 percent of the designs the OUT group is bidirectional with a
    direction register, some states become `out pindirs`, `mov pindirs` (PIO version 1) or `set pindirs`, and the side-set writes directions in a third of those. The pad of the
    pins is the output where the pin is an output and the stimulus elsewhere."""
    r = random.Random(170_000 + seed)
    if r.random() >= .45:
        return
    side_dirs = bool(spec["side_w"]) and r.random() < .4
    spec["pd"] = {"out_init": r.randrange(1 << spec["out_w"]), "side_dirs": side_dirs, "side_init": r.randrange(1 << spec["side_w"]) if spec["side_w"] else 0}
    n = len(spec["states"])
    for st in spec["states"]:
        if st["kind"] in ("nop", "setx", "sety", "mov", "out_pins", "out_x", "out_y", "out_null") and r.random() < .3:
            k = r.choice(["out_pindirs", "out_pindirs", "mov_pindirs", "set_dirs"])
            if k == "set_dirs" and side_dirs:
                k = "out_pindirs"          # the set group would be the side-set group's
            if k == "mov_pindirs" and spec["chip"] == "rp2040":
                k = "out_pindirs"
            if k == "mov_pindirs":
                st.update(kind="movg", mdst="pindirs", msrc=r.choice(["x", "y", "null", "isr", "osr", "pins"] + (["status"] if "status" in spec["shifter"] else [])), mop=r.choice(["none", "invert", "reverse"]))
            elif k == "set_dirs":
                st.update(kind="set_dirs", value=r.randrange(1 << spec["out_w"]))
            else:
                st.update(kind="out_pindirs", n=r.choice([spec["out_w"], spec["out_w"], r.randint(1, 8)]))
    for j in range(spec["out_w"]):
        spec["stim"][spec["out_base"] + j] = "".join(r.choice("01") for _ in range(r.randint(10, 40)))
    if side_dirs:
        for j in range(spec["side_w"]):
            spec["stim"][spec["side_base"] + j] = "".join(r.choice("01") for _ in range(r.randint(10, 40)))


NEW_DATA_KINDS = ["pull_ie", "pull_ie_nb", "push_if", "push_if_nb", "out_isr", "in_isr", "in_osr", "movg", "movg", "movg", "movg", "josre", "irq_set", "irq_set", "irq_clear",
                  "irq_wait", "wait_irq", "wait_irq", "wait_pin", "wait_pin"]
MOV_DSTS = ["x", "y", "osr", "isr", "pins"]
MOV_SRCS = ["x", "y", "null", "status", "isr", "osr", "pins"]


def _extend_data_spec(spec, seed):
    """Half of the seeds keep the original design; the others switch on the newer features (a second random stream, so the first half is the design the
    generator always drew): autopull / autopush with thresholds, and `with` forms of out / in."""
    r = random.Random(90_000 + seed)
    spec["shifter"] = {}
    spec["irq_set"], spec["irq_clear"] = [], []
    if r.random() < .5:
        return
    sh = spec["shifter"]
    if r.random() < .6:
        sh["autopull"], sh["pull_thresh"] = True, r.choice([32, 8, 16, r.randint(1, 32)])
    if r.random() < .6:
        sh["autopush"], sh["push_thresh"] = True, r.choice([32, 8, 16, r.randint(1, 32)])
    for key in ("pull_thresh", "push_thresh"):          # also without autopull / autopush: pull ifempty, push iffull and jmp !osre use them
        if key not in sh and r.random() < .7:
            sh[key] = r.choice([8, 16, r.randint(1, 32)])
    sh["fifo_join"] = r.choice(["none", "none", "tx", "rx"])
    if sh["fifo_join"] == "tx":              # the TX FIFO holds eight words
        spec["tx"] = spec["tx"] + [r.getrandbits(32) for _ in range(r.randint(0, 4))]
    sh["status"] = r.choice(["tx", "rx"] + ([] if spec["chip"] == "rp2040" else ["irq"]))
    sh["status_n"] = r.randint(0, 7 if sh["status"] == "irq" else 6)
    spec["irq_set"] = sorted((r.randint(0, 90), r.randint(0, 3)) for _ in range(r.randint(0, 5)))          # the host raises and lowers IRQ flags (flags 0 to 3 mostly, to interact)
    spec["irq_clear"] = sorted((r.randint(0, 90), r.randint(0, 3)) for _ in range(r.randint(0, 5)))
    n = len(spec["states"])
    for i, st in enumerate(spec["states"]):
        st["with"] = r.random() < .5
        if r.random() < .35:           # a newer kind in place of the old one
            k = r.choice(NEW_DATA_KINDS)
            st["kind"] = k
            if k in ("out_isr", "in_isr", "in_osr"):
                st["n"] = r.choice([1, 2, 5, 8, 16, 31, 32, r.randint(1, 32)])
            if k == "movg":
                st["mdst"] = r.choice(MOV_DSTS)
                st["msrc"] = r.choice([c for c in MOV_SRCS if c != "pins" or spec["chip"] != "rp2040"])
                st["mop"] = r.choice(["none", "none", "invert", "reverse"])
            if k == "wait_pin":
                st["bit"], st["level"] = r.randrange(spec["in_w"]), r.random() < .5
            if k.startswith("irq") or k == "wait_irq":
                st["flag"], st["rel"], st["pol"] = r.choice([0, 1, 2, 3, r.randint(0, 7)]), r.random() < .3, r.randint(0, 1)
            if k == "josre":
                st["target"] = r.randint(0, i)
                if st["target"] == (i + 1) % n:
                    st["kind"] = "nop"


def random_data_factory(spec):
    from pioweave.platform import PLATFORMS

    def factory():
        pl = PLATFORMS[spec["chip"]]()
        pd = spec.get("pd")
        res = [Resource("outbus", 0, Pins(" ".join(str(spec["out_base"] + k) for k in range(spec["out_w"])), dir="io" if pd else "o")),
               Resource("inbus", 0, Pins(" ".join(str(spec["in_base"] + k) for k in range(spec["in_w"])), dir="i"))]
        if spec["side_w"]:
            res.append(Resource("sidebus", 0, Pins(" ".join(str(spec["side_base"] + k) for k in range(spec["side_w"])), dir="io" if pd and pd["side_dirs"] else "o")))
        pl.add_resources(res)
        return RandomDataDesign(spec), pl
    return factory
