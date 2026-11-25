"""Compensation from single-stain controls, the quality checks, and group comparisons.

Every number that decides a warning comes from Project.prefs, where the person can change it. The
defaults there are proposals, not rules.
"""
from __future__ import annotations

import numpy as np
from scipy import stats as SS

from .project import Project


# ---------------------------------------------------------------------------- compensation
def _fluor_area(p: Project) -> list[str]:
    fl = [c["name"] for c in p.channels if c["kind"] == "fluor"]
    area = [n for n in fl if n.endswith("-A")]
    return area or fl


def comp_channels(p: Project) -> list[str]:
    """Channels a compensation matrix covers: the ones in the files' own matrix, else the fluorescence areas."""
    for s in p.samples:
        if s.spill:
            return list(s.spill[0])
    return _fluor_area(p)


def _pos_neg(p: Project, s, names: list[str], compensated: bool):
    """Median of the brightest events of a single-stain control and of its negative reference, per channel.

    Positive: the top `comp_top_pct` percent of events in the stained channel, leaving out events at the
    detector maximum. Negative: the unstained control if there is one, else the dimmest 15 % of the same tube.
    """
    a = p.data(s.id, compensated)
    idx = [s.ix[n] for n in names]
    prim = p.raw(s.id)[:, s.ix[s.stain]].astype(np.float64)   # choose the bright events before compensation
    top = float(p.ch(s.stain)["range"]) * 0.99
    ok = prim < top
    if ok.sum() < 50:
        return None
    cut = np.percentile(prim[ok], 100.0 - p.prefs["comp_top_pct"])
    pos = ok & (prim >= cut)
    un = next((u for u in p.samples if u.role == "unstained" and all(n in u.ix for n in names)), None)
    if un is not None:
        neg_med = np.median(p.data(un.id, compensated)[:, [un.ix[n] for n in names]].astype(np.float64), axis=0)
        neg_src = un.name
    else:
        lo = prim <= np.percentile(prim, 15.0)
        neg_med = np.median(a[lo][:, idx].astype(np.float64), axis=0)
        neg_src = "dimmest 15 % of the same tube"
    pos_med = np.median(a[pos][:, idx].astype(np.float64), axis=0)
    return pos_med, neg_med, int(pos.sum()), neg_src


def comp_from_controls(p: Project) -> dict:
    """Spillover matrix from the single-stain controls. Rows without a control are left as no spillover."""
    with p.lock:
        names = comp_channels(p)
        n = len(names)
        m = np.eye(n)
        used, notes = {}, []
        for s in p.samples:
            if s.role != "single" or not s.stain:
                continue
            if s.stain not in names or not all(k in s.ix for k in names):
                notes.append(f"{s.name}: its stained channel is not one of the compensated channels.")
                continue
            if s.stain in used:
                notes.append(f"{s.name}: a second control for {s.stain}; the first one ({used[s.stain]}) was used.")
                continue
            r = _pos_neg(p, s, names, compensated=False)
            if r is None:
                notes.append(f"{s.name}: too few events below the detector maximum.")
                continue
            pos, neg, npos, src = r
            i = names.index(s.stain)
            denom = pos[i] - neg[i]
            if denom <= 0:
                notes.append(f"{s.name}: the stained channel is not brighter than the negative reference.")
                continue
            m[i, :] = (pos - neg) / denom
            m[i, i] = 1.0
            used[s.stain] = s.name
        missing = [k for k in names if k not in used]
        if not used:
            raise ValueError("No single-stain controls with a stained channel were found. Set them on the Data tab.")
        return {"names": names, "matrix": m.tolist(), "used": used, "missing": missing, "notes": notes}


def comp_residuals(p: Project) -> list:
    """After compensation, how much of each single-stain control's signal is still seen in other channels."""
    out = []
    with p.lock:
        names = comp_channels(p)
        for s in p.samples:
            if s.role != "single" or not s.stain or s.stain not in names or not all(k in s.ix for k in names):
                continue
            r = _pos_neg(p, s, names, compensated=True)
            if r is None:
                continue
            pos, neg, npos, src = r
            i = names.index(s.stain)
            denom = pos[i] - neg[i]
            if denom <= 0:
                continue
            res = (pos - neg) / denom * 100.0
            for j, k in enumerate(names):
                if j != i:
                    out.append({"control": s.name, "from": s.stain, "into": k, "pct": float(res[j]), "signal": float(denom),
                                "dim": bool(denom < 0.01 * float(p.ch(s.stain)["range"]))})
    return out


def comp_view(p: Project) -> dict:
    with p.lock:
        s = p.sample(p.current) if p.current else None
        names, m, src = [], [], "none"
        if p.comp["mode"] == "matrix":
            names, m, src = p.comp["names"], p.comp["matrix"], "project matrix"
        elif p.comp["mode"] == "file" and s is not None and s.spill:
            names, m, src = s.spill[0], s.spill[1].tolist(), f"stored in {s.name}"
        res = comp_residuals(p) if p.comp["mode"] != "none" else []
        return {"mode": p.comp["mode"], "names": list(names), "matrix": m, "source": src,
                "channels": comp_channels(p), "residuals": res,
                "controls": [{"name": c.name, "stain": c.stain} for c in p.samples if c.role == "single"],
                "threshold": p.prefs["comp_residual_pct"]}


# ---------------------------------------------------------------------------- checks
def _c(level: str, title: str, detail: str, sid=None, key: str = "") -> dict:
    return {"level": level, "title": title, "detail": detail, "sid": sid, "key": key}


def checks(p: Project) -> list:
    """Project-wide checks first (sid None), then one list per file."""
    out = []
    with p.lock:
        pr = p.prefs
        if not p.samples:
            return out
        # 1. compensation
        fl = _fluor_area(p)
        if len(fl) > 1:
            if p.comp["mode"] == "none":
                out.append(_c("warn", "No compensation is applied", f"{len(fl)} fluorescence channels are used as measured.", key="comp"))
            elif p.comp["mode"] == "file":
                no = [s.name for s in p.samples if not s.spill]
                ident = [s.name for s in p.samples if s.spill and np.allclose(s.spill[1], np.eye(len(s.spill[0])))]
                if no:
                    out.append(_c("warn", f"{len(no)} file(s) carry no compensation matrix", ", ".join(no[:4]) + ("…" if len(no) > 4 else ""), key="comp"))
                elif len(ident) == len(p.samples):
                    out.append(_c("warn", "No compensation in these files", "The stored matrix is the identity: nothing is subtracted.", key="comp"))
                else:
                    out.append(_c("ok", "Compensation read from each file", f"{len(p.samples) - len(ident)} of {len(p.samples)} files have a non-trivial matrix.", key="comp"))
            else:
                out.append(_c("ok", "Project compensation matrix in use", f"{len(p.comp['names'])} channels.", key="comp"))
            singles = [s for s in p.samples if s.role == "single" and s.stain]
            if not singles:
                out.append(_c("warn", "No single-stain controls loaded", "Compensation cannot be checked without them.", key="singles"))
            elif p.comp["mode"] != "none":
                bad = [r for r in comp_residuals(p) if abs(r["pct"]) > pr["comp_residual_pct"]]
                if bad:
                    w = max(bad, key=lambda r: abs(r["pct"]))
                    kind = "over" if w["pct"] < 0 else "under"
                    out.append(_c("warn", f"Compensation looks off in {len(bad)} channel pair(s)",
                                  f"Worst: {w['from']} into {w['into']} is {kind}-compensated by {abs(w['pct']):.1f} % "
                                  f"(limit {pr['comp_residual_pct']:g} %)."
                                  + (" That control is dim, so the estimate is rough." if w.get("dim") else ""), key="residual"))
                else:
                    out.append(_c("ok", "Single-stain controls sit flat after compensation",
                                  f"Every residual is within {pr['comp_residual_pct']:g} %.", key="residual"))
        # 2. gates that differ between files
        own = [(sid, gid) for sid, ov in p.overrides.items() for gid in ov]
        if p.gates:
            if own:
                names = sorted({p.sample(sid).name for sid, _ in own if sid in p.by_id})
                out.append(_c("warn", f"{len(names)} file(s) have their own version of a gate",
                              ", ".join(names[:4]) + ("…" if len(names) > 4 else ""), key="own"))
            else:
                out.append(_c("ok", f"Gates are the same in all {len(p.samples)} files", "No file has its own version of a gate.", key="own"))
        # 3. controls for gated fluorescence channels
        gated = []
        for g in p.gates:
            for k in (g["x"], g.get("y")):
                if k and p.ch(k)["kind"] == "fluor" and k not in gated:
                    gated.append(k)
        has_un = any(s.role == "unstained" for s in p.samples)
        for k in gated:
            fmo = [s for s in p.samples if s.role in ("fmo", "isotype") and s.stain == k]
            if not fmo:
                out.append(_c("warn", f"No FMO control for {p.label(k)}",
                              "An unstained control is used for the overlay." if has_un else "There is no unstained control either.", key=f"fmo:{k}"))
                continue
            for g in p.gates:
                if k not in (g["x"], g.get("y")):
                    continue
                for c in fmo:
                    ms = p.masks(c.id)
                    par = int(ms[g["parent"]].sum()) if g["parent"] else p.raw(c.id).shape[0]
                    inside = 100.0 * int(ms[g["id"]].sum()) / par if par else 0.0
                    if inside > pr["fmo_inside_pct"]:
                        out.append(_c("warn", f"Gate “{g['name']}” reaches into the FMO control",
                                      f"{inside:.1f} % of {c.name} falls inside (limit {pr['fmo_inside_pct']:g} %).", key=f"fmo:{g['id']}"))
                    else:
                        out.append(_c("ok", f"Gate “{g['name']}” clears the FMO control",
                                      f"{inside:.2f} % of {c.name} falls inside.", key=f"fmo:{g['id']}"))
        # 4. instrument settings
        diff = []
        for c in p.channels:
            if c["kind"] == "time":
                continue
            vs = {}
            for s in p.samples:
                if c["name"] in s.ix:
                    v = s.channels[s.ix[c["name"]]].get("voltage")
                    if v not in (None, ""):
                        vs.setdefault(str(v), []).append(s.name)
            if len(vs) > 1:
                diff.append((c["name"], vs))
        if diff:
            name, vs = diff[0]
            out.append(_c("warn", f"{len(diff)} channel(s) were recorded at different voltages",
                          f"For example {name}: " + "; ".join(f"{k} V in {len(v)} file(s)" for k, v in vs.items())
                          + ". Intensities are not comparable between those files.", key="volt"))
        # per file
        for s in p.samples:
            if s.role != "sample" or (s.id not in p._raw and s.id != p.current):
                continue                       # study files already read: the checks never force a slow load
            out.extend(file_checks(p, s.id))
    return out


def file_checks(p: Project, sid: str) -> list:
    out = []
    pr = p.prefs
    s = p.sample(sid)
    a = p.raw(sid)
    n = a.shape[0]
    # saturation
    worst = None
    for c in s.channels:
        if c["kind"] == "time":
            continue
        v = a[:, s.ix[c["name"]]]
        frac = 100.0 * float((v >= c["range"] - 1).sum()) / n if n else 0.0
        if frac > pr["saturation_pct"] and (worst is None or frac > worst[1]):
            worst = (c["name"], frac)
    if worst:
        out.append(_c("warn", f"Events at the detector maximum in {worst[0]}",
                      f"{worst[1]:.1f} % of events (limit {pr['saturation_pct']:g} %). They cannot be measured.", sid, "sat"))
    # flow rate over time
    tname = next((c["name"] for c in s.channels if c["kind"] == "time"), None)
    if tname and n >= 2000:
        t = a[:, s.ix[tname]].astype(np.float64)
        if t.max() > t.min():
            counts = np.histogram(t, bins=20)[0].astype(float)
            med = np.median(counts)
            if med > 0:
                bad = np.abs(counts - med) / med * 100.0 > pr["rate_deviation_pct"]
                pct = 100.0 * bad.sum() / counts.size
                if pct > pr["rate_bad_bins_pct"]:
                    out.append(_c("warn", "Flow rate was uneven",
                                  f"{int(bad.sum())} of 20 time slices differ from the median rate by more than "
                                  f"{pr['rate_deviation_pct']:g} %.", sid, "rate"))
                else:
                    out.append(_c("ok", "Flow rate steady over time", f"{int(20 - bad.sum())} of 20 time slices are within "
                                  f"{pr['rate_deviation_pct']:g} % of the median rate.", sid, "rate"))
    # small gates
    ms = p.masks(sid)
    for g in p.gates:
        c = int(ms[g["id"]].sum())
        if c < pr["min_events"]:
            par = int(ms[g["parent"]].sum()) if g["parent"] else n
            lo, hi = wilson(c, par)
            out.append(_c("warn", f"Only {c:,} events in “{g['name']}”",
                          f"Below {pr['min_events']:g}. 95 % interval for the percentage: {lo:.2f} to {hi:.2f} %.", sid, f"few:{g['id']}"))
    return out


def wilson(k: int, n: int, z: float = 1.959964) -> tuple:
    """Wilson score interval for a proportion, in percent."""
    if n <= 0:
        return 0.0, 0.0
    ph = k / n
    d = 1 + z * z / n
    c = ph + z * z / (2 * n)
    h = z * np.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    return 100.0 * max(0.0, (c - h) / d), 100.0 * min(1.0, (c + h) / d)


# ---------------------------------------------------------------------------- group comparison
def compare(p: Project, key: str) -> dict:
    """One value per file, compared between groups. Files are the replicates; events never are."""
    with p.lock:
        d = next((q for q in p.stat_list() if q["key"] == key), None)
        if d is None:
            raise ValueError("That statistic no longer exists.")
        groups = []
        for g in p.groups():
            vals = [p.stat_value(s.id, d) for s in p.study() if s.group == g]
            vals = [v for v in vals if v is not None]
            groups.append({"name": g, "n": len(vals), "values": vals,
                           "mean": float(np.mean(vals)) if vals else None,
                           "sd": float(np.std(vals, ddof=1)) if len(vals) > 1 else None,
                           "median": float(np.median(vals)) if vals else None})
        tests, note = [], ""
        usable = [g for g in groups if g["n"] >= 2]
        small = [g["name"] for g in groups if g["n"] < 3]
        if len(usable) < 2:
            note = "A comparison needs at least two groups with two or more files each."
        else:
            arrs = [np.asarray(g["values"], dtype=float) for g in usable]
            flat = all(np.ptp(a) == 0 for a in arrs)
            if len(usable) == 2:
                if not flat:
                    t = SS.ttest_ind(arrs[0], arrs[1], equal_var=False)
                    tests.append({"name": "Welch's t-test", "stat": float(t.statistic), "p": float(t.pvalue), "what": "means, unequal variances"})
                u = SS.mannwhitneyu(arrs[0], arrs[1], alternative="two-sided")
                tests.append({"name": "Mann–Whitney U", "stat": float(u.statistic), "p": float(u.pvalue), "what": "ranks"})
            else:
                if not flat:
                    f = SS.f_oneway(*arrs)
                    tests.append({"name": "One-way ANOVA", "stat": float(f.statistic), "p": float(f.pvalue), "what": "means across all groups"})
                k = SS.kruskal(*arrs)
                tests.append({"name": "Kruskal–Wallis", "stat": float(k.statistic), "p": float(k.pvalue), "what": "ranks across all groups"})
            left = len(groups) - len(usable)
            bits = []
            if left:
                bits.append(f"{left} group(s) with a single file are left out of the test.")
            if any(g["n"] < 3 for g in usable):
                bits.append("Some groups have only two files: treat the p-values with caution.")
            note = " ".join(bits)
        return {"key": key, "title": d["title"], "groups": groups, "tests": tests, "note": note}
