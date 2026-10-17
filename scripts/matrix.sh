#!/bin/sh
# Run the Python tests of bitweave/python against given Amaranth versions, each in a fresh virtual environment.
# Usage: python/scripts/matrix.sh 0.5.1 0.5.10 [...] [git:COMMIT]    (needs network; bitweave and pioasm are used when found, see python/README.md)
# `git:COMMIT` installs Amaranth from github.com/amaranth-lang/amaranth at that commit (the 0.6 development version in _compat.TESTED_DEV is commit 90449f1e6d5abdfb8cac7519ac7b34d83a9f42b7).
# MATRIX_QUICK=1 runs the quick tests only. PIO_BUILD_FIRMWARE=1 also builds Pico SDK projects (needs the SDK and arm-none-eabi-gcc under .tools).
# Supported versions: see _compat.py (TESTED, TESTED_DEV). (the compiler reads Amaranth's private IR). pip refuses other versions through the package metadata, and amaranth_rp/_compat.py refuses them at import.
set -u
cd "$(dirname "$0")/../.."
ROOT=$PWD
[ -z "${PIOASM:-}" ] && [ -x "$ROOT/.tools/bin/pioasm" ] && export PIOASM="$ROOT/.tools/bin/pioasm"      # the MicroPython project test needs it
for v in "$@"; do
  d=$(mktemp -d)
  echo "== amaranth $v"
  python3 -m venv "$d/venv" && case "$v" in
    git:*) "$d/venv/bin/pip" install -q pytest "git+https://github.com/amaranth-lang/amaranth.git@${v#git:}" 2>&1 | tail -2 ;;
    *) "$d/venv/bin/pip" install -q "amaranth==$v" pytest 2>&1 | tail -2 ;;
  esac
  # --no-deps: pip itself would refuse a version outside amaranth>=0.5,<0.6; this shows what the version gate in _compat.py says
  "$d/venv/bin/pip" install -q --no-deps -e python/builder -e python/rp -e python/boards 2>&1 | tail -2
  if "$d/venv/bin/python" -W error -c "import amaranth_rp" 2>"$d/err.txt"; then
    if [ "${MATRIX_QUICK:-0}" = 1 ]; then
      # MATRIX_QUICK=1: what does not need the co-simulation (about a second): refusals, the builder, the boards, and the instruction words of every example design
      "$d/venv/bin/python" -m pytest -q -W error python/rp/tests/test_refusals.py python/builder/tests python/boards/tests -p no:cacheprovider 2>&1 | tail -2
      "$d/venv/bin/python" -m pytest -q -W error python/rp/tests/test_compile.py -k same_on_every -p no:cacheprovider 2>&1 | tail -1
    else
      "$d/venv/bin/python" -m pytest -q -W error python/builder/tests python/rp/tests python/boards/tests -p no:cacheprovider 2>&1 | tail -3
    fi
  else
    echo "refused: $(tail -1 "$d/err.txt")"
  fi
  rm -rf "$d"
done
