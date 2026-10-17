#!/bin/sh
# Run the pioweave tests against given Amaranth versions, each in a fresh virtual environment.
# Usage: scripts/matrix.sh 0.5.1 0.5.10 [...] [git:COMMIT]      (needs network; see docs/compatibility.rst)
#   `git:COMMIT` installs Amaranth from github.com/amaranth-lang/amaranth at that commit (the 0.6 development version in pioweave/_gate.py,
#   TESTED_DEV, is commit 90449f1e6d5abdfb8cac7519ac7b34d83a9f42b7).
#   MATRIX_QUICK=1       only the quick tests (refusals, builder, boards, the instruction words of every example): about a second per version
#   PIO_BUILD_FIRMWARE=1 also builds the generated Pico SDK projects (needs the SDK and arm-none-eabi-gcc, see docs/testing.rst)
# The tests that compare with the bitweave PIO simulator need the bitweave binary and pioasm: BITWEAVE and PIOASM, or a bitweave checkout beside this
# repository (../bitweave with target/release/bitweave and .tools/bin/pioasm). Without them those tests are skipped, not passed.
# pip refuses a version outside the range in pyproject.toml, and pioweave/_gate.py refuses a version it was not tested with.
#
# Every version ends with one result line: "result VERSION: OK", "result VERSION: FAILED (...)" or "result VERSION: refused (the gate's message)". The exit status is 0 only
# when every version is OK: a version that cannot be installed, that the gate refuses or whose tests fail makes it 1 (2 when no version was given).
set -u
cd "$(dirname "$0")/.."
ROOT=$PWD
SIBLING="$ROOT/../bitweave"
[ -z "${PIOASM:-}" ] && [ -x "$SIBLING/.tools/bin/pioasm" ] && export PIOASM="$SIBLING/.tools/bin/pioasm"
[ -z "${BITWEAVE:-}" ] && [ -x "$SIBLING/target/release/bitweave" ] && export BITWEAVE="$SIBLING/target/release/bitweave"
if [ "$#" -eq 0 ]; then
  echo "usage: scripts/matrix.sh VERSION... (for example 0.5.1 0.5.10 git:COMMIT)" >&2
  exit 2
fi
status=0
for v in "$@"; do
  d=$(mktemp -d) || { echo "result $v: FAILED (mktemp)"; status=1; continue; }
  echo "== amaranth $v"
  if ! python3 -m venv "$d/venv" > "$d/venv.txt" 2>&1; then
    tail -2 "$d/venv.txt"
    echo "result $v: FAILED (cannot create a virtual environment)"
    status=1; rm -rf "$d"; continue
  fi
  case "$v" in
    git:*) spec="git+https://github.com/amaranth-lang/amaranth.git@${v#git:}" ;;
    *) spec="amaranth==$v" ;;
  esac
  if ! "$d/venv/bin/pip" install -q "$spec" > "$d/pip.txt" 2>&1; then
    tail -2 "$d/pip.txt"
    echo "result $v: FAILED (cannot install $spec)"
    status=1; rm -rf "$d"; continue
  fi
  # --no-deps: pip would refuse a version outside the declared range; this shows what the version gate says
  if ! "$d/venv/bin/pip" install -q --no-deps -e . > "$d/pip2.txt" 2>&1; then
    tail -2 "$d/pip2.txt"
    echo "result $v: FAILED (cannot install pioweave)"
    status=1; rm -rf "$d"; continue
  fi
  if ! "$d/venv/bin/python" -W error -c "import pioweave, pioweave._compat" 2> "$d/err.txt"; then
    msg=$(grep -m1 "^[A-Za-z_.]*UnsupportedAmaranth:" "$d/err.txt" || tail -1 "$d/err.txt")
    echo "result $v: refused ($msg)"
    status=1; rm -rf "$d"; continue
  fi
  ok=1
  if [ "${MATRIX_QUICK:-0}" = 1 ]; then
    PYTHONWARNINGS=error "$d/venv/bin/python" -m unittest tests.test_refusals tests.test_program tests.test_boards > "$d/t1.txt" 2>&1 || ok=0
    tail -3 "$d/t1.txt"
    PYTHONWARNINGS=error "$d/venv/bin/python" -m unittest tests.test_compile.DataPathTests.test_example_programs_are_the_same_on_every_amaranth_version > "$d/t2.txt" 2>&1 || ok=0
    tail -3 "$d/t2.txt"
  else
    PYTHONWARNINGS=error "$d/venv/bin/python" -m unittest discover -t . -s tests > "$d/t1.txt" 2>&1 || ok=0
    tail -3 "$d/t1.txt"
  fi
  if [ "$ok" = 1 ]; then
    echo "result $v: OK"
  else
    echo "result $v: FAILED (the tests above)"
    status=1
  fi
  rm -rf "$d"
done
exit $status
