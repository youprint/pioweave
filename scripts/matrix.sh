#!/bin/sh
# Run the pioweave tests against given Amaranth versions, each in a fresh virtual environment.
# Usage: scripts/matrix.sh 0.5.1 0.5.10 [...] [git:COMMIT]      (needs network; see docs/compatibility.rst)
#   `git:COMMIT` installs Amaranth from github.com/amaranth-lang/amaranth at that commit (the 0.6 development version in pioweave/_compat.py,
#   TESTED_DEV, is commit 90449f1e6d5abdfb8cac7519ac7b34d83a9f42b7).
#   MATRIX_QUICK=1       only the quick tests (refusals, builder, boards, the instruction words of every example): about a second per version
#   PIO_BUILD_FIRMWARE=1 also builds the generated Pico SDK projects (needs the SDK and arm-none-eabi-gcc, see docs/testing.rst)
# The tests that compare with the bitweave PIO simulator need the bitweave binary and pioasm: BITWEAVE and PIOASM, or a bitweave checkout beside this
# repository (../bitweave with target/release/bitweave and .tools/bin/pioasm). Without them those tests are skipped, not passed.
# pip refuses a version outside the range in pyproject.toml, and pioweave/_compat.py refuses a version it was not tested with.
set -u
cd "$(dirname "$0")/.."
ROOT=$PWD
SIBLING="$ROOT/../bitweave"
[ -z "${PIOASM:-}" ] && [ -x "$SIBLING/.tools/bin/pioasm" ] && export PIOASM="$SIBLING/.tools/bin/pioasm"
[ -z "${BITWEAVE:-}" ] && [ -x "$SIBLING/target/release/bitweave" ] && export BITWEAVE="$SIBLING/target/release/bitweave"
for v in "$@"; do
  d=$(mktemp -d)
  echo "== amaranth $v"
  python3 -m venv "$d/venv" && case "$v" in
    git:*) "$d/venv/bin/pip" install -q "git+https://github.com/amaranth-lang/amaranth.git@${v#git:}" 2>&1 | tail -2 ;;
    *) "$d/venv/bin/pip" install -q "amaranth==$v" 2>&1 | tail -2 ;;
  esac
  # --no-deps: pip would refuse a version outside the declared range; this shows what the version gate says
  "$d/venv/bin/pip" install -q --no-deps -e . 2>&1 | tail -2
  if "$d/venv/bin/python" -W error -c "import pioweave" 2>"$d/err.txt"; then
    if [ "${MATRIX_QUICK:-0}" = 1 ]; then
      PYTHONWARNINGS=error "$d/venv/bin/python" -m unittest tests.test_refusals tests.test_program tests.test_boards 2>&1 | tail -3
      PYTHONWARNINGS=error "$d/venv/bin/python" -m unittest tests.test_compile.DataPathTests.test_example_programs_are_the_same_on_every_amaranth_version 2>&1 | tail -3
    else
      PYTHONWARNINGS=error "$d/venv/bin/python" -m unittest discover -t . -s tests 2>&1 | tail -3
    fi
  else
    echo "refused: $(tail -1 "$d/err.txt")"
  fi
  rm -rf "$d"
done
