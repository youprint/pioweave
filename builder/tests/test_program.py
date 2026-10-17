# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Tests of program.py and examples.py.

The main check: the DSL encodes every instruction form itself, from the datasheet tables, and the real pioasm assembles the text the DSL
prints; the two must give identical words. The tests that need pioasm are skipped when it is not found (set PIOASM, or put it on the PATH,
or build it into ../bitweave/.tools/bin as described in bitweave's `guide pio`). Nothing here runs on a chip.
"""
import itertools
import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
from pioweave_builder import examples as pio_examples
from pioweave_builder.program import (ALWAYS, CLEAR, EXEC, INVERT, ISR, NOT_OSRE, NOT_X, NOT_Y, NULL, OSR, PC, PIN, PINDIRS, PINS, REVERSE, SET, STATUS, WAIT, X, X_DEC,  # noqa: E402
                     X_NE_Y, Y, Y_DEC, NONE, PioError, Program, write_pio)


def find_pioasm():
    for c in (os.environ.get("PIOASM"), shutil.which("pioasm"), os.path.join(HERE, "..", "..", "..", ".tools", "bin", "pioasm")):
        if c and os.path.exists(c):
            return c
    return None


PIOASM = find_pioasm()
needs_pioasm = pytest.mark.skipif(PIOASM is None, reason="pioasm not found (set PIOASM)")


def assemble(text, tmp_path, version=1, name="p.pio"):
    """pioasm JSON output of a source text; returns (returncode, stderr, programs)."""
    src = tmp_path / name
    src.write_text(text)
    out = tmp_path / (name + ".json")
    r = subprocess.run([PIOASM, "-v", str(version), "-o", "json", str(src), str(out)], capture_output=True, text=True)
    progs = json.loads(out.read_text())["programs"] if r.returncode == 0 and out.exists() else []
    return r.returncode, r.stderr, progs


def words_of(prog_json):
    return [int(i["hex"], 16) for i in prog_json["instructions"]]


# ----------------------------------------------------------------------------- every instruction form against pioasm
def L(method, *args, **kwargs):
    """A builder that calls `Program.method(*args, **kwargs, side=..., delay=...)`."""
    return lambda p, **k: getattr(p, method)(*args, **kwargs, **k)


def forms(version):
    """(label, builder(p, side=, delay=)) for every operand combination of every instruction; version 1 adds the RP2350 forms."""
    out = []
    for c in (ALWAYS, NOT_X, X_DEC, NOT_Y, Y_DEC, X_NE_Y, PIN, NOT_OSRE):
        for t in ("start", 0, 2):
            out.append(("jmp %r %r" % (c, t), L("jmp", t, when=c)))
    for pol in (0, 1):
        for n in (0, 5, 31):
            out.append(("wait_gpio", L("wait_gpio", pol, n)))
            out.append(("wait_pin", L("wait_pin", pol, n)))
        for f, rel in itertools.product((0, 3, 7), (False, True)):
            out.append(("wait_irq", L("wait_irq", pol, f, rel=rel)))
    for src, bits in itertools.product((PINS, X, Y, NULL, ISR, OSR), (1, 2, 31, 32)):
        out.append(("in %r %d" % (src, bits), L("in_", src, bits)))
    for dst, bits in itertools.product((PINS, X, Y, NULL, PINDIRS, PC, ISR, EXEC), (1, 2, 31, 32)):
        out.append(("out %r %d" % (dst, bits), L("out", dst, bits)))
    for a, b in itertools.product((False, True), repeat=2):
        out.append(("push", L("push", iffull=a, block=b)))
        out.append(("pull", L("pull", ifempty=a, block=b)))
    dsts = [PINS, X, Y, EXEC, PC, ISR, OSR] + ([PINDIRS] if version else [])
    for dst, src, op in itertools.product(dsts, (PINS, X, Y, NULL, STATUS, ISR, OSR), (NONE, INVERT, REVERSE)):
        out.append(("mov %r %r %r" % (dst, src, op), L("mov", dst, src, op=op)))
    for f, mode, rel in itertools.product((0, 3, 7), (SET, WAIT, CLEAR), (False, True)):
        out.append(("irq", L("irq", f, mode, rel=rel)))
    for dst, v in itertools.product((PINS, X, Y, PINDIRS), (0, 1, 17, 31)):
        out.append(("set %r %d" % (dst, v), L("set", dst, v)))
    out.append(("nop", L("nop")))
    out.append(("word", lambda p, **k: p.word(0xA5A5)))   # skipped for a mandatory side-set, see build_batches
    if version:
        for pol in (0, 1):
            for off in range(4):
                out.append(("wait_jmppin", L("wait_jmppin", pol, off)))
            for f, other in itertools.product((0, 7), ("prev", "next")):
                out.append(("wait_irq other", L("wait_irq", pol, f, other=other)))
        for f, mode, other in itertools.product((0, 5), (SET, WAIT, CLEAR), ("prev", "next")):
            out.append(("irq other", L("irq", f, mode, other=other)))
    return out


def rx_forms():
    """MOV to / from the RX FIFO registers; pioasm wants `.fifo putget` for them."""
    out = []
    for idx in (0, 1, 3, Y):
        out.append(("mov_put", L("mov_put", idx)))
        out.append(("mov_get", L("mov_get", idx)))
    return out


# (constructor kwargs, [(side, delay) variants])
CONFIGS = [
    ({}, [(None, 0), (None, 1), (None, 31)]),
    ({"side_set": 1}, [(0, 0), (1, 0), (1, 15), (0, 7)]),
    ({"side_set": 2, "side_opt": True}, [(None, 0), (3, 0), (2, 3), (None, 3), (0, 1)]),   # 2 delay bits left
    ({"side_set": 3, "side_pindirs": True}, [(5, 0), (7, 3)]),
    ({"side_set": 5}, [(31, 0), (0, 0)]),
]


def build_batches(version, chip):
    """Programs of at most 32 instructions holding every form under every side-set configuration."""
    progs = []
    for ci, (kw, variants) in enumerate(CONFIGS):
        mandatory = kw.get("side_set") and not kw.get("side_opt")
        items = [(fn, side, delay) for lbl, fn in forms(version) for side, delay in variants if not (mandatory and lbl == "word")]
        for bi in range(0, len(items), 30):
            p = Program("c%d_b%d_v%d" % (ci, bi // 30, version), chip=chip, **kw)
            p.label("start")
            p.nop(side=0 if kw.get("side_set") and not kw.get("side_opt") else None)
            for fn, side, delay in items[bi:bi + 30]:
                fn(p, side=side, delay=delay)
            progs.append(p)
        if version:
            rx_items = [(fn, side, delay) for _, fn in rx_forms() for side, delay in variants]
            for ri in range(0, len(rx_items), 32):
                q = Program("c%d_rx%d_v%d" % (ci, ri // 32, version), chip=chip, **kw)
                q.fifo("putget")
                for fn, side, delay in rx_items[ri:ri + 32]:
                    fn(q, side=side, delay=delay)
                progs.append(q)
    return progs


@needs_pioasm
@pytest.mark.parametrize("version,chip", [(0, "rp2040"), (1, "rp2350")])
def test_every_form_encodes_like_pioasm(tmp_path, version, chip):
    progs = build_batches(version, chip)
    for p in progs:
        assert len(p._instrs) <= 32
    text = "\n".join(p.pio_text() for p in progs)
    rc, err, got = assemble(text, tmp_path, version=version)
    assert rc == 0, err[:2000]
    assert [g["name"] for g in got] == [p.name for p in progs]
    total = 0
    for p, g in zip(progs, got):
        mine = p.words()
        theirs = words_of(g)
        assert mine == theirs, "%s: %s" % (p.name, [(i, hex(a), hex(b)) for i, (a, b) in enumerate(zip(mine, theirs)) if a != b][:5])
        total += len(mine)
    assert total > 1000 if version else total > 800


# ----------------------------------------------------------------------------- the version gate agrees with pioasm
V1_ONLY = [
    ("wait_jmppin", lambda p: p.wait_jmppin(1, 0)),
    ("mov pindirs", lambda p: p.mov(PINDIRS, X)),
    ("mov_put", lambda p: (p.fifo("putget"), p.mov_put(0))),
    ("mov_get", lambda p: (p.fifo("putget"), p.mov_get(Y))),
    ("irq prev", lambda p: p.irq(1, SET, other="prev")),
    ("wait irq next", lambda p: p.wait_irq(1, 1, other="next")),
]


@pytest.mark.parametrize("label,fn", V1_ONLY, ids=[x[0] for x in V1_ONLY])
def test_version_one_forms_are_refused_on_the_rp2040(label, fn):
    with pytest.raises(PioError, match="PIO version 1|RP2350"):
        fn(Program("v", chip="rp2040"))
    fn(Program("v", chip="rp2350"))
    fn(Program("v", chip="rp2354"))


@needs_pioasm
@pytest.mark.parametrize("label,fn", V1_ONLY, ids=[x[0] for x in V1_ONLY])
def test_pioasm_refuses_the_same_forms_for_version_zero(tmp_path, label, fn):
    p = Program("v", chip="rp2350")
    fn(p)
    rc, err, _ = assemble(p.pio_text().replace(".pio_version 1", ".pio_version 0"), tmp_path, version=0)
    assert rc != 0 and "version" in err.lower(), err


# ----------------------------------------------------------------------------- the example programs equal the hand-written .pio files
@needs_pioasm
@pytest.mark.parametrize("name", sorted(pio_examples.PROGRAMS))
def test_examples_equal_the_hand_written_programs(tmp_path, name):
    prog = pio_examples.PROGRAMS[name]()
    with open(os.path.join(HERE, "pio_ref", name + ".pio")) as fh:
        ref = fh.read()
    rc, err, ref_prog = assemble(ref, tmp_path, version=prog.version, name="ref.pio")
    assert rc == 0, err
    rc, err, dsl_prog = assemble(prog.pio_text(), tmp_path, version=prog.version, name="dsl.pio")
    assert rc == 0, err
    assert words_of(dsl_prog[0]) == words_of(ref_prog[0]) == prog.words()
    for key in ("wrapTarget", "wrap", "sideset", "origin", "pioVersion"):
        assert dsl_prog[0][key] == ref_prog[0][key], key
    assert prog.wrap_range == (dsl_prog[0]["wrapTarget"], dsl_prog[0]["wrap"])


@needs_pioasm
def test_wait_gpio_numbers_up_to_47_like_pioasm(tmp_path):
    p = Program("hi", chip="rp2350b")
    for g in (16, 20, 31, 32, 35, 47):
        p.wait_gpio(1, g)
        p.wait_gpio(0, g, delay=1)
    rc, err_, got = assemble(p.pio_text(), tmp_path)
    assert rc == 0, err_
    assert p.words() == words_of(got[0])
    assert max(p.words()) > 0xFFFF and got[0]["usedGPIORanges"] == 6
    # below 16 together with above 31 does not fit one window; the rule is pioasm's and the DSL's
    q = Program("mix", chip="rp2350b")
    q.wait_gpio(1, 3)
    q.wait_gpio(1, 40)
    rc, err_, _ = assemble(".program mix\n.pio_version 1\n    wait 1 gpio 3\n    wait 1 gpio 40\n", tmp_path, name="q.pio")
    assert rc != 0 and "already been used" in err_
    with pytest.raises(PioError, match="below 16 and above 31"):
        q.words()
    # GPIO above 31 needs version 1
    with pytest.raises(PioError, match="GPIO number"):
        Program("v0", chip="rp2040").wait_gpio(1, 32)
    for chip in ("rp2350a", "rp2350b", "rp2354a", "rp2354b"):
        assert Program("c", chip=chip).version == 1


def test_example_words_from_hand_derivation():
    # square: set pins,1 [1] = 0xE101 ; set pins,0 [1] = 0xE100
    assert pio_examples.square().words() == [0xE101, 0xE100]
    # uart_tx: pull block side 1 [7] ; set x,7 side 0 [7] ; out pins,1 ; jmp x-- 2 [6]  (side-set 1 bit + enable bit, 3 delay bits)
    assert pio_examples.uart_tx().words() == [0x9FA0, 0xF727, 0x6001, 0x0642]
    assert pio_examples.passthru().words() == [0x6020, 0x4020]


def test_examples_run_on_the_rp2350_too():
    for f in pio_examples.PROGRAMS.values():
        assert f(chip="rp2350").words() == f(chip="rp2040").words()


# ----------------------------------------------------------------------------- structure, directives, determinism
def test_text_is_deterministic_and_has_the_structure():
    def build():
        p = Program("demo", chip="rp2350", side_set=1, side_opt=True, origin=0)
        p.define("N", 5, public=True)
        p.clock_divider(2.5)
        p.fifo("putget")
        p.mov_status("rxfifo", 2)
        p.config_in(8, "left")
        p.config_out(8, "right", auto=True)
        p.config_set(3)
        p.label("top")
        p.wrap_target()
        p.set(X, 4, side=1)
        p.jmp("top", when=X_DEC, delay=2)
        p.wrap()
        p.nop()
        return p
    a, b = build(), build()
    assert a.pio_text() == b.pio_text()
    t = a.pio_text()
    for want in (".program demo", ".pio_version 1", ".define PUBLIC N 5", ".origin 0", ".clock_div 2.5", ".fifo putget", ".mov_status rxfifo < 2", ".in 8 left\n",
                 ".out 8 right auto", ".set 3", ".side_set 1 opt", "top:", ".wrap_target", ".wrap\n", "jmp x-- top [2]", "set x, 4 side 1"):
        assert want in t, want
    assert t.index(".wrap_target") < t.index("top:") < t.index("set x")
    assert a.wrap_range == (0, 1)


@needs_pioasm
def test_directives_are_accepted_by_pioasm(tmp_path):
    p = Program("demo", chip="rp2350", side_set=1, side_opt=True, origin=0)
    p.define("N", 5, public=True)
    p.clock_divider(2.5)
    p.fifo("putget")
    p.mov_status("irq", 2)
    p.config_in(8, "left")
    p.config_out(8, "right", auto=True)
    p.config_set(3)
    p.label("top")
    p.wrap_target()
    p.set(X, 4, side=1)
    p.jmp("top", when=X_DEC, delay=2)
    p.wrap()
    p.nop()
    rc, err, got = assemble(p.pio_text(), tmp_path)
    assert rc == 0, err
    assert words_of(got[0]) == p.words()
    assert (got[0]["wrapTarget"], got[0]["wrap"]) == p.wrap_range


@needs_pioasm
def test_origin_makes_jump_targets_absolute(tmp_path):
    p = Program("org", origin=4)
    p.label("a")
    p.set(X, 1)
    p.jmp("a")
    rc, err, got = assemble(p.pio_text(), tmp_path, version=0)
    assert rc == 0, err
    assert p.words() == words_of(got[0])
    assert p.words()[1] & 31 == 0     # pioasm writes program-relative targets; the SDK adds the load offset (4)


@needs_pioasm
def test_write_pio_holds_several_programs(tmp_path):
    path = tmp_path / "two.pio"
    write_pio([pio_examples.square(), pio_examples.passthru()], str(path))
    rc, err, got = assemble(path.read_text(), tmp_path, version=0, name="two2.pio")
    assert rc == 0, err
    assert [g["name"] for g in got] == ["square", "passthru"]


def test_emit_script_prints_the_generator_line(tmp_path):
    r = subprocess.run([sys.executable, "-I", "-m", "pioweave_builder", "emit", "square", str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    line = [l for l in r.stdout.splitlines() if l.startswith("EMIT ")][-1]
    path = json.loads(line[5:])["path"]
    with open(path) as fh:
        assert fh.read() == pio_examples.square().pio_text()
    for bad in (["nope", str(tmp_path)], []):
        r = subprocess.run([sys.executable, "-I", "-m", "pioweave_builder", "emit"] + bad, capture_output=True, text=True)
        assert r.returncode != 0
    r = subprocess.run([sys.executable, "-I", "-m", "pioweave_builder", "emit", "uart_tx", str(tmp_path), "rp2040"], capture_output=True, text=True)
    assert r.returncode == 0 and "uart_tx.pio" in r.stdout


# ----------------------------------------------------------------------------- errors are raised when the program is built
def err(fn, match):
    with pytest.raises(PioError, match=match):
        fn()


def test_program_level_errors():
    err(lambda: Program("1bad"), "program name")
    err(lambda: Program(""), "program name")
    err(lambda: Program("x", chip="avr"), "chip")
    err(lambda: Program("x", side_set=6), "side_set")
    err(lambda: Program("x", side_set=5, side_opt=True), "enable bit")
    err(lambda: Program("x", side_opt=True), "side_set >= 1")
    err(lambda: Program("x", origin=32), "origin")
    p = Program("x")
    err(p._resolve, "no instructions")


def test_instruction_argument_errors():
    p = Program("x")
    err(lambda: p.set(X, 32), "set value")
    err(lambda: p.set(NULL, 1), "set destination")
    err(lambda: p.in_(PINDIRS, 1), "in source")
    err(lambda: p.in_(X, 0), "in bit count")
    err(lambda: p.in_(X, 33), "in bit count")
    err(lambda: p.out(STATUS, 1), "out destination")
    err(lambda: p.mov(NULL, X), "mov destination")
    err(lambda: p.mov(X, EXEC), "mov source")
    err(lambda: p.mov(X, Y, op="x"), "mov operation")
    err(lambda: p.jmp("a", when="x"), "jmp condition")
    err(lambda: p.jmp(3.5), "jmp target")
    err(lambda: p.jmp(32), "jmp address")
    err(lambda: p.wait_gpio(2, 1), "polarity")
    err(lambda: p.wait_gpio(1, 32), "GPIO")
    err(lambda: p.wait_irq(1, 8), "IRQ flag")
    err(lambda: p.irq(1, "set"), "irq mode")
    err(lambda: p.irq(9), "IRQ flag")
    err(lambda: p.nop(delay=32), "delay")
    err(lambda: p.nop(side=1), "side_set=0")
    err(lambda: p.nop(delay=True), "delay")
    err(lambda: p.word(0x10000), "word")
    q = Program("q", chip="rp2350")
    err(lambda: q.wait_jmppin(1, 4), "jmppin offset")
    err(lambda: q.mov_put(0), "needs fifo")
    q.fifo("putget")
    err(lambda: q.mov_put(4), "rxfifo index")
    err(lambda: q.push(), "push needs fifo")
    q.nop()
    err(lambda: q.fifo("tx"), "before the first instruction")
    err(lambda: q.irq(1, SET, rel=True, other="next"), "rel is not supported")
    err(lambda: q.irq(1, SET, other="left"), "prev")
    err(lambda: q.wait_irq(1, 1, rel=True, other="prev"), "rel is not supported")


def test_side_set_rules():
    m = Program("m", side_set=2)
    err(lambda: m.nop(), "mandatory side-set")
    err(lambda: m.nop(side=4), "side")
    err(lambda: m.nop(side=1, delay=8), "delay")     # 5 - 2 = 3 delay bits
    m.nop(side=3, delay=7)
    o = Program("o", side_set=2, side_opt=True)
    o.nop()
    err(lambda: o.nop(delay=4), "delay")             # 5 - 2 - 1 = 2 delay bits
    o.nop(side=3, delay=3)
    full = Program("f", side_set=5)
    err(lambda: full.nop(side=0, delay=1), "delay")


def test_label_wrap_and_size_errors():
    p = Program("x")
    err(lambda: p.label("1a"), "label")
    p.label("a")
    err(lambda: p.label("a"), "twice")
    err(p._resolve, "past the last instruction")
    p.nop()
    p.jmp("missing")
    err(p._resolve, "undefined label")
    q = Program("q")
    err(q.wrap, "needs an instruction")
    q.wrap_target()
    err(q.wrap_target, "only once")
    err(q._resolve, "past the last instruction")
    q.nop()
    q.wrap()
    err(q.wrap, "only once")
    r = Program("r")
    for _ in range(32):
        r.nop()
    err(r.nop, "32 words")
    o = Program("o", origin=30)
    for _ in range(3):
        o.nop()
    err(o._resolve, "does not fit")
    far = Program("far", origin=31)
    far.nop()
    far.label("end")
    far.nop()
    far.jmp("end")
    err(far._resolve, "does not fit|beyond")


def test_directive_errors():
    p = Program("x", chip="rp2040")
    err(lambda: p.fifo("putget"), "fifo")
    err(lambda: p.fifo("bogus"), "fifo")
    p.fifo("tx")
    err(lambda: p.mov_status("irq", 1), "version 1")
    err(lambda: p.mov_status("bogus", 1), "kind")
    err(lambda: p.mov_status("rxfifo", 16), "level")
    err(lambda: p.config_in(8), "must be 32")
    p.config_in(32, "left", auto=True, threshold=16)
    err(lambda: p.config_out(32, threshold=4), "auto = True")
    err(lambda: p.config_out(32, direction="up"), "direction")
    err(lambda: p.config_set(6), ".set count")
    err(lambda: p.clock_divider(0), "clock divider")
    err(lambda: p.define("1x", 1), "symbol")
    err(lambda: p.define("x", 1.5), "define value")
    q = Program("q", chip="rp2350")
    q.mov_status("irq", 1, which="next")
    err(lambda: q.mov_status("irq", 1, which="far"), "which")
    err(lambda: q.mov_status("irq", 9), "irq flag")
    q.config_in(8)
    q.fifo("txget")
    q.nop()
    assert ".mov_status irq next set 1" in q.pio_text()
    for late in (lambda: q.fifo("tx"), lambda: q.config_set(1), lambda: q.config_out(8), lambda: q.clock_divider(2), lambda: q.mov_status("rxfifo", 1)):
        err(late, "before the first instruction")
    a = Program("a", chip="rp2350")
    a.fifo("putget")
    a.config_in(8, auto=True)
    a.nop()
    err(a.pio_text, "incompatible")
    err(lambda: Program("w", side_set=1).word(1), "mandatory side-set")


def test_jump_targets_are_program_relative():
    p = Program("rel", origin=4)
    p.label("a")
    p.set(X, 1)
    p.jmp("a")
    assert p.words()[1] & 31 == 0
    assert p.wrap_range == (0, 1)
