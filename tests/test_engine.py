"""Engine tests on small made-up FCS files written for the test (never shipped as examples)."""
import io
import json
import os
import re
import xml.dom.minidom

import flowio
import numpy as np
import pytest

from flowbench import analysis as A
from flowbench import export as X
from flowbench import gates as G
from flowbench import transforms as TF
from flowbench.project import Project

CH = ["FSC-A", "SSC-A", "FL1-A", "FL2-A", "Time"]
SPILL = np.array([[1.0, 0.20], [0.05, 1.0]])


def write(path, n=4000, seed=1, pos=0.3, single=None, spill=True):
    """Two scatter channels, two fluorescence channels with known spillover, and time."""
    r = np.random.default_rng(seed)
    fsc = r.normal(100000, 15000, n)
    ssc = r.normal(60000, 10000, n)
    hi = r.random(n) < pos
    t1 = np.where(hi, r.normal(20000, 2000, n), r.normal(50, 30, n))
    t2 = np.where(~hi, r.normal(15000, 1500, n), r.normal(40, 30, n))
    if single == "FL1-A":
        t1, t2 = np.where(r.random(n) < .6, r.normal(30000, 2500, n), r.normal(50, 30, n)), r.normal(40, 30, n)
    if single == "FL2-A":
        t1, t2 = r.normal(50, 30, n), np.where(r.random(n) < .6, r.normal(25000, 2000, n), r.normal(40, 30, n))
    if single == "none":
        t1, t2 = r.normal(50, 30, n), r.normal(40, 30, n)
    obs = np.c_[t1, t2] @ SPILL
    data = np.c_[fsc, ssc, obs, np.linspace(0, 100, n)]
    meta = {"spillover": "2,FL1-A,FL2-A," + ",".join(str(v) for v in SPILL.ravel())} if spill else {}
    with open(path, "wb") as fh:
        flowio.create_fcs(fh, data.ravel().tolist(), CH, metadata_dict=meta)
    return data, hi


@pytest.fixture
def proj(tmp_path):
    files = []
    for i, (name, pos) in enumerate([("A_1", .30), ("A_2", .32), ("A_3", .28), ("B_1", .60), ("B_2", .58), ("B_3", .62)]):
        f = tmp_path / f"{name}.fcs"
        write(f, seed=10 + i, pos=pos)
        files.append(str(f))
    for name, single in [("Comp FL1 Stained Control", "FL1-A"), ("Comp FL2 Stained Control", "FL2-A"), ("Unstained Control", "none")]:
        f = tmp_path / f"{name}.fcs"
        write(f, seed=50 + len(files), single=single, spill=False)
        files.append(str(f))
    p = Project()
    p.add_files(files)
    return p


def test_reads_files_and_guesses_roles(proj):
    assert len(proj.samples) == 9
    roles = {s.name: (s.role, s.stain, s.group) for s in proj.samples}
    assert roles["A_1.fcs"] == ("sample", "", "A") and roles["B_3.fcs"][2] == "B"
    assert roles["Comp FL1 Stained Control.fcs"][:2] == ("single", "FL1-A")
    assert roles["Unstained Control.fcs"][0] == "unstained"
    assert proj.groups() == ["A", "B"]
    assert proj.comp["mode"] == "file"


def test_scales_round_trip():
    x = np.array([-500.0, -1, 0, 3, 250, 9999, 262144])
    for s in ({"type": "linear", "T": 262144.0, "A": 0.0}, {"type": "asinh", "T": 262144.0, "M": 4.5, "A": 0.0},
              {"type": "logicle", "T": 262144.0, "M": 4.5, "W": 0.5, "A": 0.0}, {"type": "logicle", "T": 10000.0, "M": 4.0, "W": 1.5, "A": 0.0}):
        assert np.allclose(TF.inv(s, TF.fwd(s, x)), x, rtol=1e-6, atol=1e-4), s
        assert abs(float(TF.fwd(s, s["T"])) - 1.0) < 1e-9
        assert all(0 <= d <= 1 for d, _ in TF.ticks(s))
    lg = {"type": "log", "T": 10000.0, "M": 4.0}
    assert np.allclose(TF.inv(lg, TF.fwd(lg, np.array([1.0, 37.0, 10000.0]))), [1, 37, 10000])


def test_compensation_recovers_the_true_signal(proj, tmp_path):
    s = next(q for q in proj.samples if q.name == "A_1.fcs")
    raw, comp = proj.raw(s.id), proj.data(s.id)
    assert np.allclose(comp[:, [2, 3]].astype(float) @ SPILL, raw[:, [2, 3]], rtol=1e-4, atol=0.5)
    # after compensation the FL2-negative events sit near their true value, not on the spillover line
    neg = comp[:, 2] > 5000
    assert abs(float(np.median(comp[neg, 3])) - 40) < 15 and float(np.median(raw[neg, 3])) > 3000
    proj.set_comp(mode="none")
    assert np.array_equal(proj.data(s.id), raw)


def test_matrix_from_single_stain_controls(proj):
    r = A.comp_from_controls(proj)
    assert r["names"] == ["FL1-A", "FL2-A"] and not r["missing"]
    assert np.allclose(np.array(r["matrix"]), SPILL, atol=0.01)
    proj.set_comp(names=r["names"], matrix=r["matrix"])
    proj.set_comp(mode="matrix")
    assert max(abs(x["pct"]) for x in A.comp_residuals(proj)) < 1.0
    # a deliberately wrong matrix is caught, with the right sign
    proj.set_comp(names=r["names"], matrix=[[1, 0.10], [0.05, 1]])
    res = {(x["from"], x["into"]): x["pct"] for x in A.comp_residuals(proj)}
    assert res[("FL1-A", "FL2-A")] > 5
    assert any(c["key"] == "residual" and c["level"] == "warn" and "under" in c["detail"] for c in A.checks(proj))


def test_gates_count_what_numpy_counts(proj):
    s = next(q for q in proj.samples if q.name == "B_1.fcs")
    a = proj.data(s.id).astype(float)
    tx, ty = proj.transforms["FSC-A"], proj.transforms["SSC-A"]
    d = lambda t, v: float(TF.fwd(t, v))  # noqa: E731
    rect = proj.add_gate(None, "rect", "FSC-A", "SSC-A", {"x0": d(tx, 80000), "x1": d(tx, 130000), "y0": d(ty, 40000), "y1": d(ty, 80000)}, "Cells")[0]
    want = (a[:, 0] >= 80000) & (a[:, 0] < 130000) & (a[:, 1] >= 40000) & (a[:, 1] < 80000)
    assert np.array_equal(proj.masks(s.id)[rect], want)
    t1 = proj.transforms["FL1-A"]
    rng = proj.add_gate(rect, "range", "FL1-A", None, {"x0": d(t1, 5000), "x1": d(t1, 200000)}, "FL1+")[0]
    assert np.array_equal(proj.masks(s.id)[rng], want & (a[:, 2] >= 5000) & (a[:, 2] < 200000))
    quad = proj.add_gate(rect, "quad", "FL1-A", "FL2-A", {"cx": d(t1, 3000), "cy": d(proj.transforms["FL2-A"], 3000)})
    ms = proj.masks(s.id)
    assert sum(int(ms[q].sum()) for q in quad) == int(want.sum())           # the four quadrants share out the parent exactly
    ur = next(g for g in proj.gates if g["id"] in quad and g["q"] == "UR")
    assert np.array_equal(ms[ur["id"]], want & (a[:, 2] >= 3000) & (a[:, 3] >= 3000))
    tri = [[d(t1, 3000), d(proj.transforms["FL2-A"], -200)], [d(t1, 200000), d(proj.transforms["FL2-A"], -200)], [d(t1, 200000), d(proj.transforms["FL2-A"], 3000)]]
    poly = proj.add_gate(rect, "poly", "FL1-A", "FL2-A", {"pts": tri}, "Tri")[0]
    inside = G.pip(TF.fwd(t1, a[:, 2]), TF.fwd(proj.transforms["FL2-A"], a[:, 3]), tri)
    assert np.array_equal(ms[rect] & inside, proj.masks(s.id)[poly])
    tree = {r["name"]: r for r in proj.tree(s.id)}
    assert tree["Cells"]["count"] == int(want.sum()) and abs(tree["FL1+"]["pct_parent"] - 100 * (want & (a[:, 2] >= 5000)).sum() / want.sum()) < 0.2


def test_a_gate_keeps_its_data_position_when_the_scale_changes(proj):
    s = proj.samples[0]
    t1 = proj.transforms["FL1-A"]
    g = proj.add_gate(None, "range", "FL1-A", None, {"x0": float(TF.fwd(t1, 5000)), "x1": float(TF.fwd(t1, 100000))}, "R")[0]
    before = int(proj.masks(s.id)[g].sum())
    proj.set_transform("FL1-A", {"type": "asinh"})
    assert int(proj.masks(s.id)[g].sum()) == before
    proj.set_transform("FL1-A", {"type": "logicle", "W": 1.5})
    assert int(proj.masks(s.id)[g].sum()) == before
    with pytest.raises(ValueError):
        proj.set_transform("FL1-A", {"type": "logicle", "W": 9})


def test_a_file_can_have_its_own_gate_and_it_is_reported(proj):
    a1 = next(q for q in proj.samples if q.name == "A_1.fcs")
    a2 = next(q for q in proj.samples if q.name == "A_2.fcs")
    b1 = next(q for q in proj.samples if q.name == "B_1.fcs")
    g = proj.add_gate(None, "range", "FSC-A", None, {"x0": .3, "x1": .5}, "R")[0]
    base = int(proj.masks(b1.id)[g].sum())
    proj.update_gate(g, {"x0": .0, "x1": .9}, "file", a1.id)
    assert g in proj.overrides[a1.id] and a2.id not in proj.overrides
    assert int(proj.masks(b1.id)[g].sum()) == base and int(proj.masks(a1.id)[g].sum()) > 3900
    assert any(c["key"] == "own" and c["level"] == "warn" for c in A.checks(proj))
    proj.update_gate(g, {"x0": .0, "x1": .9}, "group", a1.id)
    assert g in proj.overrides[a2.id] and b1.id not in proj.overrides
    proj.update_gate(g, {"x0": .3, "x1": .5}, "all", a1.id)                # "all files" puts every file back on the shared gate
    assert not proj.overrides
    proj.update_gate(g, {"x0": .1, "x1": .2}, "file", a1.id)
    proj.reset_gate(g, a1.id)
    assert not proj.overrides


def _gated(proj):
    t1 = proj.transforms["FL1-A"]
    return proj.add_gate(None, "range", "FL1-A", None, {"x0": float(TF.fwd(t1, 5000)), "x1": float(TF.fwd(t1, 250000))}, "FL1 high")[0]


def test_statistics_are_per_file_and_compare_groups(proj):
    g = _gated(proj)
    tb = proj.table()
    assert [r["name"] for r in tb["rows"]] == ["A_1.fcs", "A_2.fcs", "A_3.fcs", "B_1.fcs", "B_2.fcs", "B_3.fcs"]
    vals = {r["name"]: r["values"][0] for r in tb["rows"]}
    assert 26 < vals["A_1.fcs"] < 34 and 56 < vals["B_1.fcs"] < 64
    c = A.compare(proj, tb["stats"][0]["key"])
    assert [x["n"] for x in c["groups"]] == [3, 3] and {t["name"] for t in c["tests"]} == {"Welch's t-test", "Mann–Whitney U"}
    assert c["tests"][0]["p"] < 0.001
    proj.add_stat(g, "median", "FL1-A")
    proj.add_stat(g, "count")
    assert len(proj.stat_list()) == 3 and proj.table()["rows"][0]["values"][1] > 15000
    proj.set_sample(proj.samples[0].id, include=False)
    assert len(proj.table()["rows"]) == 5


def test_checks_flag_small_gates_and_missing_controls(proj):
    proj.add_gate(None, "range", "FL1-A", None, {"x0": .999, "x1": 1.0}, "Tiny")
    proj.current = next(q.id for q in proj.samples if q.role == "sample")
    cs = A.checks(proj)
    assert any(c["key"].startswith("few:") and "interval" in c["detail"] for c in cs)
    assert any(c["key"].startswith("fmo:") and c["level"] == "warn" for c in cs)
    lo, hi = A.wilson(5, 1000)
    assert 0.15 < lo < 0.25 and 1.1 < hi < 1.2


def test_prism_file_is_written_and_updated_in_place(proj, tmp_path):
    g = _gated(proj)
    z = X.new_pzfx(proj)
    xml.dom.minidom.parseString(z.encode())
    assert z.count("<Table ") == 1 and "<Title>FL1 high, % of All events</Title>" in z and "<Title>A</Title>" in z and "<Title>B</Title>" in z
    assert len(re.findall(r"<d>[\d.]+</d>", z)) == 6
    assert 'CreatedByProgram="GraphPad Prism"' in z            # Prism 10 warns about any other creating program
    assert "Written by Flow Bench" in z
    # a file as Prism leaves it: an extra table of the user's own and the binary block that must not change
    mine = '<Table ID="Table7" XFormat="none" TableType="OneWay" EVFormat="AsteriskAfterNumber">\n<Title>My own table</Title>\n<YColumn Width="81" Decimals="2" Subcolumns="1">\n<Title>x</Title>\n<Subcolumn>\n<d>42</d>\n</Subcolumn>\n</YColumn>\n</Table>'
    tail = '\n<!--Analyses, graphs and layouts as compressed binary. Don&apos;t edit this part of the file.-->\n\n<Template dt:dt="bin.base64" xmlns:dt="urn:schemas-microsoft-com:datatypes">eF7svQmcXMV1AAAA\n</Template>\n</GraphPadPrismFile>\n'
    z = z.replace("</TableSequence>", '<Ref ID="Table7"></Ref>\n</TableSequence>').replace("</GraphPadPrismFile>\n", mine + tail)
    # ... and with the header Flow Bench 0.1.0 wrote, which Prism 10 warns about: an update puts it right
    z = z.replace('CreatedByProgram="GraphPad Prism" CreatedByVersion="8.3.1.332"', 'CreatedByProgram="Flow Bench" CreatedByVersion="0.1.0"')
    assert 'CreatedByProgram="Flow Bench"' in z
    f = tmp_path / "stats.pzfx"
    f.write_text(z, encoding="utf-8")
    old = re.findall(r"<d>([\d.]+)</d>", z)[:6]
    t1 = proj.transforms["FL1-A"]
    proj.update_gate(g, {"x0": float(TF.fwd(t1, 21000)), "x1": 1.0}, "all", proj.current)
    proj.add_stat(g, "count")
    r = X.update_pzfx(proj, str(f))
    out = f.read_text(encoding="utf-8")
    assert r["updated"] == ["FL1 high, % of All events"] and r["added"] == ["FL1 high, events"] and (tmp_path / r["backup"]).exists()
    assert 'CreatedByProgram="Flow Bench"' not in out and 'CreatedByProgram="GraphPad Prism"' in out
    assert out.endswith(tail) and mine in out                                # the user's table and the binary block are untouched
    assert re.findall(r"<d>([\d.]+)</d>", out)[:6] != old and out.count("<Table ") == 3
    assert 'ID="Table0"' in out and 'ID="Table8"' in out and '<Ref ID="Table8"></Ref>' in out
    xml.dom.minidom.parseString(out.encode())
    assert (tmp_path / r["backup"]).read_text(encoding="utf-8") == z
    with pytest.raises(ValueError):
        bad = tmp_path / "x.pzfx"
        bad.write_text("not prism", encoding="utf-8")
        X.update_pzfx(proj, str(bad))


def test_excel_gatingml_and_project_file(proj, tmp_path):
    from openpyxl import load_workbook
    from test_gatingml import evaluate, load
    s = next(q for q in proj.samples if q.name == "B_2.fcs")
    proj.current = s.id
    t1, t2 = proj.transforms["FL1-A"], proj.transforms["FL2-A"]
    d = lambda t, v: float(TF.fwd(t, v))  # noqa: E731
    r = proj.add_gate(None, "rect", "FSC-A", "SSC-A", {"x0": .3, "x1": .5, "y0": .15, "y1": .3}, "Cells")[0]
    proj.add_gate(r, "poly", "FL1-A", "FL2-A", {"pts": [[d(t1, 2000), d(t2, -300)], [d(t1, 200000), d(t2, -300)], [d(t1, 200000), d(t2, 4000)], [d(t1, 2000), d(t2, 2000)]]}, "Poly")
    proj.add_gate(r, "quad", "FL1-A", "FL2-A", {"cx": d(t1, 3000), "cy": d(t2, 3000)})
    proj.add_gate(r, "range", "FL2-A", None, {"x0": d(t2, 4000), "x1": d(t2, 100000)}, "FL2+")
    wb = load_workbook(io.BytesIO(X.xlsx(proj)))
    assert wb.sheetnames == ["Statistics", "Gates by file", "Settings", "Checks"] and wb["Statistics"].max_row == 7
    gml = tmp_path / "gates.xml"
    gml.write_text(X.gatingml(proj), encoding="utf-8")
    gates = load(str(gml))
    ms = proj.masks(s.id)
    assert len(gates) == len(proj.gates) == 7
    for g in proj.gates:                                                       # written, read back by the test reader, same events
        assert np.array_equal(evaluate(proj, s.id, gates[g["id"]], gates), ms[g["id"]]), g["name"]
    f = tmp_path / "a.flowbench"
    proj.add_object("density", r)
    proj.save(str(f))
    assert json.loads(f.read_text())["app"] == "flow-bench"
    q = Project()
    assert q.open(str(f)) == {"missing": []}
    assert [g["name"] for g in q.gates] == [g["name"] for g in proj.gates] and len(q.objects) == len(proj.objects)
    s2 = next(x for x in q.samples if x.name == "B_2.fcs")
    assert [x["count"] for x in q.tree(s2.id)] == [x["count"] for x in proj.tree(s.id)]
    assert q.add_gate(None, "range", "FSC-A", None, {"x0": .1, "x1": .2})[0] not in {g["id"] for g in proj.gates}


def test_render_sends_points_once(proj):
    sid = proj.current
    r = proj.render(sid)
    o = r["objects"][0]
    assert o["type"] == "density" and len(o["px"]) > 100 and o["shown"] == o["n"] == 4000
    again = proj.render(sid, {o["id"]: o["key"]})["objects"][0]
    assert "px" not in again and again["key"] == o["key"]
    proj.set_transform("FSC-A", {"type": "log"})
    assert "px" in proj.render(sid, {o["id"]: o["key"]})["objects"][0]


def test_pdf_report_builds(proj):
    from flowbench import report
    _gated(proj)
    proj.add_object("hist", None, "FL1-A")
    proj.add_object("stats")
    b = report.pdf(proj)
    assert b[:5] == b"%PDF-" and len(b) > 50000
