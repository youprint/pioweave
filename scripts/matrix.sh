#!/bin/sh
# Run the Python tests of bitweave/python against given Amaranth versions, each in a fresh virtual environment.
# Usage: python/scripts/matrix.sh 0.5.10 [0.5.9 ...]      (needs network; bitweave and pioasm are used when found, see python/README.md)
# Only 0.5.10 is supported (the compiler reads Amaranth's private IR). pip refuses other versions through the package metadata, and amaranth_rp/_compat.py refuses them at import.
set -u
cd "$(dirname "$0")/../.."
ROOT=$PWD
for v in "$@"; do
  d=$(mktemp -d)
  echo "== amaranth $v"
  python3 -m venv "$d/venv" && "$d/venv/bin/pip" install -q "amaranth==$v" pytest 2>&1 | tail -2
  # --no-deps: pip itself would refuse a version outside amaranth>=0.5.10,<0.6; this shows what the version gate in _compat.py says
  "$d/venv/bin/pip" install -q --no-deps -e python/builder -e python/rp -e python/boards 2>&1 | tail -2
  if "$d/venv/bin/python" -W error -c "import amaranth_rp" 2>"$d/err.txt"; then
    "$d/venv/bin/python" -m pytest -q -W error python/builder/tests python/rp/tests python/boards/tests -p no:cacheprovider 2>&1 | tail -3
  else
    echo "refused: $(tail -1 "$d/err.txt")"
  fi
  rm -rf "$d"
done
