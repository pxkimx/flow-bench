"""Exports: GraphPad Prism project (.pzfx), Excel workbook, Gating-ML 2.0.

Prism: a .pzfx file is XML. Its data tables are plain text; graphs, analyses and layouts are kept in
one binary block that Prism asks other programs not to edit. Flow Bench writes one Column table per
statistic (groups as columns, one value per file). "Update" replaces the numbers of tables that have
the same title in an existing file and leaves everything else untouched, so graphs and analyses made
in Prism redraw from the new numbers when the file is opened. A dated backup is made first.
"""
from __future__ import annotations

import datetime as _dt
import io
import os
import re
import shutil
from xml.sax.saxutils import escape, unescape

import numpy as np

from . import __version__
from . import analysis as A
from . import transforms as TF
from .project import Project


# ---------------------------------------------------------------------------- Prism
def prism_tables(p: Project) -> list:
    """[{title, decimals, groups: [{name, values, files}]}], one per statistic."""
    out = []
    with p.lock:
        groups = p.groups()
        for d in p.stat_list():
            cols = []
            for g in groups:
                vals, files = [], []
                for s in p.study():
                    if s.group == g:
                        vals.append(p.stat_value(s.id, d))
                        files.append(s.name)
                cols.append({"name": g, "values": vals, "files": files})
            out.append({"title": d["title"], "decimals": 0 if d["stat"] == "count" else 2, "groups": cols})
    return out


def _ycolumns(t: dict) -> str:
    parts = []
    for g in t["groups"]:
        cells = "\n".join("<d>%s</d>" % ("" if v is None else repr(round(float(v), 6))) for v in g["values"])
        parts.append(f'<YColumn Width="110" Decimals="{t["decimals"]}" Subcolumns="1">\n<Title>{escape(g["name"])}</Title>\n'
                     f"<Subcolumn>\n{cells}\n</Subcolumn>\n</YColumn>")
    return "\n".join(parts)


def _table_xml(tid: str, t: dict) -> str:
    return (f'<Table ID="{tid}" XFormat="none" TableType="OneWay" EVFormat="AsteriskAfterNumber">\n'
            f'<Title>{escape(t["title"])}</Title>\n<RowTitlesColumn Width="1">\n<Subcolumn></Subcolumn>\n</RowTitlesColumn>\n'
            f'{_ycolumns(t)}\n</Table>')


def _notes(p: Project, tables: list) -> str:
    lines = [f"Written by Flow Bench {__version__} on {_dt.datetime.now():%Y-%m-%d %H:%M}.",
             "Each table holds one statistic: one column per group, one row per file.", "Row order in every table:"]
    if tables:
        for g in tables[0]["groups"]:
            lines.append(f"  {g['name']}: " + "; ".join(g["files"]))
    return escape("\n".join(lines))


def new_pzfx(p: Project) -> str:
    tables = prism_tables(p)
    if not tables or not tables[0]["groups"]:
        raise ValueError("There is nothing to export yet: draw a gate and give the files a group on the Data tab.")
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    refs = "\n".join('<Ref ID="Table%d"%s></Ref>' % (i, ' Selected="1"' if i == 0 else "") for i in range(len(tables)))
    body = "\n".join(_table_xml(f"Table{i}", t) for i, t in enumerate(tables))
    return ('<?xml version="1.0" encoding="UTF-8"?><GraphPadPrismFile PrismXMLVersion="5.00">\n<Created>\n'
            f'<OriginalVersion {PRISM_HEADER} Login="" DateTime="{now}"></OriginalVersion>\n'
            "</Created>\n<InfoSequence>\n<Ref ID=\"Info0\" Selected=\"1\"></Ref>\n</InfoSequence>\n<Info ID=\"Info0\">\n"
            f"<Title>Flow Bench export</Title>\n<Notes>{_notes(p, tables)}</Notes>\n"
            f"<Constant><Name>Experiment Date</Name><Value>{_dt.date.today().isoformat()}</Value></Constant>\n"
            "<Constant><Name>Experiment ID</Name><Value></Value></Constant>\n"
            "<Constant><Name>Notebook ID</Name><Value></Value></Constant>\n"
            "<Constant><Name>Project</Name><Value></Value></Constant>\n"
            "<Constant><Name>Experimenter</Name><Value></Value></Constant>\n"
            "<Constant><Name>Protocol</Name><Value></Value></Constant>\n</Info>\n"
            f"<TableSequence>\n{refs}\n</TableSequence>\n{body}\n</GraphPadPrismFile>\n")


# Prism 10 warns "This file does not fully comply with the Prism XML format" and opens the file as Untitled
# unless the header names Prism as the creating program (checked in Prism 10.2.3). The Info sheet says that
# Flow Bench wrote the file.
PRISM_HEADER = 'CreatedByProgram="GraphPad Prism" CreatedByVersion="8.3.1.332"'
_OLD_HEADER = re.compile(r'CreatedByProgram="Flow Bench" CreatedByVersion="[^"]*"')
_TABLE = re.compile(r"<Table\s[^>]*>.*?</Table>", re.S)


def update_pzfx(p: Project, path: str) -> dict:
    """Replace the numbers of same-titled tables in an existing .pzfx; add tables that are new."""
    tables = prism_tables(p)
    if not tables or not tables[0]["groups"]:
        raise ValueError("There is nothing to export yet: draw a gate and give the files a group on the Data tab.")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if "<GraphPadPrismFile" not in text[:400]:
        raise ValueError("That is not a Prism .pzfx file. (Files saved as .prism cannot be updated: save as .pzfx in Prism.)")
    cut = text.find("<Template")
    head, tail = (text, "") if cut < 0 else (text[:cut], text[cut:])
    ours = {t["title"]: t for t in tables}
    updated, skipped = [], []

    def swap(m):
        block = m.group(0)
        tm = re.search(r"<Title>(.*?)</Title>", block, re.S)
        title = unescape(re.sub(r"<[^>]+>", "", tm.group(1))).strip() if tm else ""
        if title not in ours or title in updated:
            return block
        if 'TableType="OneWay"' not in block[:300]:
            skipped.append(title)
            return block
        updated.append(title)
        i = block.find("<YColumn")
        keep = block[:i] if i >= 0 else block[:block.rfind("</Table>")]
        return keep + _ycolumns(ours[title]) + "\n</Table>"

    head = _OLD_HEADER.sub(PRISM_HEADER, head, count=1)      # files written by Flow Bench 0.1.0
    head = _TABLE.sub(swap, head)
    new = [t for t in tables if t["title"] not in updated and t["title"] not in skipped]
    if new:
        nums = [int(n) for n in re.findall(r'<Table ID="Table(\d+)"', head)] + [int(n) for n in re.findall(r'<Ref ID="Table(\d+)"', head)]
        nxt = max(nums) + 1 if nums else 0
        ids = [f"Table{nxt + i}" for i in range(len(new))]
        blocks = "\n".join(_table_xml(i, t) for i, t in zip(ids, new))
        refs = "\n".join(f'<Ref ID="{i}"></Ref>' for i in ids)
        if "</TableSequence>" in head:
            head = head.replace("</TableSequence>", refs + "\n</TableSequence>", 1)
        else:
            anchor = head.find("<Table ")
            seq = f"<TableSequence>\n{refs}\n</TableSequence>\n"
            head = head[:anchor] + seq + head[anchor:] if anchor >= 0 else head.replace("</GraphPadPrismFile>", seq + "</GraphPadPrismFile>")
        last = head.rfind("</Table>")
        if last >= 0:
            head = head[:last + 8] + "\n" + blocks + head[last + 8:]
        else:
            head = head.replace("</GraphPadPrismFile>", blocks + "\n</GraphPadPrismFile>") if not tail else head + blocks + "\n\n"
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = re.sub(r"\.pzfx$", "", path, flags=re.I) + f".backup-{stamp}.pzfx"
    shutil.copy2(path, backup)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(head + tail)
    os.replace(tmp, path)
    return {"updated": updated, "added": [t["title"] for t in new], "skipped": skipped, "backup": os.path.basename(backup)}


# ---------------------------------------------------------------------------- Excel
def xlsx(p: Project) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    bold = Font(bold=True)
    with p.lock:
        tb = p.table()
        ws = wb.active
        ws.title = "Statistics"
        ws.append(["File", "Group"] + [d["title"] for d in tb["stats"]])
        for r in tb["rows"]:
            ws.append([r["name"], r["group"]] + r["values"])
        ws2 = wb.create_sheet("Gates by file")
        ws2.append(["File", "Role", "Group", "Gate", "Parent", "Events", "% of parent", "% of all events", "File's own version"])
        for s in p.samples:
            names = {None: "All events"}
            for row in p.tree(s.id):
                names[row["id"]] = row["name"]
                if row["id"] is None:
                    continue
                ws2.append([s.name, s.role, s.group, row["name"], names.get(row.get("parent"), "All events"), row["count"],
                            row["pct_parent"], row["pct_total"], "yes" if row.get("own") else ""])
        ws3 = wb.create_sheet("Settings")
        ws3.append(["Flow Bench", __version__])
        ws3.append(["Written", _dt.datetime.now().strftime("%Y-%m-%d %H:%M")])
        ws3.append(["Compensation", {"none": "none", "file": "matrix stored in each file", "matrix": "project matrix"}[p.comp["mode"]]])
        ws3.append([])
        ws3.append(["Channel", "Marker", "Scale", "T (top)", "M (decades)", "W (width)", "A"])
        for c in p.channels:
            t = p.transforms[c["name"]]
            ws3.append([c["name"], c["marker"], t["type"], t.get("T"), t.get("M"), t.get("W"), t.get("A")])
        ws3.append([])
        ws3.append(["Check thresholds"])
        for k, v in p.prefs.items():
            ws3.append([k, v])
        if p.comp["mode"] == "matrix":
            ws3.append([])
            ws3.append(["Project compensation matrix (rows: fluorochrome, columns: detector)"])
            ws3.append([""] + p.comp["names"])
            for n, row in zip(p.comp["names"], p.comp["matrix"]):
                ws3.append([n] + list(row))
        ws4 = wb.create_sheet("Checks")
        ws4.append(["Result", "File", "Check", "Detail"])
        for c in A.checks(p):
            ws4.append(["warning" if c["level"] == "warn" else "ok", p.sample(c["sid"]).name if c["sid"] else "(project)", c["title"], c["detail"]])
    for w in wb.worksheets:
        for cell in w[1]:
            cell.font = bold
        for col in w.columns:
            w.column_dimensions[col[0].column_letter].width = min(48, max(10, max(len(str(c.value)) if c.value is not None else 0 for c in col) + 2))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------- Gating-ML 2.0
def _tid(name: str) -> str:
    return "T_" + re.sub(r"[^A-Za-z0-9_]", "_", name)


def gatingml(p: Project, sid: str | None = None) -> str:
    """The gates (this file's versions if `sid` is given) as Gating-ML 2.0. Polygon corners are written in the
    scale of each axis, as the standard requires; rectangles and quadrants are written in data units."""
    with p.lock:
        mode = p.comp["mode"]
        cref_fl = {"none": "uncompensated", "file": "FCS", "matrix": "ProjectSpill"}[mode]
        covered = set(A.comp_channels(p)) if mode != "none" else set()
        cref = lambda ch: cref_fl if ch in covered else "uncompensated"  # noqa: E731
        o = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gating:Gating-ML xmlns:gating="http://www.isac-net.org/std/Gating-ML/v2.0/gating"',
             '    xmlns:transforms="http://www.isac-net.org/std/Gating-ML/v2.0/transformations"',
             '    xmlns:data-type="http://www.isac-net.org/std/Gating-ML/v2.0/datatypes">',
             f"  <!-- Flow Bench {__version__} -->"]
        if mode == "matrix":
            o.append('  <transforms:spectrumMatrix transforms:id="ProjectSpill">')
            for tag in ("fluorochromes", "detectors"):
                o.append(f"    <transforms:{tag}>")
                o += [f'      <data-type:fcs-dimension data-type:name="{escape(n)}" />' for n in p.comp["names"]]
                o.append(f"    </transforms:{tag}>")
            for row in p.comp["matrix"]:
                o.append("    <transforms:spectrum>")
                o += [f'      <transforms:coefficient transforms:value="{v!r}" />' for v in row]
                o.append("    </transforms:spectrum>")
            o.append("  </transforms:spectrumMatrix>")
        used = []
        for g in p.gates:
            if g["type"] == "poly":
                for ch in (g["x"], g["y"]):
                    if ch not in used:
                        used.append(ch)
        for ch in used:
            t = p.transforms[ch]
            if t["type"] == "linear":
                el = f'<transforms:flin transforms:T="{t["T"]!r}" transforms:A="{t.get("A", 0.0)!r}" />'
            elif t["type"] == "log":
                el = f'<transforms:flog transforms:T="{t["T"]!r}" transforms:M="{t["M"]!r}" />'
            elif t["type"] == "asinh":
                el = f'<transforms:fasinh transforms:T="{t["T"]!r}" transforms:M="{t["M"]!r}" transforms:A="{t.get("A", 0.0)!r}" />'
            else:
                el = (f'<transforms:logicle transforms:T="{t["T"]!r}" transforms:W="{t["W"]!r}" transforms:M="{t["M"]!r}" '
                      f'transforms:A="{t.get("A", 0.0)!r}" />')
            o.append(f'  <transforms:transformation transforms:id="{_tid(ch)}">\n    {el}\n  </transforms:transformation>')

        def dim(ch, tref=None, lo=None, hi=None):
            a = f' gating:compensation-ref="{cref(ch)}"'
            if tref:
                a += f' gating:transformation-ref="{tref}"'
            if lo is not None:
                a += f' gating:min="{lo!r}"'
            if hi is not None:
                a += f' gating:max="{hi!r}"'
            return (f"    <gating:dimension{a}>\n      <data-type:fcs-dimension data-type:name=\"{escape(ch)}\" />\n"
                    "    </gating:dimension>")

        for g in p.gates:
            geom = p.geom(sid, g) if sid else g["geom"]
            par = f' gating:parent_id="{g["parent"]}"' if g["parent"] else ""
            head = f' gating:id="{g["id"]}"{par}'
            o.append(f"  <!-- {escape(g['name'])} -->")
            if g["type"] == "poly":
                tx, ty = p.transforms[g["x"]], p.transforms[g["y"]]
                o.append(f"  <gating:PolygonGate{head}>")
                o.append(dim(g["x"], _tid(g["x"])))
                o.append(dim(g["y"], _tid(g["y"])))
                for a, b in geom["pts"]:
                    o.append("    <gating:vertex>\n"
                             f'      <gating:coordinate data-type:value="{float(TF.fwd(tx, a))!r}" />\n'
                             f'      <gating:coordinate data-type:value="{float(TF.fwd(ty, b))!r}" />\n    </gating:vertex>')
                o.append("  </gating:PolygonGate>")
            else:
                o.append(f"  <gating:RectangleGate{head}>")
                if g["type"] == "range":
                    o.append(dim(g["x"], None, geom["x0"], geom["x1"]))
                elif g["type"] == "rect":
                    o.append(dim(g["x"], None, geom["x0"], geom["x1"]))
                    o.append(dim(g["y"], None, geom["y0"], geom["y1"]))
                else:
                    q = g["q"]
                    o.append(dim(g["x"], None, geom["cx"] if q[1] == "R" else None, None if q[1] == "R" else geom["cx"]))
                    o.append(dim(g["y"], None, geom["cy"] if q[0] == "U" else None, None if q[0] == "U" else geom["cy"]))
                o.append("  </gating:RectangleGate>")
        o.append("</gating:Gating-ML>")
        return "\n".join(o) + "\n"


def stamp() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d")


def group_summary(p: Project) -> list:
    """Per statistic: group means and the comparison, for the report."""
    out = []
    for d in p.stat_list():
        out.append(A.compare(p, d["key"]))
    return out


def as_float(v):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else float(v)
