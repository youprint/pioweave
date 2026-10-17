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
    for c in (os.environ.get("BITWEAVE"), shutil.which("bitweave"), os.path.join(HERE, "..", "..", "..", "target", "release", "bitweave")):
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None


def find_pioasm():
    for c in (os.environ.get("PIOASM"), shutil.which("pioasm"), os.path.join(HERE, "..", "..", "..", ".tools", "bin", "pioasm")):
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None


from pioweave.project import amaranth_run, amaranth_waves  # noqa: E402,F401  (the reference simulation lives next to the project writers)


def _toml_value(v):
    return '"%s"' % v if isinstance(v, str) else ("true" if v is True else "false" if v is False else "%d" % v)


def pio_run(compiled, cycles, stim=None, extra=None, tx=(), tx_feed=(), drain=True):
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
                "rx": [(e["cycle"], e["value"]) for e in sim["rx"]], "rx_left": len(sim["rx_left"]), "rx_left_words": list(sim["rx_left"]), "tx_left": sim["tx_left"]}
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
        setp = platform.buffer(m, "o", "setbus", 0) if s["set_w"] else None
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
                    if k.startswith("set") and k not in ("setx", "sety") and setp is not None:
                        m.d.sync += setp.o.eq(st["value"])
                    if k == "setx":
                        m.d.sync += x.eq(st["value"])
                    if k == "sety":
                        m.d.sync += y.eq(st["value"])
                    if k == "mov":
                        src = {"x": x, "y": y}[st["src"]]
                        m.d.sync += {"x": x, "y": y}[st["dst"]].eq(~src if st["inv"] else src)
                    if k in ("nop", "nop_side", "set", "set_side", "setx", "sety", "mov"):
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
    return spec


def random_factory(spec):
    from pioweave.platform import PLATFORMS

    def factory():
        pl = PLATFORMS[spec["chip"]]()
        res = []
        if spec["set_w"]:
            res.append(Resource("setbus", 0, Pins(" ".join(str(spec["set_base"] + k) for k in range(spec["set_w"])), dir="o")))
        if spec["side_w"]:
            res.append(Resource("sidebus", 0, Pins(" ".join(str(spec["side_base"] + k) for k in range(spec["side_w"])), dir="o")))
        pl.add_resources(res)
        return RandomDesign(spec), pl
    return factory


# ----------------------------------------------------------------------------- random designs with a data path (FIFOs, OSR, ISR)
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
        outp = platform.buffer(m, "o", "outbus", 0)
        inp = platform.buffer(m, "i", "inbus", 0)
        side = platform.buffer(m, "o", "sidebus", 0) if s["side_w"] else None
        if side is not None:
            platform.pio_role(side, "side")
        sh = platform.shifter(m, out_shift=s["out_shift"], in_shift=s["in_shift"])
        x, y = platform.scratch("x"), platform.scratch("y")
        n = len(s["states"])
        with m.FSM():
            for i, st in enumerate(s["states"]):
                fall = "S%d" % ((i + 1) % n)
                with m.State("S%d" % i):
                    k = st["kind"]
                    if "side" in st and side is not None:
                        m.d.sync += side.o.eq(st["side"])
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
                    else:
                        if k == "out_x":
                            sh.out(m, "x", st["n"])
                        elif k == "out_y":
                            sh.out(m, "y", st["n"])
                        elif k == "out_null":
                            sh.out(m, "null", st["n"])
                        elif k == "out_pins":
                            sh.out(m, outp, st["n"])
                        elif k == "in_x":
                            sh.in_(m, "x", st["n"])
                        elif k == "in_y":
                            sh.in_(m, "y", st["n"])
                        elif k == "in_null":
                            sh.in_(m, "null", st["n"])
                        elif k == "in_pins":
                            sh.in_(m, inp, st["n"])
                        elif k == "setx":
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
    spec["tx_feed"] = sorted((r.randint(1, 90), r.getrandbits(32)) for _ in range(r.randint(0, 3)))
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
    return spec


def random_data_factory(spec):
    from pioweave.platform import PLATFORMS

    def factory():
        pl = PLATFORMS[spec["chip"]]()
        res = [Resource("outbus", 0, Pins(" ".join(str(spec["out_base"] + k) for k in range(spec["out_w"])), dir="o")),
               Resource("inbus", 0, Pins(" ".join(str(spec["in_base"] + k) for k in range(spec["in_w"])), dir="i"))]
        if spec["side_w"]:
            res.append(Resource("sidebus", 0, Pins(" ".join(str(spec["side_base"] + k) for k in range(spec["side_w"])), dir="o")))
        pl.add_resources(res)
        return RandomDataDesign(spec), pl
    return factory
