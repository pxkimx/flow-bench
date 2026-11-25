"""PDF report: what was analysed, with which settings, every gate on every study file, the numbers and
every warning. Always printed light, whatever the app's appearance."""
from __future__ import annotations

import datetime as _dt
import io

import numpy as np

from . import __version__
from . import analysis as A
from . import transforms as TF
from .project import Project

INK, DIM, LINE, ACCENT = "#1c1c1e", "#6e6e73", "#d1d1d6", "#0a64c8"


def _density_colours(x, y):
    h, xe, ye = np.histogram2d(x, y, bins=96, range=[[0, 1], [0, 1]])
    ix = np.clip((x * 96).astype(int), 0, 95)
    iy = np.clip((y * 96).astype(int), 0, 95)
    d = np.log1p(h[ix, iy])
    return d / d.max() if d.size and d.max() > 0 else d


def _axis(ax, spec, which):
    t = TF.ticks(spec)
    major = [(d, lab) for d, lab in t if lab]
    minor = [d for d, lab in t if not lab]
    if which == "x":
        ax.set_xticks([d for d, _ in major])
        ax.set_xticklabels([lab for _, lab in major], fontsize=6)
        ax.set_xticks(minor, minor=True)
    else:
        ax.set_yticks([d for d, _ in major])
        ax.set_yticklabels([lab for _, lab in major], fontsize=6)
        ax.set_yticks(minor, minor=True)


def file_figure(p: Project, sid: str) -> bytes | None:
    """One PNG with every plot object for a file, gates drawn."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("fb", ["#283cb4", "#00a0eb", "#28be5a", "#fae128", "#e12d1e"])
    objs = [o for o in p.objects if o["type"] in ("density", "hist")]
    if not objs:
        return None
    r = p.render(sid)
    by = {o["id"]: o for o in r["objects"]}
    s = p.sample(sid)
    ms = p.masks(sid)
    cols = min(3, len(objs))
    rows = int(np.ceil(len(objs) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(2.45 * cols, 2.5 * rows), squeeze=False)
    for ax in axes.ravel()[len(objs):]:
        ax.axis("off")
    for ax, o in zip(axes.ravel(), objs):
        q = by[o["id"]]
        ax.set_title(f"{q['title']}  ({q['sub']})", fontsize=7, loc="left", color=INK, pad=3)
        for sp in ax.spines.values():
            sp.set_linewidth(0.5)
            sp.set_color("#8e8e93")
        ax.tick_params(length=2.5, width=0.5, colors="#48484a", pad=1.5)
        ax.tick_params(which="minor", length=1.3, width=0.4)
        ax.set_xlim(0, 1)
        if q.get("missing"):
            ax.text(.5, .5, "channel not in this file", ha="center", va="center", fontsize=7, color=DIM)
            continue
        m = ms[o["pop"]] if o.get("pop") else None
        x = np.clip(p._display(sid, o["x"], m), 0, 1)
        _axis(ax, p.transforms[o["x"]], "x")
        ax.set_xlabel(q["xl"], fontsize=7, labelpad=1.5)
        if o["type"] == "density":
            y = np.clip(p._display(sid, o["y"], m), 0, 1)
            step = max(1, int(np.ceil(x.size / 20000)))
            xs, ys = x[::step], y[::step]
            if xs.size:
                c = _density_colours(xs, ys)
                order = np.argsort(c)
                ax.scatter(xs[order], ys[order], c=c[order], cmap=cmap, s=0.6, linewidths=0, rasterized=True)
            ax.set_ylim(0, 1)
            _axis(ax, p.transforms[o["y"]], "y")
            ax.set_ylabel(q["yl"], fontsize=7, labelpad=1.5)
            for g in q.get("gates", []):
                pct = lambda v: "n/a" if v is None else f"{v:.1f}%"  # noqa: E731
                if g["type"] == "poly":
                    pts = np.array(g["pts"] + [g["pts"][0]])
                    ax.plot(pts[:, 0], pts[:, 1], color="#111", lw=0.8)
                    top = pts[:-1, 1].max()
                    ax.text(pts[:-1, 0].mean(), top + .02 if top < .9 else top - .02, f"{g['name']} {pct(g['pct'])}", fontsize=6,
                            ha="center", va="bottom" if top < .9 else "top")
                elif g["type"] == "rect":
                    ax.plot([g["x0"], g["x1"], g["x1"], g["x0"], g["x0"]], [g["y0"], g["y0"], g["y1"], g["y1"], g["y0"]], color="#111", lw=0.8)
                    ax.text((g["x0"] + g["x1"]) / 2, max(g["y0"], g["y1"]) + .02, f"{g['name']} {pct(g['pct'])}", fontsize=6, ha="center", va="bottom")
                elif g["type"] == "quad":
                    ax.axvline(g["cx"], color="#111", lw=0.7)
                    ax.axhline(g["cy"], color="#111", lw=0.7)
                    pos = {"UL": (.03, .97, "left", "top"), "UR": (.97, .97, "right", "top"), "LL": (.03, .03, "left", "bottom"), "LR": (.97, .03, "right", "bottom")}
                    for part in g["parts"]:
                        px, py, ha, va = pos[part["q"]]
                        ax.text(px, py, pct(part["pct"]), fontsize=6, ha=ha, va=va, transform=ax.transAxes)
        else:
            h, e = np.histogram(x, bins=128, range=(0, 1))
            mid = (e[:-1] + e[1:]) / 2
            top = h.max() if h.max() > 0 else 1
            ax.fill_between(mid, h / top, color="#00a0eb", alpha=.3, lw=0)
            ax.plot(mid, h / top, color="#0a64c8", lw=0.8)
            ctrl = q.get("control")
            if ctrl is None:
                c = p._control_for(o["x"], sid)
                if c is not None:
                    cm = p.masks(c.id).get(o["pop"]) if o.get("pop") else None
                    cv = p.data(c.id)[:, c.ix[o["x"]]]
                    cv = cv[cm] if cm is not None else cv
                    ch = np.histogram(np.clip(TF.fwd(p.transforms[o["x"]], cv.astype(float)), 0, 1), bins=128, range=(0, 1))[0]
                    ctrl = {"hist128": ch, "name": c.name}
            else:
                ctrl = {"hist128": np.asarray(ctrl["hist"]).reshape(128, 2).sum(axis=1), "name": ctrl["name"]}
            if ctrl is not None and ctrl["hist128"].max() > 0:
                ax.plot(mid, ctrl["hist128"] / ctrl["hist128"].max(), color="#8e8e93", lw=0.7, ls="--")
            ax.set_ylim(0, 1.18)
            ax.set_yticks([])
            ax.set_ylabel("events (scaled to peak)", fontsize=6, labelpad=2)
            for i, g in enumerate(q.get("gates", [])):
                if g["type"] == "range":
                    yy = 1.06 + 0.0 * i
                    ax.plot([g["x0"], g["x1"]], [yy, yy], color="#111", lw=0.8, marker="|", ms=5)
                    ax.text((g["x0"] + g["x1"]) / 2, yy + .02, f"{g['name']} " + ("n/a" if g["pct"] is None else f"{g['pct']:.1f}%"), fontsize=6, ha="center", va="bottom")
    fig.tight_layout(pad=0.6)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200)
    plt.close(fig)
    return buf.getvalue()


def pdf(p: Project) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24, alignment=0, textColor=colors.HexColor(INK), spaceAfter=2)
    H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=colors.HexColor(INK), spaceBefore=10, spaceAfter=4, keepWithNext=1)
    B = ParagraphStyle("B", parent=ss["BodyText"], fontName="Helvetica", fontSize=8.6, leading=11.5, textColor=colors.HexColor(INK))
    S = ParagraphStyle("S", parent=B, fontSize=7.6, leading=10, textColor=colors.HexColor(DIM))

    def tbl(rows, widths=None, head=True, size=7.4):
        t = Table(rows, colWidths=widths, repeatRows=1 if head else 0)
        st = [("FONT", (0, 0), (-1, -1), "Helvetica", size), ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(INK)),
              ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor(LINE)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
              ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
        if head:
            st += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", size), ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#8e8e93"))]
        t.setStyle(TableStyle(st))
        return t

    num = lambda v, d=1: "–" if v is None else f"{v:,.{d}f}"  # noqa: E731
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    story = []
    with p.lock:
        if not p.samples:
            raise ValueError("There are no files to report on.")
        story += [Paragraph("Flow cytometry report", H1),
                  Paragraph(f"Flow Bench {__version__} · {_dt.datetime.now():%d %B %Y, %H:%M} · {len(p.samples)} files, {len(p.gates)} gates", S)]
        if p.example:
            story.append(Paragraph(f"Example data: {esc(p.example['citation'])}, doi:{esc(p.example['doi'])}, Zenodo {esc(p.example['record'])}, "
                                   f"{esc(p.example['licence'])}. {esc(p.example.get('note', ''))}", S))
        story.append(Paragraph("Files", H2))
        rows = [["File", "Role", "Group", "Events", "Cytometer", "Date"]]
        for s in p.samples:
            role = s.role + (f" ({s.stain})" if s.stain else "")
            rows.append([Paragraph(esc(s.name), S), role, Paragraph(esc(s.group or "–"), S), f"{s.n:,}", s.cyt, s.date])
        story.append(tbl(rows, [62 * mm, 26 * mm, 42 * mm, 15 * mm, 22 * mm, 18 * mm]))
        # settings
        story.append(Paragraph("Compensation and scales", H2))
        cv = A.comp_view(p)
        mode = {"none": "No compensation was applied.", "file": "Each file was compensated with the matrix stored in it. "
                "Control tubes without a matrix of their own use the study files' matrix.",
                "matrix": "All files were compensated with one project matrix."}[p.comp["mode"]]
        story.append(Paragraph(mode, B))
        if cv["names"] and len(cv["names"]) <= 12:
            w = (175 - 26) / len(cv["names"])
            rows = [[""] + [Paragraph(esc(n.replace("-A", "")), S) for n in cv["names"]]]
            for n, row in zip(cv["names"], cv["matrix"]):
                rows.append([Paragraph(esc(n.replace("-A", "")), S)] + [f"{v * 100:.1f}" for v in row])
            story += [Spacer(1, 3), Paragraph(f"Spillover, percent ({esc(cv['source'])}; rows: fluorochrome, columns: detector)", S), tbl(rows, [26 * mm] + [w * mm] * len(cv["names"]), size=6.6)]
        rows = [["Channel", "Marker", "Scale", "Top (T)", "Decades (M)", "Width (W)"]]
        for c in p.channels:
            t = p.transforms[c["name"]]
            rows.append([c["name"], c["marker"] or "–", t["type"], num(t.get("T"), 0), num(t.get("M")) if "M" in t else "–", num(t.get("W")) if "W" in t else "–"])
        story += [Spacer(1, 5), tbl(rows, [38 * mm, 34 * mm, 24 * mm, 26 * mm, 26 * mm, 26 * mm])]
        # checks
        story.append(Paragraph("Checks", H2))
        cks = A.checks(p)
        rows = [["", "File", "Check", "Detail"]]
        for c in cks:
            rows.append(["warning" if c["level"] == "warn" else "ok", Paragraph(esc(p.sample(c["sid"]).name if c["sid"] else "project"), S),
                         Paragraph(esc(c["title"]), S), Paragraph(esc(c["detail"]), S)])
        t = tbl(rows, [14 * mm, 40 * mm, 58 * mm, 63 * mm])
        t.setStyle(TableStyle([("TEXTCOLOR", (0, i), (0, i), colors.HexColor("#b25000" if r[0] == "warning" else "#248a3d")) for i, r in enumerate(rows) if i]))
        story.append(t)
        pr = p.prefs
        story.append(Paragraph(f"Limits used: fewer than {pr['min_events']:g} events in a gate; more than {pr['fmo_inside_pct']:g} % of an FMO control inside a gate; "
                               f"compensation residual above {pr['comp_residual_pct']:g} %; more than {pr['saturation_pct']:g} % of events at a detector maximum; "
                               f"flow rate more than {pr['rate_deviation_pct']:g} % from the median in over {pr['rate_bad_bins_pct']:g} % of time slices. "
                               "Files that were not opened in this session are not checked one by one.", S))
        # statistics
        tb = p.table()
        if tb["stats"] and tb["rows"]:
            story += [PageBreak(), Paragraph("Statistics by group", H2),
                      Paragraph("One value per file. Groups are compared file against file, never event against event.", S), Spacer(1, 3)]
            for d in tb["stats"]:
                cmp = A.compare(p, d["key"])
                dec = 0 if d["stat"] == "count" else 2
                rows = [["Group", "Files", "Mean", "SD", "Median", "Values"]]
                for g in cmp["groups"]:
                    rows.append([Paragraph(esc(g["name"]), S), str(g["n"]), num(g["mean"], dec), num(g["sd"], dec), num(g["median"], dec),
                                 Paragraph(", ".join(num(v, dec) for v in g["values"]), S)])
                txt = "; ".join(f"{t['name']}: p = {t['p']:.3g}" for t in cmp["tests"]) or "No test run."
                story.append(KeepTogether([Paragraph(f"<b>{esc(d['title'])}</b>", B), tbl(rows, [58 * mm, 12 * mm, 20 * mm, 18 * mm, 20 * mm, 47 * mm]),
                                           Paragraph(esc(txt + (" " + cmp["note"] if cmp["note"] else "")), S), Spacer(1, 6)]))
        # per file
        names = {None: "All events"}
        for g in p.gates:
            names[g["id"]] = g["name"]
        for s in p.study():
            story.append(PageBreak())
            story.append(Paragraph(esc(s.name), H2))
            story.append(Paragraph(f"{esc(s.group or 'no group')} · {s.n:,} events · {esc(s.cyt)}", S))
            png = file_figure(p, s.id)
            if png:
                from PIL import Image as PI
                w, h = PI.open(io.BytesIO(png)).size
                width = min(175 * mm, w / 200 * 72 * 1.0)
                story += [Spacer(1, 4), Image(io.BytesIO(png), width=width, height=width * h / w)]
            rows = [["Gate", "Parent", "Events", "% of parent", "% of all", ""]]
            for row in p.tree(s.id):
                if row["id"] is None:
                    continue
                rows.append([Paragraph(esc("  " * (row["depth"] - 1) + row["name"]), S), Paragraph(esc(names.get(row.get("parent"), "All events")), S),
                             f"{row['count']:,}", num(row["pct_parent"], 2), num(row["pct_total"], 2), "this file's own gate" if row.get("own") else ""])
            if len(rows) > 1:
                story += [Spacer(1, 5), tbl(rows, [52 * mm, 42 * mm, 20 * mm, 22 * mm, 20 * mm, 19 * mm])]
            fc = A.file_checks(p, s.id)
            for c in fc:
                if c["level"] == "warn":
                    story.append(Paragraph(f"Warning: {esc(c['title'])}. {esc(c['detail'])}", S))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor(DIM))
        canvas.drawString(17 * mm, 10 * mm, f"Flow Bench {__version__}")
        canvas.drawRightString(A4[0] - 17 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=17 * mm, rightMargin=17 * mm, topMargin=15 * mm, bottomMargin=16 * mm,
                      title="Flow cytometry report", author="Flow Bench").build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
