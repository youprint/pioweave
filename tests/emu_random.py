"""Random PIO programs and state machine configurations for the differential test of the emulator against bitweave's PIO simulator (tests/test_emulator_differential.py)."""
import random

from pioweave.builder import (ALWAYS, CLEAR, EXEC, INVERT, ISR, NONE, NOT_OSRE, NOT_X, NOT_Y, NULL, OSR, PC, PIN, PINDIRS, PINS, REVERSE, SET, STATUS, WAIT, X, X_DEC,
                              X_NE_Y, Y, Y_DEC, PioError, Program)

CONDS = [ALWAYS, NOT_X, X_DEC, NOT_Y, Y_DEC, X_NE_Y, PIN, NOT_OSRE]
IN_SRC = [PINS, X, Y, NULL, ISR, OSR]
OUT_DST = [PINS, X, Y, NULL, PINDIRS, PC, ISR, EXEC]
MOV_DST = [PINS, X, Y, PINDIRS, EXEC, PC, ISR, OSR]
MOV_SRC = [PINS, X, Y, NULL, STATUS, ISR, OSR]
KINDS = ["nop", "jmp", "jmp", "wait_gpio", "wait_pin", "wait_irq", "wait_jmppin", "in", "in", "out", "out", "push", "pull", "mov", "mov", "irq", "set", "set", "set"]


def _attempt(r, chip):
    n = r.randint(2, 12)
    side_count = r.choice([0, 0, 1, 2, 3])
    opt = side_count > 0 and r.random() < .5
    pindirs = side_count > 0 and r.random() < .2
    p = Program("rnd", chip=chip, side_set=side_count, side_opt=opt, side_pindirs=pindirs)
    v1 = chip != "rp2040"
    side_bits = side_count + (1 if opt else 0)
    delay_max = (1 << (5 - side_bits)) - 1
    labels = ["l%d" % i for i in range(n)]
    for i in range(n):
        p.label(labels[i])
        k = r.choice(KINDS)
        kw = {}
        if side_count and (not opt or r.random() < .6):
            kw["side"] = r.randrange(1 << side_count)
        if delay_max and r.random() < .4:
            kw["delay"] = r.randint(0, delay_max)
        if k == "nop":
            p.nop(**kw)
        elif k == "jmp":
            p.jmp(r.choice(labels), when=r.choice(CONDS), **kw)
        elif k == "wait_gpio":
            p.wait_gpio(r.randint(0, 1), r.randint(0, 31), **kw)
        elif k == "wait_pin":
            p.wait_pin(r.randint(0, 1), r.randint(0, 31), **kw)
        elif k == "wait_irq":
            p.wait_irq(r.randint(0, 1), r.randint(0, 7), rel=r.random() < .3, **kw)
        elif k == "wait_jmppin":
            p.wait_jmppin(r.randint(0, 1), r.randint(0, 3), **kw) if v1 else p.nop(**kw)
        elif k == "in":
            p.in_(r.choice(IN_SRC), r.randint(1, 32), **kw)
        elif k == "out":
            p.out(r.choice(OUT_DST), r.randint(1, 32), **kw)
        elif k == "push":
            p.push(iffull=r.random() < .4, block=r.random() < .6, **kw)
        elif k == "pull":
            p.pull(ifempty=r.random() < .4, block=r.random() < .6, **kw)
        elif k == "mov":
            p.mov(r.choice(MOV_DST), r.choice(MOV_SRC), op=r.choice([NONE, NONE, INVERT, REVERSE]), **kw)
        elif k == "irq":
            p.irq(r.randint(0, 7), mode=r.choice([SET, SET, WAIT, CLEAR]), rel=r.random() < .3, **kw)
        else:
            p.set(r.choice([PINS, X, Y, PINDIRS]), r.randint(0, 31), **kw)
    p.words()
    return p


def random_program(r, chip):
    for _ in range(200):
        try:
            return _attempt(r, chip)
        except PioError:
            continue
    raise RuntimeError("no valid random program")


def random_case(seed):
    """(program, cycles, config, stim, tx, tx_feed, drain); config keys are the ones of pioweave.emulator.run_case and of a bitweave `[[pio.NAME.sim]]` case."""
    r = random.Random(50_000 + seed)
    chip = r.choice(["rp2040", "rp2350"])
    p = random_program(r, chip)
    cfg = {"set_base": r.randint(0, 31), "set_count": r.randint(0, 5), "out_base": r.randint(0, 31), "out_count": r.randint(0, 32), "sideset_base": r.randint(0, 31),
           "in_base": r.randint(0, 31), "jmp_pin": r.randint(0, 31), "in_shift": r.choice(["left", "right"]), "out_shift": r.choice(["left", "right"]),
           "autopush": r.random() < .25, "autopull": r.random() < .25, "push_thresh": r.choice([32, 32, r.randint(1, 32)]), "pull_thresh": r.choice([32, 32, r.randint(1, 32)]),
           "x": r.getrandbits(32) if r.random() < .3 else r.randint(0, 6), "y": r.getrandbits(32) if r.random() < .3 else r.randint(0, 6), "dirs": r.getrandbits(32) if r.random() < .5 else 0}
    cfg["out_count"] = r.randint(0, 32) if r.random() < .8 else 32
    if chip != "rp2040":
        if r.random() < .4:
            cfg["in_count"] = r.randint(1, 32)
        join = r.choice(["none", "none", "none", "tx", "rx", "rx_put", "rx_get", "rx_put_get"])
        cfg["fifo_join"] = join
        if join.startswith("rx_") and cfg["autopush"]:
            cfg["autopush"] = False
    else:
        cfg["fifo_join"] = r.choice(["none", "none", "tx", "rx"])
    cfg["status_sel"] = r.choice(["tx", "rx"] + (["irq"] if chip != "rp2040" else []))
    cfg["status_n"] = r.randint(0, 7)
    cycles = r.randint(30, 120)
    stim = {pin: "".join(r.choice("01") for _ in range(r.randint(5, 30))) for pin in r.sample(range(32), r.randint(0, 6))}
    tx = [r.getrandbits(32) for _ in range(r.randint(0, 5))]
    tx_feed = sorted((r.randint(1, cycles - 1), r.getrandbits(32)) for _ in range(r.randint(0, 3)))
    return p, cycles, cfg, stim, tx, tx_feed, r.random() < .7
