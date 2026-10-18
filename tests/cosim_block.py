# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE.txt)
"""Random block designs for the co-simulation tests: two to four small machines that handshake through the shared IRQ flags, write the same GPIOs (set pins, so
the highest-numbered machine wins) and read each other's output pins. A spec is a plain dict so that a failing seed can be printed and replayed.

The machines draw from the same kinds of states as tests/cosim.py (set, jump on X, IRQ set / clear / wait, wait irq, wait pin, pull, push, out, in, with and without
autopull / autopush) but with the flag numbers 0 to 3 so that the machines interact, and with the rules a block adds: only ``set pins`` (an instruction that cannot stall)
writes a GPIO that several machines write, side-set and ``out pins`` use pins of their own, and ``rel`` is not used.
"""
import random

from amaranth.build import Pins, Resource
from amaranth.hdl import Elaboratable, Module

from pioweave.block import Block, BlockSim, Machine
from pioweave.platform import PLATFORMS

KINDS = ["nop", "set", "set", "set", "set", "set", "setx", "sety", "jx", "jmp", "mov", "irq_set", "irq_set", "irq_clear", "irq_wait", "wait_irq", "wait_irq", "wait_pin",
         "pull_b", "pull_nb", "push_b", "push_nb", "out_pins", "out_x", "in_pins", "in_x", "josre"]
STALLING = {"irq_wait", "wait_irq", "wait_pin", "pull_b", "push_b"}


class RandomMachine(Elaboratable):
    """One random machine; `spec` describes it (pins, shifter configuration and states)."""

    def __init__(self, spec):
        self.spec = spec

    def elaborate(self, platform):
        s = self.spec
        m = Module()
        res = []
        for key, name, d in (("set", "setbus", "o"), ("out", "outbus", "o"), ("side", "sidebus", "o"), ("inp", "inbus", "i")):
            if s[key]:
                res.append(Resource(name, 0, Pins(" ".join(str(s[key][0] + k) for k in range(s[key][1])), dir=d)))
        platform.add_resources(res)
        setp = platform.buffer(m, "o", "setbus", 0) if s["set"] else None
        outp = platform.buffer(m, "o", "outbus", 0) if s["out"] else None
        side = platform.buffer(m, "o", "sidebus", 0) if s["side"] else None
        if side is not None:
            platform.pio_role(side, "side")
        inp = platform.buffer(m, "i", "inbus", 0) if s["inp"] else None
        sh = platform.shifter(m, out_shift=s["out_shift"], in_shift=s["in_shift"], **s["shifter"])
        x, y = platform.scratch("x"), platform.scratch("y")
        n = len(s["states"])
        with m.FSM():
            for i, st in enumerate(s["states"]):
                fall = "S%d" % ((i + 1) % n)
                with m.State("S%d" % i):
                    k = st["kind"]
                    if "side" in st and side is not None:
                        m.d.sync += side.o.eq(st["side"])
                    if k == "set":
                        m.d.sync += setp.o.eq(st["value"])
                        m.next = fall
                    elif k == "setx":
                        m.d.sync += x.eq(st["value"])
                        m.next = fall
                    elif k == "sety":
                        m.d.sync += y.eq(st["value"])
                        m.next = fall
                    elif k == "mov":
                        src = {"x": x, "y": y}[st["src"]]
                        m.d.sync += {"x": x, "y": y}[st["dst"]].eq(~src if st["inv"] else src)
                        m.next = fall
                    elif k == "jx":
                        m.d.sync += x.eq(x - 1)
                        with m.If(x != 0):
                            m.next = "S%d" % st["target"]
                        with m.Else():
                            m.next = fall
                    elif k == "jmp":
                        m.next = "S%d" % st["target"]
                    elif k == "josre":
                        with m.If(sh.osr_not_empty):
                            m.next = "S%d" % st["target"]
                        with m.Else():
                            m.next = fall
                    elif k in ("irq_set", "irq_clear"):
                        sh.irq(m, st["flag"], k[4:])
                        m.next = fall
                    elif k == "irq_wait":
                        with sh.irq(m, st["flag"], "wait"):
                            m.next = fall
                    elif k == "wait_irq":
                        with sh.wait_irq(m, st["flag"], st["pol"]):
                            m.next = fall
                    elif k == "wait_pin":
                        bit = inp.i[st["bit"]]
                        with m.If(bit if st["level"] else ~bit):
                            m.next = fall
                    elif k in ("pull_b", "pull_nb"):
                        with sh.pull(m, block=k == "pull_b"):
                            m.next = fall
                    elif k in ("push_b", "push_nb"):
                        with sh.push(m, block=k == "push_b"):
                            m.next = fall
                    elif k in ("out_pins", "out_x", "in_pins", "in_x"):
                        shift = sh.out if k.startswith("out") else sh.in_
                        target = {"pins": outp if k.startswith("out") else inp, "x": "x"}[k.split("_")[1]]
                        auto = s["shifter"].get("autopull" if k.startswith("out") else "autopush")
                        if auto or st.get("with"):
                            with shift(m, target, st["n"]):
                                m.next = fall
                        else:
                            shift(m, target, st["n"])
                            m.next = fall
                    else:
                        m.next = fall
        return m


def random_machine_spec(r, k, chip, driven):
    """The spec of machine `k`. `driven` is the set of GPIOs that the earlier machines write (to read some of them)."""
    spec = {"set": None, "out": None, "side": None, "inp": None, "out_shift": r.choice(["left", "right"]), "in_shift": r.choice(["left", "right"]), "shifter": {}}
    if r.random() < .8:
        spec["set"] = (r.randint(0, 1), r.randint(1, 2))              # GPIO 0 to 2: shared with the other machines
    if r.random() < .5:
        spec["out"] = (8 + 4 * k, r.randint(1, 3))                    # pins of their own
    if r.random() < .4:
        spec["side"] = (24 + 2 * k, r.randint(1, 2))
    own = set()
    for key in ("set", "out", "side"):
        if spec[key]:
            own.update(range(spec[key][0], spec[key][0] + spec[key][1]))
    if r.random() < .8:
        w = r.randint(1, 3)
        for _ in range(20):
            base = r.choice([0, 1, 2, 3, 8, 12, 16, 20, 28]) if driven and r.random() < .6 else 16 + 2 * k + r.randint(0, 6)
            if not own & set(range(base, base + w)) and base + w <= 32:
                spec["inp"] = (base, w)
                break
    sh = spec["shifter"]
    if r.random() < .3:
        sh["autopull"], sh["pull_thresh"] = True, r.choice([8, 16, 32])
    if r.random() < .3:
        sh["autopush"], sh["push_thresh"] = True, r.choice([8, 16, 32])
    if r.random() < .5:
        sh["pull_thresh"] = sh.get("pull_thresh", r.choice([8, 16]))
    if chip != "rp2040" and r.random() < .2:
        sh["status"], sh["status_n"] = "irq", r.randint(0, 3)
    n = r.randint(3, 8)
    kinds = [k_ for k_ in KINDS if not (k_ == "set" and not spec["set"]) and not (k_.startswith("out_pins") and not spec["out"]) and not (k_ in ("in_pins", "wait_pin") and not spec["inp"])]
    states = []
    for i in range(n):
        kind = r.choice(kinds)
        st = {"kind": kind, "src": r.choice("xy"), "dst": r.choice("xy"), "inv": r.random() < .4, "value": r.randrange(0, 6), "with": r.random() < .5}
        if kind == "set":
            st["value"] = r.randrange(1 << spec["set"][1])
        if kind in ("irq_set", "irq_clear", "irq_wait", "wait_irq"):
            st["flag"], st["pol"] = r.choice([0, 1, 2, 3, 3, r.randint(0, 7)]), r.randint(0, 1)
        if kind == "wait_pin":
            st["bit"], st["level"] = r.randrange(spec["inp"][1]), r.random() < .5
        if kind in ("out_x", "in_x"):
            st["n"] = r.choice([1, 2, 8, 16, 32, r.randint(1, 32)])
        elif kind == "out_pins":
            st["n"] = r.choice([spec["out"][1], r.randint(1, 8)])
        elif kind == "in_pins":
            st["n"] = r.choice([spec["inp"][1], r.randint(1, spec["inp"][1])])
        if kind in ("jx", "jmp", "josre"):
            st["target"] = r.randint(0, i)
            if st["target"] == (i + 1) % n:
                st["kind"] = "nop" if kind != "jmp" else "nop"
        if spec["side"] and st["kind"] not in STALLING and r.random() < .4:
            st["side"] = r.randrange(1 << spec["side"][1])
        states.append(st)
    spec["states"] = states
    return spec


def random_block_spec(seed):
    r = random.Random(70_000 + seed)
    chip = r.choice(["rp2040", "rp2350", "rp2354"])
    count = r.randint(2, 4)
    machines, driven = [], set()
    for k in range(count):
        ms = random_machine_spec(r, k, chip, driven)
        if ms["set"]:
            driven.update(range(ms["set"][0], ms["set"][0] + ms["set"][1]))
        if ms["out"]:
            driven.update(range(ms["out"][0], ms["out"][0] + ms["out"][1]))
        machines.append(ms)
    spec = {"chip": chip, "machines": machines, "cycles": 120, "stim": {}, "tx": {}, "tx_feed": {}, "drain": {}}
    for g in range(32):
        if g not in driven:
            spec["stim"][g] = "".join(r.choice("01") for _ in range(r.randint(10, 40)))
    for k, ms in enumerate(machines):
        name = "m%d" % k
        spec["tx"][name] = [r.getrandbits(32) for _ in range(r.randint(0, 4))]
        feed = sorted((r.randint(1, 100), r.getrandbits(32)) for _ in range(r.randint(0, 3)))
        spec["tx_feed"][name] = [e for j, e in enumerate(feed) if j == 0 or e[0] != feed[j - 1][0]]
        spec["drain"][name] = r.random() < .7
    spec["irq_set"] = sorted((r.randint(0, 100), r.randint(0, 3)) for _ in range(r.randint(0, 4)))
    spec["irq_clear"] = sorted((r.randint(0, 100), r.randint(0, 3)) for _ in range(r.randint(0, 4)))
    return spec


def random_block(spec):
    """``(Block, BlockSim)`` for a spec."""
    machines = [Machine("m%d" % k, lambda ms=ms: RandomMachine(ms)) for k, ms in enumerate(spec["machines"])]
    block = Block(machines, PLATFORMS[spec["chip"]], name="rnd")
    sim = BlockSim(cycles=spec["cycles"], stim=spec["stim"], tx=spec["tx"], tx_feed=spec["tx_feed"], drain=spec["drain"], irq_set=tuple(spec["irq_set"]), irq_clear=tuple(spec["irq_clear"]))
    return block, sim
