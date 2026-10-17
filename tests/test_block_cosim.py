# Copyright (c) 2026 the project owner. All rights reserved. Proprietary. (see ../LICENSE)
"""Random blocks of two to four state machines (tests/cosim_block.py): the Amaranth simulation of the whole block and the emulator running the compiled programs together
must agree cycle by cycle on the pins, the IRQ flags, the words received and what is left in the FIFOs; each machine alone in the emulator, in the environment that
`machine_cases` derives, must repeat what it did in the block; and (with the tools) the same single-machine cases pass in bitweave's simulator. PIO_COSIM_N sets the
number of random blocks (default 15)."""
import os

from pioweave.block import block_run, emulate_block, machine_cases

from . import cosim_block, support
from .test_block import agree, solo_matches
from .test_project import needs_tools, run_bitweave

N = int(os.environ.get("PIO_COSIM_N", "15"))


@support.cases
class RandomBlockTests(support.TestCase):
    @support.parametrize("seed", range(N))
    def test_random_blocks_agree_with_the_emulator(self, seed):
        spec = cosim_block.random_block_spec(seed)
        block, sim = cosim_block.random_block(spec)
        why = "seed %d\n%s" % (seed, spec)
        bc, a, e = agree(self, block, sim, why)
        solo_matches(self, bc, sim, a, why)

    @needs_tools
    @support.parametrize("seed", range(N))
    def test_random_blocks_pass_in_bitweaves_simulator(self, seed):
        spec = cosim_block.random_block_spec(seed)
        block, sim = cosim_block.random_block(spec)
        out = self.tmp_path / "p"
        block.build("rnd", str(out), project="pio", sim=sim)
        r, res = run_bitweave(out, "rnd", "pio")
        why = "seed %d\n%s\n%s" % (seed, spec, (out / "rnd.pio").read_text())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr + why)
        sims = [s for s in res["steps"] if s["step"].startswith("sim:")]
        self.assertEqual(len(sims), len(spec["machines"]), why)
        self.assertTrue(all(s["status"] == "pass" for s in sims), "%s\n%s" % (sims, why))
