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
    for c in (os.environ.get("BITWEAVE"), shutil.which("bitweave"), os.path.join(HERE, "..", "..", "target", "release", "bitweave")):
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None


def find_pioasm():
    for c in (os.environ.get("PIOASM"), shutil.which("pioasm"), os.path.join(HERE, "..", "..", ".tools", "bin", "pioasm")):
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None


from pio_project import amaranth_waves  # noqa: E402,F401  (the reference simulation lives next to the project writers)


def pio_waves(compiled, cycles, stim=None, extra=None):
    """Run the compiled program in the bitweave PIO simulator; returns {gpio: waveform} for the output GPIOs, or raises."""
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
            lines.append("%s = %d" % (k, v))
        # the PIO's own pin numbers; an input outside the window cannot be read by this program, so its stimulus is not needed
        window = {compiled.pin(g): w for g, w in (stim or {}).items() if 0 <= compiled.pin(g) <= 31}
        if window:
            lines.append("[pio.p.sim.pins_in]")
            for pin, w in window.items():
                lines.append('%d = "%s"' % (pin, w))
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
        return {g: "".join(str((t["pad"] >> compiled.pin(g)) & 1) for t in trace) for g in compiled.output_gpios}
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
    from platform_rp import PLATFORMS

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
