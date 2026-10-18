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
# the families of the extra generators (see random_family_case): which instruction forms they draw from
FAMILIES = {
    "exec": ["pull", "pull", "out_exec", "out_exec", "mov_exec", "in", "out", "set", "jmp", "nop", "push"],
    "irq": ["irq", "irq", "irq", "wait_irq", "wait_irq", "mov_status", "mov_status", "jmp", "set", "nop", "in", "push"],
    "fifo": ["mov_put", "mov_get", "push", "pull", "in", "out", "set", "mov", "jmp", "nop"],
    "thresholds": ["in", "in", "out", "out", "push", "pull", "jmp_osre", "set", "mov", "nop"],
    "pindirs": ["set_pindirs", "out_pindirs", "mov_pindirs", "set", "out", "mov", "jmp", "nop", "in"],
}


def _attempt(r, chip, family=None, fifo=None):
    n = r.randint(2, 12)
    kinds = FAMILIES[family] if family else KINDS
    side_count = r.choice([0, 0, 1, 2, 3])
    opt = side_count > 0 and r.random() < .5
    pindirs = side_count > 0 and (r.random() < (.6 if family == "pindirs" else .2))
    p = Program("rnd", chip=chip, side_set=side_count, side_opt=opt, side_pindirs=pindirs)
    v1 = chip != "rp2040"
    if fifo and v1:
        p.fifo(fifo)
    side_bits = side_count + (1 if opt else 0)
    delay_max = (1 << (5 - side_bits)) - 1
    labels = ["l%d" % i for i in range(n)]
    for i in range(n):
        p.label(labels[i])
        k = r.choice(kinds)
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
        elif k == "out_exec":
            p.out(EXEC, r.choice([16, 16, 32, r.randint(1, 32)]), **kw)
        elif k == "mov_exec":
            p.mov(EXEC, r.choice([X, Y, OSR, ISR]), **kw)
        elif k == "mov_status":
            p.mov(r.choice([X, Y]), STATUS, **kw)
        elif k == "mov_put":
            p.mov_put(r.choice([0, 1, 2, 3, Y]), **kw) if v1 else p.nop(**kw)
        elif k == "mov_get":
            p.mov_get(r.choice([0, 1, 2, 3, Y]), **kw) if v1 else p.nop(**kw)
        elif k == "jmp_osre":
            p.jmp(r.choice(labels), when=NOT_OSRE, **kw)
        elif k == "set_pindirs":
            p.set(PINDIRS, r.randint(0, 31), **kw)
        elif k == "out_pindirs":
            p.out(PINDIRS, r.randint(1, 32), **kw)
        elif k == "mov_pindirs":
            p.mov(PINDIRS, r.choice([X, Y, NULL, OSR, ISR]), op=r.choice([NONE, INVERT, REVERSE]), **kw) if v1 else p.mov(PINS, r.choice([X, Y]), **kw)
        else:
            p.set(r.choice([PINS, X, Y, PINDIRS]), r.randint(0, 31), **kw)
    p.words()
    return p


def random_program(r, chip, family=None, fifo=None):
    for _ in range(200):
        try:
            return _attempt(r, chip, family, fifo)
        except PioError:
            continue
    raise RuntimeError("no valid random program")


def _instruction_words(r, chip, count):
    """Valid instruction words (as OUT EXEC and MOV EXEC data): the words of small random programs, with the upper half random."""
    out = []
    while len(out) < count:
        words = random_program(r, chip, r.choice(["pindirs", "thresholds", "irq"])).words()
        out.append((r.getrandbits(16) << 16 if r.random() < .5 else 0) | (r.choice(words) & 0xFFFF))
    return out


def random_case(seed, family=None):
    """(program, cycles, config, stim, tx, tx_feed, drain); config keys are the ones of pioweave.emulator.run_case and of a bitweave `[[pio.NAME.sim]]` case.

    `family` None draws from every instruction form; the names in FAMILIES give the extra generators: ``exec`` (OUT / MOV EXEC with valid instruction words in the FIFO), ``irq``
    (IRQ set, wait, clear, WAIT IRQ, MOV STATUS from the flags), ``fifo`` (the joins of both PIO versions, MOV to and from the RX FIFO registers), ``thresholds`` (autopush and
    autopull with every threshold) and ``pindirs`` (SET, OUT, MOV and side-set on the pin directions)."""
    r = random.Random(50_000 + seed + (0 if family is None else 1_000_000 * (1 + list(FAMILIES).index(family))))
    chip = r.choice(["rp2040", "rp2350"])
    if family == "fifo":
        chip = "rp2350" if r.random() < .85 else "rp2040"
    join = None
    if family == "fifo":
        join = r.choice(["rx_put", "rx_get", "rx_put_get", "rx_put_get", "tx", "rx", "none"]) if chip != "rp2040" else r.choice(["none", "tx", "rx"])
    p = random_program(r, chip, family, {"rx_put": "txput", "rx_get": "txget", "rx_put_get": "putget", "tx": "tx", "rx": "rx", "none": "txrx"}.get(join))
    cfg = {"set_base": r.randint(0, 31), "set_count": r.randint(0, 5), "out_base": r.randint(0, 31), "out_count": r.randint(0, 32), "sideset_base": r.randint(0, 31),
           "in_base": r.randint(0, 31), "jmp_pin": r.randint(0, 31), "in_shift": r.choice(["left", "right"]), "out_shift": r.choice(["left", "right"]),
           "autopush": r.random() < .25, "autopull": r.random() < .25, "push_thresh": r.choice([32, 32, r.randint(1, 32)]), "pull_thresh": r.choice([32, 32, r.randint(1, 32)]),
           "x": r.getrandbits(32) if r.random() < .3 else r.randint(0, 6), "y": r.getrandbits(32) if r.random() < .3 else r.randint(0, 6), "dirs": r.getrandbits(32) if r.random() < .5 else 0}
    cfg["out_count"] = r.randint(0, 32) if r.random() < .8 else 32
    if family == "thresholds":
        cfg.update(autopush=r.random() < .8, autopull=r.random() < .8, push_thresh=r.randint(1, 32), pull_thresh=r.randint(1, 32))
    if family == "exec":
        cfg["out_shift"] = "right" if r.random() < .8 else "left"
    if chip != "rp2040":
        if r.random() < .4:
            cfg["in_count"] = r.randint(1, 32)
        join_any = r.choice(["none", "none", "none", "tx", "rx", "rx_put", "rx_get", "rx_put_get"])
        join = join_any if family != "fifo" else join
        cfg["fifo_join"] = join if family == "fifo" else join_any

        if cfg["fifo_join"].startswith("rx_") and cfg["autopush"]:
            cfg["autopush"] = False
    else:
        cfg["fifo_join"] = join if family == "fifo" else r.choice(["none", "none", "tx", "rx"])
    cfg["status_sel"] = r.choice(["tx", "rx"] + (["irq"] if chip != "rp2040" else []))
    cfg["status_n"] = r.randint(0, 7)
    if chip != "rp2040" and cfg["status_sel"] != "irq":
        # RP2350: a level above the FIFO depth is reserved (bitweave refuses it); the depth is 4, 8 when the other FIFO is joined to it, 0 when it is joined away
        join = cfg["fifo_join"]
        depth = {"tx": (8, 0), "rx": (0, 8)}.get(join, (4, 0) if join.startswith("rx_") else (4, 4))[0 if cfg["status_sel"] == "tx" else 1]
        cfg["status_n"] = min(cfg["status_n"], depth)
    cycles = r.randint(30, 120)
    stim = {pin: "".join(r.choice("01") for _ in range(r.randint(5, 30))) for pin in r.sample(range(32), r.randint(0, 6))}
    count = r.randint(4, 8) if family in ("thresholds", "fifo", "exec") else r.randint(0, 5)
    tx = _instruction_words(r, chip, count) if family == "exec" else [r.getrandbits(32) for _ in range(count)]
    feeds = r.randint(0, 3)
    tx_feed = sorted((r.randint(1, cycles - 1), _instruction_words(r, chip, 1)[0] if family == "exec" else r.getrandbits(32)) for _ in range(feeds))
    return p, cycles, cfg, stim, tx, tx_feed, r.random() < .7
