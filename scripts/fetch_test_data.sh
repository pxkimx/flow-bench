#!/bin/bash
# Downloads the Gating-ML 2.0 compliance set used by tests/test_gatingml.py (one FCS file, gate definitions and,
# for each gate, the list of events that are inside) from FlowKit's repository into tests/data/gatingml.
# The files are not kept in this repository.
set -eu
DEST="$(cd "$(dirname "$0")/.." && pwd)/tests/data/gatingml"
mkdir -p "$DEST/gml" "$DEST/truth"; cd "$DEST"
curl -sS -m 60 "https://api.github.com/repos/whitews/FlowKit/git/trees/master?recursive=1" | python3 -c "
import json,sys
for x in json.load(sys.stdin)['tree']:
    p=x['path']
    if p=='data/gate_ref/data1.fcs' or p.startswith('data/gate_ref/gml/') or p.startswith('data/gate_ref/truth/Results'): print(p)" > .list
cat .list | xargs -P 12 -I{} sh -c 'f="{}"; out="${f#data/gate_ref/}"; [ -s "$out" ] || curl -sS -m 60 --retry 3 -o "$out" "https://raw.githubusercontent.com/whitews/FlowKit/master/$f"'
echo "Gating-ML reference set: $(ls gml | wc -l | tr -d ' ') gate files, $(ls truth | wc -l | tr -d ' ') answer files"
