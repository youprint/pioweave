"""The emulator against the Amaranth simulation of the same design: every example design and random designs (with and without the data path) are compiled to a PIO program,
run in the emulator and in the Amaranth simulator, and must agree pin by pin and cycle by cycle, on the words pushed (with their cycles) and on the FIFO contents left.
Needs no other tool (the same comparison against bitweave's simulator is in test_compile.py). PIO_COSIM_N sets the number of random designs (default 15)."""
import os

from pioweave import PLATFORMS, compile_design, designs
from pioweave.emulator import run_compiled
from pioweave.project import amaranth_run

from . import cosim, support

N = int(os.environ.get("PIO_COSIM_N", "15"))


def both(factory, cycles, stim=None, tx=(), tx_feed=(), drain=True):
    design, platform = factory()
    compiled = compile_design(design, platform, "t")
    a = amaranth_run(factory, cycles, stim, tx, tx_feed, drain)
    e = run_compiled(compiled, cycles, stim, tx, tx_feed, drain)
    waves = {g: e.wave(compiled.pin(g)) for g in compiled.output_gpios}
    return compiled, a, e, waves


@support.cases
class AmaranthAgreementTests(support.TestCase):
    @support.parametrize("name", sorted(designs.PROGRAMS))
    def test_examples_agree_with_the_amaranth_simulation(self, name):
        cls, chip, stim, cycles, opts = designs.entry(name)
        compiled, a, e, waves = both(lambda: (cls(), PLATFORMS[chip]()), cycles, stim, **opts)
        self.assertEqual({g: w for g, w in a["waves"].items() if g in waves}, waves)
        self.assertTrue(cosim.same_rx(a, {"rx": e.rx, "rx_left_words": e.rx_left}, opts.get("drain", True)))
        self.assertEqual((a["tx_left"], a["rx_left"]), (e.tx_left, len(e.rx_left)))

    @support.parametrize("seed", range(N))
    def test_random_designs_agree_with_the_amaranth_simulation(self, seed):
        spec = cosim.random_spec(seed)
        compiled, a, e, waves = both(cosim.random_factory(spec), 160, spec["stim"])
        undriven = {g: w for g, w in a["waves"].items() if g not in waves}
        self.assertTrue(all(set(w) <= {"0"} for w in undriven.values()))
        self.assertEqual({g: w for g, w in a["waves"].items() if g in waves}, waves, "seed %d\n%s\n%s" % (seed, compiled.program.pio_text(), spec))

    @support.parametrize("seed", range(N))
    def test_random_data_path_designs_agree_with_the_amaranth_simulation(self, seed):
        spec = cosim.random_data_spec(seed)
        compiled, a, e, waves = both(cosim.random_data_factory(spec), 160, spec["stim"], spec["tx"], spec["tx_feed"], spec["drain"])
        why = "seed %d\n%s\n%s" % (seed, compiled.program.pio_text(), spec)
        self.assertEqual({g: w for g, w in a["waves"].items() if g in waves}, waves, why)
        self.assertTrue(cosim.same_rx(a, {"rx": e.rx, "rx_left_words": e.rx_left}, spec["drain"]), why)
        self.assertEqual((a["tx_left"], a["rx_left"]), (e.tx_left, len(e.rx_left)), why)
