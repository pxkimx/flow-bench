"""Flow Bench's gate maths against the Gating-ML 2.0 compliance set (ISAC), event for event.

The reference files (one FCS file, gate definitions, and for each gate a list saying which events are
inside) are not kept in this repository: `scripts/fetch_test_data.sh` downloads them from FlowKit's
repository into tests/data/gatingml. Without them these tests are skipped.

Only gates Flow Bench can draw are tested: rectangles (including one-dimensional ranges) and polygons,
with no compensation, the file's own matrix or a matrix given in the file, on linear, log, arcsinh and
logicle scales. Ellipsoid, Boolean and quadrant definitions, hyperlog and ratio scales are reported as
not covered.
"""
import glob
import os
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from flowbench import gates as G
from flowbench import transforms as TF
from flowbench.project import Project

HERE = os.path.join(os.path.dirname(__file__), "data", "gatingml")
NS = {"g": "http://www.isac-net.org/std/Gating-ML/v2.0/gating",
      "t": "http://www.isac-net.org/std/Gating-ML/v2.0/transformations",
      "d": "http://www.isac-net.org/std/Gating-ML/v2.0/datatypes"}
A = lambda ns, k: "{%s}%s" % (NS[ns], k)  # noqa: E731
pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(HERE, "data1.fcs")), reason="reference set not downloaded")


class Unsupported(Exception):
    pass


def _spec(el):
    kind = el.tag.split("}")[1]
    f = lambda k, default=None: float(el.get(A("t", k), default))  # noqa: E731
    if kind == "flin":
        return {"type": "linear", "T": f("T"), "A": f("A")}
    if kind == "flog":
        return {"type": "log", "T": f("T"), "M": f("M")}
    if kind == "fasinh":
        return {"type": "asinh", "T": f("T"), "M": f("M"), "A": f("A")}
    if kind == "logicle":
        return {"type": "logicle", "T": f("T"), "M": f("M"), "W": f("W"), "A": f("A")}
    raise Unsupported(kind)


def load(path):
    """-> {gate id: evaluator(project) -> bool array} for the supported gates in one file."""
    root = ET.parse(path).getroot()
    tfs = {}
    for t in root.findall("t:transformation", NS):
        try:
            tfs[t.get(A("t", "id"))] = _spec(list(t)[0])
        except Unsupported as e:
            tfs[t.get(A("t", "id"))] = e
    mats = {}
    for m in root.findall("t:spectrumMatrix", NS):
        fl = [x.get(A("d", "name")) for x in m.find("t:fluorochromes", NS)]
        det = [x.get(A("d", "name")) for x in m.find("t:detectors", NS)]
        rows = [[float(c.get(A("t", "value"))) for c in s] for s in m.findall("t:spectrum", NS)]
        mats[m.get(A("t", "id"))] = (fl, det, np.array(rows))
    out = {}
    for el in root:
        if not isinstance(el.tag, str) or "}" not in el.tag:
            continue
        kind = el.tag.split("}")[1]
        if kind not in ("RectangleGate", "PolygonGate"):
            if kind.endswith("Gate"):
                out[el.get(A("g", "id"))] = Unsupported(kind)
            continue
        gid = el.get(A("g", "id"))
        dims = []
        try:
            for dm in el.findall("g:dimension", NS):
                fd = dm.find("d:fcs-dimension", NS)
                if fd is None:
                    raise Unsupported("derived dimension")
                tr = dm.get(A("g", "transformation-ref"))
                spec = tfs.get(tr) if tr else None
                if isinstance(spec, Exception):
                    raise spec
                comp = dm.get(A("g", "compensation-ref")) or "uncompensated"
                if comp not in ("FCS", "uncompensated"):
                    fl, det, m = mats[comp]
                    if m.shape[0] != m.shape[1]:
                        raise Unsupported("non-square matrix")
                dims.append({"name": fd.get(A("d", "name")), "comp": comp, "spec": spec,
                             "min": dm.get(A("g", "min")), "max": dm.get(A("g", "max"))})
            verts = [[float(c.get(A("d", "value"))) for c in v.findall("g:coordinate", NS)] for v in el.findall("g:vertex", NS)]
            out[gid] = {"kind": kind, "dims": dims, "verts": verts, "parent": el.get(A("g", "parent_id")), "mats": mats}
        except Unsupported as e:
            out[gid] = e
    return out


def column(p, sid, dim, mats):
    s = p.sample(sid)
    name = dim["name"]
    if dim["comp"] == "uncompensated":
        v = p.raw(sid)[:, s.ix[name]]
    elif dim["comp"] == "FCS":
        p.set_comp(mode="file")
        v = p.data(sid)[:, s.ix[name]] if name in s.ix else None
    else:
        fl, det, m = mats[dim["comp"]]
        p.set_comp(names=det, matrix=m.tolist())
        p.set_comp(mode="matrix")
        v = p.data(sid)[:, s.ix[det[fl.index(name)] if name in fl else name]]
    v = np.asarray(v, dtype=np.float64)
    return TF.fwd(dim["spec"], v) if dim["spec"] else v


def evaluate(p, sid, gate, all_gates):
    cols = [column(p, sid, d, gate["mats"]) for d in gate["dims"]]
    if gate["kind"] == "PolygonGate":
        m = G.pip(cols[0], cols[1], gate["verts"])
    else:
        m = np.ones(cols[0].shape, dtype=bool)
        for c, d in zip(cols, gate["dims"]):
            if d["min"] is not None:
                m &= c >= float(d["min"])
            if d["max"] is not None:
                m &= c < float(d["max"])
    if gate["parent"]:
        par = all_gates.get(gate["parent"])
        if not isinstance(par, dict):
            raise Unsupported("parent gate not supported")
        m &= evaluate(p, sid, par, all_gates)
    return m


def run():
    p = Project()
    p.add_files([os.path.join(HERE, "data1.fcs")])
    sid = p.samples[0].id
    results = {}
    for path in sorted(glob.glob(os.path.join(HERE, "gml", "*.xml"))):
        gates = load(path)
        for gid, g in gates.items():
            truth = os.path.join(HERE, "truth", f"Results_{gid}.txt")
            if not os.path.exists(truth) or gid in results and results[gid][0] == "pass":
                continue
            if isinstance(g, Exception):
                results[gid] = ("skip", str(g))
                continue
            try:
                got = evaluate(p, sid, g, gates)
            except Unsupported as e:
                results[gid] = ("skip", str(e))
                continue
            want = np.loadtxt(truth, dtype=int).astype(bool)
            diff = int((got != want).sum())
            results[gid] = ("pass", "") if diff == 0 else ("fail", f"{diff} of {want.size} events differ")
    return results


def test_reference_gates_match_event_for_event():
    r = run()
    failed = {k: v[1] for k, v in r.items() if v[0] == "fail"}
    passed = [k for k, v in r.items() if v[0] == "pass"]
    assert not failed, failed
    assert len(passed) >= 20, f"only {len(passed)} reference gates were testable"


if __name__ == "__main__":
    r = run()
    for k in sorted(r):
        print(f"{r[k][0]:5s} {k:22s} {r[k][1]}")
    print({s: sum(1 for v in r.values() if v[0] == s) for s in ("pass", "fail", "skip")})
