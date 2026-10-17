# Study: how much of Amaranth's internals amaranth-rp depends on (2026-10-17)

Question (owner): what has to change so amaranth-rp follows Amaranth's public interfaces. This note records what was checked, not a change.

## What the compiler uses

`_compat.py` is the one place that touches Amaranth internals: `Fragment` and `amaranth.hdl._ast` (`Assign`, `Switch`, `Operator`, `Const`, `Concat`), plus `Signal.decoder` for the FSM state names.

## Facts checked on Amaranth 0.5.10 (installed) and 0.6.0.dev140 (GitHub main, scratch environment)

| Fact | Evidence |
|---|---|
| `Fragment` is exported by `amaranth.hdl` (in `__all__`), and `Fragment.get(obj, platform)` is how elaboration is done. Its attributes `statements`, `subfragments`, `domains` are not documented. | `amaranth/hdl/__init__.py`, `_ir.py` (0.5.10) |
| `Statement`, `Assign`, `Switch`, `Operator`, `Concat` are not in `amaranth.hdl.__all__`; they live in the private `_ast` and in the deprecated shim `amaranth.hdl.ast` ("TODO(amaranth-0.6): remove module"). | `hdl/ast.py`, `hdl/_ast.py` |
| The 0.6.0 changelog (unreleased) says: "Removed: (deprecated in 0.5.0) public submodules of amaranth.hdl". It does not mention `Fragment`, the IR or a way to traverse a design. | amaranth-lang.org/docs/amaranth/latest/changes.html, read 2026-10-17 |
| The statements of `m.FSM()` have the same shape in 0.5.10 and 0.6.0.dev140 (a `Switch` per domain on the state signal, the `ongoing` flags as comb assignments). | IR dumps of one FSM in both versions |
| One thing changed: in 0.6.0.dev140 `Signal.decoder` is `None` for the FSM state; the state names (`NAME/n`) are in the signal's private format object (`_format._variants`, a dict number -> name). | `hdl/_dsl.py` (FSM section), `hdl/_ast.py` |
| `m.FSM()` yields an object with `.encoding` and `.decoding` (state name <-> number) in both versions, and a documented `.ongoing()`. | `hdl/_dsl.py` |

## Experiment

In a scratch virtual environment with Amaranth main (0.6.0.dev140) and `AMARANTH_RP_ALLOW_UNTESTED=1`:

- unchanged amaranth-rp fails on every design at its first check (the state-name lookup);
- with a ten-line shim that restores `Signal.decoder` from `_format._variants`, all ten example designs (six without and four with the data path) compile to exactly the same instruction words as on 0.5.10, and the Amaranth reference simulation of the four data-path designs runs and gives the same number of received words (2, 2, 7).

Afterwards (same day): the adapter layer of option A was implemented, and the whole test suite passes on 0.5.1, 0.5.10 and 0.6.0.dev140 (see python/README.md). Not run: later 0.6 development versions.

## Options

| Option | What it takes | Remaining risk |
|---|---|---|
| A. Keep reading the IR, add an adapter in `_compat.py` (state names: `.decoder`, else `_format._variants`; `Fragment` from `amaranth.hdl`; statement classes imported from `_ast`) and run `python/scripts/matrix.sh` against Amaranth main regularly | about half a day, then upkeep | private names can move again; the gate must keep refusing untested versions |
| B. Use the FSM object's `.decoding` for the state names (needs the design to expose the FSM, for example `with platform.fsm(m) as fsm:` instead of `m.FSM()`) | small change for names only | the statement classes are still private |
| C. Replace raw assignments by PIO-specific helper calls so the compiler never inspects Amaranth expressions | a redesign; loses "write ordinary Amaranth" | none from Amaranth internals, but a different language |
| D. Ask Amaranth upstream for a public design-traversal API | not in our hands | open-ended |

Recommendation: A with B's name lookup as the first choice, widening the version gate to the versions the matrix has passed.
