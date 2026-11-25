"""The open project: files, compensation, axis scales, gates, the page layout, and everything computed from them.

One Project lives in the server. All access goes through its lock. Events are read from disk on demand
and kept in a size-limited cache; nothing is written next to the user's files.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import zlib
from collections import OrderedDict
from pathlib import Path

import numpy as np

from . import __version__
from . import gates as G
from . import transforms as TF
from .fcs import Sample

HOME = Path.home() / "Library" / "Application Support" / "Flow Bench"
MAX_POINTS = 40000            # events drawn per plot; counts always use every event
CACHE_BYTES = 3 * 1024 ** 3
ROLES = ("sample", "unstained", "single", "fmo", "isotype")
QUADS = ("UL", "UR", "LL", "LR")

# Proposed thresholds for the checks. They are starting values for the person to confirm or change,
# not rules; each is shown with its value wherever it is used.
DEFAULT_PREFS = {"min_events": 100, "fmo_inside_pct": 1.0, "comp_residual_pct": 2.0, "saturation_pct": 1.0,
                 "rate_deviation_pct": 50.0, "rate_bad_bins_pct": 10.0, "comp_top_pct": 10.0}


def prefs_path() -> Path:
    return Path(os.environ.get("FB_PREFS") or (HOME / "preferences.json"))


def load_prefs() -> dict:
    out = dict(DEFAULT_PREFS)
    try:
        d = json.loads(prefs_path().read_text(encoding="utf-8"))
        for k, v in d.items():
            if k in out and isinstance(v, (int, float)):
                out[k] = float(v)
    except (OSError, ValueError):
        pass
    return out


def save_prefs(p: dict) -> None:
    f = prefs_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    changed = {k: v for k, v in p.items() if k in DEFAULT_PREFS and v != DEFAULT_PREFS[k]}
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(changed, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(f)


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")


def short_name(label: str) -> str:
    s = re.sub(r"-(A|H|W)$", "", label.strip())
    return s if len(s) <= 14 else s[:13] + "…"


class Project:
    def __init__(self):
        self.lock = threading.RLock()
        self.prefs = load_prefs()
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        self.samples: list[Sample] = []
        self.by_id: dict[str, Sample] = {}
        self.channels: list[dict] = []          # reference channel list (union over files, first file first)
        self.transforms: dict[str, dict] = {}
        self.comp = {"mode": "none", "names": [], "matrix": []}
        self.gates: list[dict] = []             # parents always come before their children
        self.overrides: dict[str, dict] = {}    # sample id -> gate id -> geometry (this file's own version)
        self.objects: list[dict] = []           # what is on the layout page
        self.statdefs: list[dict] = []          # statistics the person added (beyond % of parent per gate)
        self.group_order: list[str] = []        # optional order of groups in tables, charts and Prism
        self.current: str | None = None
        self.prism_path: str | None = None
        self.project_path: str | None = None
        self.example: dict | None = None
        self.dirty = False
        self._n = {"s": 0, "g": 0, "o": 0, "q": 0}
        self.comp_ver = self.gate_ver = self.tf_ver = 0
        self._raw: OrderedDict[str, np.ndarray] = OrderedDict()
        self._compd: dict[str, tuple] = {}
        self._masks: dict[str, tuple] = {}

    def _id(self, k: str) -> str:
        self._n[k] += 1
        return f"{k}{self._n[k]}"

    def ch(self, name: str) -> dict:
        for c in self.channels:
            if c["name"] == name:
                return c
        raise ValueError(f"No channel called {name}.")

    def label(self, name: str) -> str:
        c = self.ch(name)
        return f"{c['marker']} ({c['name']})" if c["marker"] and c["marker"] != c["name"] else c["name"]

    def sample(self, sid: str) -> Sample:
        if sid not in self.by_id:
            raise ValueError("That file is no longer in the project.")
        return self.by_id[sid]

    # ------------------------------------------------------------------ files
    def add_files(self, paths: list[str]) -> dict:
        added, skipped = [], []
        with self.lock:
            have = {s.path for s in self.samples}
            for p in sorted(paths, key=lambda q: os.path.basename(q).lower()):
                p = os.path.abspath(p)
                if not p.lower().endswith(".fcs") or p in have:
                    continue
                try:
                    s = Sample(self._id("s"), p)
                except Exception as e:  # noqa: BLE001  unreadable or damaged file
                    skipped.append(f"{os.path.basename(p)}: {e}")
                    continue
                self.samples.append(s)
                self.by_id[s.id] = s
                have.add(p)
                added.append(s)
                for c in s.channels:
                    if not any(r["name"] == c["name"] for r in self.channels):
                        self.channels.append(dict(c))
                        self.transforms[c["name"]] = TF.default_for(c["kind"], c["range"])
            if added:
                self._guess(added)
                if self.comp["mode"] == "none" and not self.comp["names"] and any(s.spill for s in self.samples):
                    self.comp["mode"] = "file"
                    self.comp_ver += 1
                if self.current is None:
                    first = next((s for s in self.samples if s.role == "sample"), self.samples[0])
                    self.current = first.id
                self._fit_time()
                if not self.objects:
                    x, y = self._scatter_axes()
                    self.objects.append({"id": self._id("o"), "type": "density", "pop": None, "x": x, "y": y})
                self.dirty = True
        return {"added": len(added), "skipped": skipped}

    def _fit_time(self) -> None:
        """Time has no useful $PnR: scale it to the longest acquisition seen in the keywords or data."""
        for c in self.channels:
            if c["kind"] == "time" and not c.get("fitted") and self.current:
                try:
                    a = self.raw(self.current)
                    s = self.sample(self.current)
                    if c["name"] in s.ix:
                        mx = float(a[:, s.ix[c["name"]]].max())
                        if mx > 0:
                            self.transforms[c["name"]] = {"type": "linear", "T": mx * 1.02, "A": 0.0}
                            c["fitted"] = True
                except Exception:  # noqa: BLE001
                    pass

    def _scatter_axes(self) -> tuple:
        names = [c["name"] for c in self.channels]
        sc = [c["name"] for c in self.channels if c["kind"] == "scatter"]
        x = "FSC-A" if "FSC-A" in names else (sc[0] if sc else names[0])
        y = "SSC-A" if "SSC-A" in names else (sc[1] if len(sc) > 1 else names[min(1, len(names) - 1)])
        return x, y

    def _guess(self, new: list[Sample]) -> None:
        """Guess role, stained channel and group from file names. Shown on the Data tab to be confirmed."""
        fluor = [c["name"] for c in self.channels if c["kind"] == "fluor"]
        for s in new:
            base = re.sub(r"\.fcs$", "", s.name, flags=re.I)
            low = base.lower()
            core = re.sub(r"^specimen_\d+_", "", base, flags=re.I)
            core = re.sub(r"_\d{3}$", "", core)
            if re.search(r"unstain", low):
                s.role = "unstained"
            elif re.search(r"\bfmo\b|fmo[_ -]|[_ -]fmo", low):
                s.role = "fmo"
            elif re.search(r"isotype", low):
                s.role = "isotype"
            elif re.search(r"compensation controls|single[ _-]?stain|\bcomp[_ -]|stained control", low):
                s.role = "single"
            if s.role in ("single", "fmo"):
                key = re.sub(r"[^a-z0-9]", "", low)
                best = ""
                for f in fluor:
                    stem = re.sub(r"[^a-z0-9]", "", re.sub(r"-(A|H|W)$", "", f).lower())
                    mk = re.sub(r"[^a-z0-9]", "", (self.ch(f)["marker"] or "").lower())
                    if (stem and stem in key) or (mk and mk in key):
                        if len(stem) > len(re.sub(r"[^a-z0-9]", "", re.sub(r"-(A|H|W)$", "", best).lower())):
                            best = f
                s.stain = best
            if s.role == "sample":
                m = re.match(r"^(.*?)[ _-]+(\d{1,2})$", core) or None
                m2 = re.match(r"^(\d{1,2})[ _-]+(.+)$", core)
                if m and m.group(1).strip():
                    s.group = m.group(1).strip()
                elif m2:
                    s.group = m2.group(2).strip()
        # the same group spelled with different capitals is one group
        seen: dict[str, str] = {}
        for s in self.samples:
            if s.group:
                seen.setdefault(s.group.lower(), s.group)
                s.group = seen[s.group.lower()]

    def set_sample(self, sid: str, **kw) -> None:
        with self.lock:
            s = self.sample(sid)
            if "role" in kw:
                if kw["role"] not in ROLES:
                    raise ValueError("Unknown role.")
                s.role = kw["role"]
            if "stain" in kw:
                if kw["stain"] and kw["stain"] not in [c["name"] for c in self.channels]:
                    raise ValueError("Unknown channel.")
                s.stain = kw["stain"] or ""
            if "group" in kw:
                s.group = str(kw["group"] or "").strip()[:60]
            if "include" in kw:
                s.include = bool(kw["include"])
            self.dirty = True

    def remove_sample(self, sid: str) -> None:
        with self.lock:
            s = self.sample(sid)
            self.samples.remove(s)
            del self.by_id[sid]
            self.overrides.pop(sid, None)
            for d in (self._raw, self._compd, self._masks):
                d.pop(sid, None)
            if self.current == sid:
                self.current = self.samples[0].id if self.samples else None
            self.dirty = True

    def groups(self) -> list[str]:
        out: list[str] = []
        for s in self.samples:
            if s.role == "sample" and s.include and s.group and s.group not in out:
                out.append(s.group)
        order = {g: i for i, g in enumerate(self.group_order)}
        return sorted(out, key=lambda g: (order.get(g, len(order)), out.index(g)))

    def study(self) -> list[Sample]:
        return [s for s in self.samples if s.role == "sample" and s.include]

    # ------------------------------------------------------------------ events
    def raw(self, sid: str) -> np.ndarray:
        with self.lock:
            if sid in self._raw:
                self._raw.move_to_end(sid)
                return self._raw[sid]
            a = self.sample(sid).load()
            self._raw[sid] = a
            total = sum(v.nbytes for v in self._raw.values())
            while total > CACHE_BYTES and len(self._raw) > 1:
                k, v = self._raw.popitem(last=False)
                total -= v.nbytes
                self._compd.pop(k, None)
                self._masks.pop(k, None)
            return a

    def spill_for(self, s: Sample):
        """(names, matrix) used for this file under the current setting, or None."""
        mode = self.comp["mode"]
        if mode == "file":
            own = s.spill
            trivial = own is None or np.allclose(own[1], np.eye(len(own[0])))
            if trivial and s.role != "sample":
                # control tubes are often exported without the experiment's matrix: use the study files' one
                for t in self.samples:
                    if t.role == "sample" and t.spill and not np.allclose(t.spill[1], np.eye(len(t.spill[0]))):
                        return t.spill
            return own
        if mode == "matrix" and self.comp["names"]:
            return self.comp["names"], np.asarray(self.comp["matrix"], dtype=float)
        return None

    def data(self, sid: str, compensated: bool = True) -> np.ndarray:
        """Events for a file, compensated under the current setting."""
        with self.lock:
            raw = self.raw(sid)
            if not compensated:
                return raw
            hit = self._compd.get(sid)
            if hit and hit[0] == self.comp_ver:
                return hit[1]
            s = self.sample(sid)
            sp = self.spill_for(s)
            out = raw
            if sp is not None:
                names, m = sp
                if all(n in s.ix for n in names) and len(names):
                    idx = [s.ix[n] for n in names]
                    try:
                        inv = np.linalg.inv(np.asarray(m, dtype=np.float64))
                    except np.linalg.LinAlgError as e:
                        raise ValueError("The compensation matrix cannot be inverted. Check for an empty row.") from e
                    out = raw.copy()
                    out[:, idx] = (raw[:, idx].astype(np.float64) @ inv).astype(np.float32)
            self._compd[sid] = (self.comp_ver, out)
            return out

    def geom(self, sid: str, g: dict) -> dict:
        return self.overrides.get(sid, {}).get(g["id"], g["geom"])

    def masks(self, sid: str) -> dict:
        with self.lock:
            key = (self.comp_ver, self.gate_ver, self.tf_ver)
            hit = self._masks.get(sid)
            if hit and hit[0] == key:
                return hit[1]
            a = self.data(sid)
            s = self.sample(sid)
            out: dict[str, np.ndarray] = {}
            for g in self.gates:
                if g["x"] not in s.ix or (g.get("y") and g["y"] not in s.ix):
                    m = np.zeros(a.shape[0], dtype=bool)
                else:
                    X = a[:, s.ix[g["x"]]].astype(np.float64)
                    Y = a[:, s.ix[g["y"]]].astype(np.float64) if g.get("y") else None
                    m = G.member(g, self.geom(sid, g), X, Y, self.transforms[g["x"]],
                                 self.transforms[g["y"]] if g.get("y") else None)
                if g["parent"]:
                    m = m & out[g["parent"]]
                out[g["id"]] = m
            self._masks[sid] = (key, out)
            return out

    def pop_mask(self, sid: str, pop):
        return self.masks(sid)[pop] if pop else None

    def pop_name(self, pop) -> str:
        return self.gate(pop)["name"] if pop else "All events"

    # ------------------------------------------------------------------ gates
    def gate(self, gid: str) -> dict:
        for g in self.gates:
            if g["id"] == gid:
                return g
        raise ValueError("That gate no longer exists.")

    def _family(self, g: dict) -> list[dict]:
        return [h for h in self.gates if h.get("quad") == g["quad"]] if g.get("quad") else [g]

    def _descendants(self, ids: set) -> set:
        out = set(ids)
        grew = True
        while grew:
            grew = False
            for g in self.gates:
                if g["parent"] in out and g["id"] not in out:
                    out.add(g["id"])
                    grew = True
        return out

    def add_gate(self, parent, kind: str, x: str, y, geom_d: dict, name: str = "") -> list[str]:
        with self.lock:
            if parent:
                self.gate(parent)
            self.ch(x)
            if kind != "range":
                self.ch(y)
            tx, ty = self.transforms[x], (self.transforms[y] if kind != "range" else None)
            geom = G.from_display(kind, geom_d, tx, ty)
            ids = []
            if kind == "quad":
                qid = self._id("q")
                sx, sy = short_name(self.ch(x)["marker"] or x), short_name(self.ch(y)["marker"] or y)
                for q in QUADS:
                    nm = f"{sx}{'+' if q[1] == 'R' else '−'} {sy}{'+' if q[0] == 'U' else '−'}"
                    g = {"id": self._id("g"), "name": nm, "parent": parent or None, "type": "quad", "x": x, "y": y,
                         "geom": dict(geom), "q": q, "quad": qid}
                    self.gates.append(g)
                    ids.append(g["id"])
            else:
                used = {g["name"] for g in self.gates}
                nm = (name or "").strip()[:40]
                k = 1
                while not nm or nm in used:
                    nm = f"Gate {k}"
                    k += 1
                g = {"id": self._id("g"), "name": nm, "parent": parent or None, "type": kind, "x": x,
                     "y": (y if kind != "range" else None), "geom": geom}
                self.gates.append(g)
                ids.append(g["id"])
            self.gate_ver += 1
            self.dirty = True
            return ids

    def update_gate(self, gid: str, geom_d: dict, scope: str, sid) -> None:
        with self.lock:
            g = self.gate(gid)
            tx, ty = self.transforms[g["x"]], (self.transforms[g["y"]] if g.get("y") else None)
            geom = G.from_display(g["type"], geom_d, tx, ty)
            fam = self._family(g)
            if scope == "all":
                for h in fam:
                    h["geom"] = dict(geom)
                    for ov in self.overrides.values():
                        ov.pop(h["id"], None)
            else:
                s = self.sample(sid)
                targets = [s] if scope == "file" else [t for t in self.samples if t.group == s.group and t.role == s.role]
                for t in targets:
                    for h in fam:
                        self.overrides.setdefault(t.id, {})[h["id"]] = dict(geom)
            self.overrides = {k: v for k, v in self.overrides.items() if v}
            self.gate_ver += 1
            self.dirty = True

    def reset_gate(self, gid: str, sid: str) -> None:
        """Drop this file's own version of a gate and use the shared one again."""
        with self.lock:
            for h in self._family(self.gate(gid)):
                self.overrides.get(sid, {}).pop(h["id"], None)
            self.overrides = {k: v for k, v in self.overrides.items() if v}
            self.gate_ver += 1
            self.dirty = True

    def rename_gate(self, gid: str, name: str) -> None:
        with self.lock:
            name = (name or "").strip()[:40]
            if not name:
                raise ValueError("A gate needs a name.")
            self.gate(gid)["name"] = name
            self.dirty = True

    def delete_gate(self, gid: str) -> None:
        with self.lock:
            g = self.gate(gid)
            gone = self._descendants({h["id"] for h in self._family(g)})
            self.gates = [h for h in self.gates if h["id"] not in gone]
            for ov in self.overrides.values():
                for k in list(ov):
                    if k in gone:
                        del ov[k]
            self.objects = [o for o in self.objects if o.get("pop") not in gone]
            self.statdefs = [d for d in self.statdefs if d.get("pop") not in gone]
            for o in self.objects:
                if o["type"] == "chart" and o.get("stat", {}).get("pop") in gone:
                    o["stat"] = None
            self.gate_ver += 1
            self.dirty = True

    # ------------------------------------------------------------------ layout objects
    def default_axes(self, pop) -> tuple:
        names = [c["name"] for c in self.channels]
        fl = [c["name"] for c in self.channels if c["kind"] == "fluor" and c["name"].endswith("-A")] or \
             [c["name"] for c in self.channels if c["kind"] == "fluor"]
        if pop:
            g = self.gate(pop)
            if {g["x"], g.get("y")} == {"FSC-A", "SSC-A"} and "FSC-H" in names:
                return "FSC-A", "FSC-H"
            used = {(o.get("x"), o.get("y")) for o in self.objects}
            for i in range(0, len(fl) - 1):
                for j in range(i + 1, len(fl)):
                    if (fl[i], fl[j]) not in used:
                        return fl[i], fl[j]
        if len(fl) >= 2 and pop:
            return fl[0], fl[1]
        return self._scatter_axes()

    def add_object(self, kind: str, pop=None, x=None, y=None, stat=None) -> str:
        with self.lock:
            if pop:
                self.gate(pop)
            o = {"id": self._id("o"), "type": kind}
            if kind == "density":
                dx, dy = self.default_axes(pop)
                o.update(pop=pop or None, x=x or dx, y=y or dy)
            elif kind == "hist":
                fl = [c["name"] for c in self.channels if c["kind"] == "fluor"]
                o.update(pop=pop or None, x=x or (fl[0] if fl else self.channels[0]["name"]))
            elif kind == "chart":
                o.update(stat=stat)
            elif kind != "stats":
                raise ValueError("Unknown object.")
            self.objects.append(o)
            self.dirty = True
            return o["id"]

    def update_object(self, oid: str, **kw) -> None:
        with self.lock:
            o = next((q for q in self.objects if q["id"] == oid), None)
            if not o:
                raise ValueError("That object is no longer on the page.")
            for k in ("x", "y"):
                if kw.get(k):
                    self.ch(kw[k])
                    o[k] = kw[k]
            if "pop" in kw:
                if kw["pop"]:
                    self.gate(kw["pop"])
                o["pop"] = kw["pop"] or None
            if "stat" in kw:
                o["stat"] = kw["stat"]
            self.dirty = True

    def delete_object(self, oid: str) -> None:
        with self.lock:
            self.objects = [o for o in self.objects if o["id"] != oid]
            self.dirty = True

    def move_object(self, oid: str, delta: int) -> None:
        with self.lock:
            i = next((k for k, o in enumerate(self.objects) if o["id"] == oid), None)
            if i is None:
                return
            j = max(0, min(len(self.objects) - 1, i + delta))
            self.objects.insert(j, self.objects.pop(i))
            self.dirty = True

    # ------------------------------------------------------------------ scales and compensation
    def set_transform(self, name: str, spec: dict) -> None:
        with self.lock:
            c = self.ch(name)
            cur = self.transforms[name]
            kind = spec.get("type", cur["type"])
            t = float(spec.get("T") or cur.get("T") or c["range"])
            if t <= 0:
                raise ValueError("The top of the scale must be above zero.")
            if kind == "linear":
                new = {"type": "linear", "T": t, "A": 0.0}
            elif kind == "log":
                new = {"type": "log", "T": t, "M": float(spec.get("M", cur.get("M", 4.5)))}
            elif kind == "asinh":
                new = {"type": "asinh", "T": t, "M": float(spec.get("M", cur.get("M", 4.5))), "A": float(spec.get("A", 0.0))}
            elif kind == "logicle":
                m = float(spec.get("M", cur.get("M", 4.5)))
                w = float(spec.get("W", cur.get("W", 0.5)))
                if not (0 <= w <= m / 2):
                    raise ValueError("Logicle width W must be between 0 and half the number of decades.")
                new = {"type": "logicle", "T": t, "M": m, "W": w, "A": float(spec.get("A", 0.0))}
            else:
                raise ValueError("Unknown scale.")
            self.transforms[name] = new
            self.tf_ver += 1
            self.dirty = True

    def set_comp(self, mode=None, names=None, matrix=None) -> None:
        with self.lock:
            if names is not None and matrix is not None:
                m = np.asarray(matrix, dtype=float)
                if m.shape != (len(names), len(names)):
                    raise ValueError("The matrix must be square, one row and one column per channel.")
                if not np.all(np.isfinite(m)):
                    raise ValueError("Every cell of the matrix needs a number.")
                self.comp["names"], self.comp["matrix"] = list(names), m.tolist()
            if mode is not None:
                if mode not in ("none", "file", "matrix"):
                    raise ValueError("Unknown compensation setting.")
                if mode == "matrix" and not self.comp["names"]:
                    raise ValueError("There is no project matrix yet. Compute one from controls or copy the file's.")
                self.comp["mode"] = mode
            self.comp_ver += 1
            self.dirty = True

    # ------------------------------------------------------------------ numbers
    def tree(self, sid: str) -> list:
        """Rows for the gate view of one file: All events, then every gate in order."""
        with self.lock:
            s = self.sample(sid)
            ms = self.masks(sid)
            n = self.raw(sid).shape[0]
            rows = [{"id": None, "name": "All events", "depth": 0, "count": n, "pct_parent": 100.0, "pct_total": 100.0}]
            depth = {None: 0}
            for g in self.gates:
                d = depth[g["parent"]] + 1
                depth[g["id"]] = d
                c = int(ms[g["id"]].sum())
                pc = n if not g["parent"] else int(ms[g["parent"]].sum())
                rows.append({"id": g["id"], "name": g["name"], "depth": d, "parent": g["parent"], "type": g["type"],
                             "count": c, "pct_parent": (100.0 * c / pc) if pc else None,
                             "pct_total": (100.0 * c / n) if n else None,
                             "own": g["id"] in self.overrides.get(s.id, {})})
            return rows

    def stat_list(self) -> list:
        """Every statistic the project reports: % of parent for each gate, then the ones the person added."""
        out = [{"pop": g["id"], "stat": "pct_parent", "channel": None, "auto": True} for g in self.gates]
        out += [dict(d, auto=False) for d in self.statdefs]
        for d in out:
            d["key"] = f"{d['pop']}|{d['stat']}|{d.get('channel') or ''}"
            d["title"] = self.stat_title(d)
        return out

    def stat_title(self, d: dict) -> str:
        pop = self.pop_name(d.get("pop"))
        k = d["stat"]
        if k == "pct_parent":
            par = self.pop_name(self.gate(d["pop"])["parent"]) if d.get("pop") else ""
            return f"{pop}, % of {par}"
        if k == "pct_total":
            return f"{pop}, % of all events"
        if k == "count":
            return f"{pop}, events"
        ch = short_name(self.ch(d["channel"])["marker"] or d["channel"]) if d.get("channel") else "?"
        return f"{pop}, {'median' if k == 'median' else 'geometric mean'} {ch}"

    def stat_value(self, sid: str, d: dict):
        ms = self.masks(sid)
        n = self.raw(sid).shape[0]
        pop = d.get("pop")
        m = ms[pop] if pop else None
        c = int(m.sum()) if m is not None else n
        k = d["stat"]
        if k == "count":
            return float(c)
        if k == "pct_total":
            return 100.0 * c / n if n else None
        if k == "pct_parent":
            if not pop:
                return 100.0
            par = self.gate(pop)["parent"]
            pc = int(ms[par].sum()) if par else n
            return 100.0 * c / pc if pc else None
        s = self.sample(sid)
        if d.get("channel") not in s.ix or c == 0:
            return None
        v = self.data(sid)[:, s.ix[d["channel"]]]
        v = v[m] if m is not None else v
        if k == "median":
            return float(np.median(v))
        if k == "geomean":
            v = v[v > 0]
            return float(np.exp(np.mean(np.log(v.astype(np.float64))))) if v.size else None
        raise ValueError("Unknown statistic.")

    def add_stat(self, pop, stat: str, channel=None) -> None:
        with self.lock:
            if stat not in ("pct_total", "count", "median", "geomean", "pct_parent"):
                raise ValueError("Unknown statistic.")
            if pop:
                self.gate(pop)
            if stat in ("median", "geomean"):
                self.ch(channel)
            else:
                channel = None
            d = {"pop": pop or None, "stat": stat, "channel": channel}
            if stat == "pct_parent" or d in self.statdefs:
                return
            self.statdefs.append(d)
            self.dirty = True

    def remove_stat(self, key: str) -> None:
        with self.lock:
            self.statdefs = [d for d in self.statdefs if f"{d['pop']}|{d['stat']}|{d.get('channel') or ''}" != key]
            self.dirty = True

    def table(self) -> dict:
        """One row per study file, one column per statistic."""
        with self.lock:
            defs = self.stat_list()
            rows = []
            for s in self.study():
                rows.append({"id": s.id, "name": s.name, "group": s.group,
                             "values": [self.stat_value(s.id, d) for d in defs]})
            return {"stats": defs, "rows": rows, "groups": self.groups()}

    # ------------------------------------------------------------------ what the page draws
    def _display(self, sid: str, name: str, mask) -> np.ndarray:
        s = self.sample(sid)
        v = self.data(sid)[:, s.ix[name]]
        if mask is not None:
            v = v[mask]
        return TF.fwd(self.transforms[name], v.astype(np.float64))

    def _gates_on(self, sid: str, pop, x: str, y) -> list:
        ms = self.masks(sid)
        n = self.raw(sid).shape[0]
        pc = int(ms[pop].sum()) if pop else n
        out, quads = [], {}
        for g in self.gates:
            if g["parent"] != (pop or None) or g["x"] != x:
                continue
            if g["type"] == "range":
                pass
            elif y is None or g.get("y") != y:
                continue
            c = int(ms[g["id"]].sum())
            item = {"id": g["id"], "name": g["name"], "type": g["type"], "count": c,
                    "pct": (100.0 * c / pc) if pc else None, "own": g["id"] in self.overrides.get(sid, {}),
                    **G.to_display(g, self.geom(sid, g), self.transforms[g["x"]],
                                   self.transforms[g["y"]] if g.get("y") else None)}
            if g["type"] == "quad":
                q = quads.setdefault(g["quad"], {"type": "quad", "id": g["id"], "quad": g["quad"], "cx": item["cx"],
                                                 "cy": item["cy"], "own": item["own"], "parts": []})
                q["parts"].append({"id": g["id"], "q": g["q"], "name": g["name"], "count": c, "pct": item["pct"]})
                if q not in out:
                    out.append(q)
            else:
                out.append(item)
        return out

    def _control_for(self, channel: str, sid: str):
        """The control to draw behind a histogram: an FMO for this channel, else an unstained file."""
        for role in ("fmo", "isotype"):
            for s in self.samples:
                if s.role == role and s.stain == channel and s.id != sid and channel in s.ix:
                    return s
        for s in self.samples:
            if s.role == "unstained" and s.id != sid and channel in s.ix:
                return s
        return None

    def render(self, sid: str, have: dict | None = None) -> dict:
        """Everything on the layout page for one file. Points are left out for objects the page already has."""
        have = have or {}
        with self.lock:
            s = self.sample(sid)
            n = self.raw(sid).shape[0]
            ms = self.masks(sid)
            objs = []
            for o in self.objects:
                r = {"id": o["id"], "type": o["type"]}
                if o["type"] in ("density", "hist"):
                    pop = o.get("pop")
                    x, y = o["x"], o.get("y") if o["type"] == "density" else None
                    r.update(pop=pop, title=self.pop_name(pop),
                             sub=("all events" if not pop else "gated on " + self.pop_name(self.gate(pop)["parent"])),
                             x=x, y=y, xl=self.label(x), yl=self.label(y) if y else "",
                             xt=TF.ticks(self.transforms[x]), yt=TF.ticks(self.transforms[y]) if y else [],
                             xs=self.transforms[x]["type"], ys=self.transforms[y]["type"] if y else None)
                    missing = x not in s.ix or (y and y not in s.ix)
                    m = ms[pop] if pop else None
                    cnt = int(m.sum()) if m is not None else n
                    r["n"] = cnt
                    if missing:
                        r["missing"] = True
                        objs.append(r)
                        continue
                    crc = zlib.crc32(np.packbits(m).tobytes()) if m is not None else 0
                    key = f"{sid}|{pop}|{x}|{y}|{cnt}|{crc}|{self.comp_ver}|{json.dumps(self.transforms[x], sort_keys=True)}|" \
                          f"{json.dumps(self.transforms[y], sort_keys=True) if y else ''}"
                    key = f"{zlib.crc32(key.encode()):08x}"
                    r["key"] = key
                    r["gates"] = self._gates_on(sid, pop, x, y)
                    if have.get(o["id"]) != key:
                        dx = self._display(sid, x, m)
                        if o["type"] == "density":
                            dy = self._display(sid, y, m)
                            step = max(1, int(np.ceil(dx.size / MAX_POINTS)))
                            r["shown"] = int(dx[::step].size)
                            r["px"] = _b64((np.clip(dx[::step], 0, 1) * 65535).astype("<u2"))
                            r["py"] = _b64((np.clip(dy[::step], 0, 1) * 65535).astype("<u2"))
                        else:
                            r["hist"] = np.histogram(np.clip(dx, 0, 1), bins=256, range=(0, 1))[0].tolist()
                            c = self._control_for(x, sid)
                            if c is not None:
                                cm = self.masks(c.id).get(pop) if pop else None
                                cv = self.data(c.id)[:, c.ix[x]]
                                cv = cv[cm] if cm is not None else cv
                                cd = np.clip(TF.fwd(self.transforms[x], cv.astype(np.float64)), 0, 1)
                                r["control"] = {"name": c.name, "role": c.role, "n": int(cd.size),
                                                "hist": np.histogram(cd, bins=256, range=(0, 1))[0].tolist()}
                elif o["type"] == "chart":
                    r["chart"] = self.chart(o.get("stat"))
                objs.append(r)
            return {"sid": sid, "name": s.name, "n": n, "cyt": s.cyt, "objects": objs, "tree": self.tree(sid)}

    def chart(self, stat) -> dict | None:
        defs = self.stat_list()
        d = next((q for q in defs if stat and q["key"] == stat.get("key")), None) or (defs[-1] if defs else None)
        if not d:
            return None
        groups = []
        for gname in self.groups():
            vals = []
            for s in self.study():
                if s.group == gname:
                    v = self.stat_value(s.id, d)
                    if v is not None:
                        vals.append({"sid": s.id, "name": s.name, "v": v})
            groups.append({"name": gname, "values": vals})
        return {"key": d["key"], "title": d["title"], "groups": groups,
                "unit": "%" if d["stat"].startswith("pct") else ""}

    # ------------------------------------------------------------------ summary for the page
    def state(self) -> dict:
        with self.lock:
            return {
                "version": __version__,
                "samples": [s.info() for s in self.samples],
                "channels": [{"name": c["name"], "marker": c["marker"], "kind": c["kind"], "label": self.label(c["name"]),
                              "scale": self.transforms[c["name"]]} for c in self.channels],
                "groups": self.groups(),
                "comp": {"mode": self.comp["mode"], "has_matrix": bool(self.comp["names"]),
                         "files_with_matrix": sum(1 for s in self.samples if s.spill)},
                "gates": [{k: g.get(k) for k in ("id", "name", "parent", "type", "x", "y", "q", "quad")} for g in self.gates],
                "objects": [dict(o) for o in self.objects],
                "stats": self.stat_list(),
                "current": self.current,
                "prism": {"path": self.prism_path, "name": os.path.basename(self.prism_path) if self.prism_path else None,
                          "exists": bool(self.prism_path and os.path.exists(self.prism_path))},
                "project": {"path": self.project_path, "name": os.path.basename(self.project_path) if self.project_path else None,
                            "dirty": self.dirty},
                "example": self.example,
                "prefs": self.prefs, "prefs_default": DEFAULT_PREFS,
            }

    # ------------------------------------------------------------------ save and open
    def to_json(self) -> dict:
        with self.lock:
            path_of = {s.id: s.path for s in self.samples}
            return {"app": "flow-bench", "version": __version__,
                    "files": [{"path": s.path, "role": s.role, "stain": s.stain, "group": s.group, "include": s.include}
                              for s in self.samples],
                    "current": path_of.get(self.current), "transforms": self.transforms, "comp": self.comp,
                    "gates": self.gates,
                    "overrides": {path_of[k]: v for k, v in self.overrides.items() if k in path_of},
                    "objects": self.objects, "statdefs": self.statdefs, "prism_path": self.prism_path,
                    "group_order": self.group_order,
                    "example": self.example, "counters": self._n}

    def save(self, path: str) -> None:
        with self.lock:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.to_json(), fh, indent=1)
            os.replace(tmp, path)
            self.project_path = path
            self.dirty = False

    def open(self, path: str) -> dict:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if d.get("app") != "flow-bench":
            raise ValueError("That is not a Flow Bench project file.")
        with self.lock:
            self.reset()
            missing = [f["path"] for f in d.get("files", []) if not os.path.exists(f["path"])]
            self.add_files([f["path"] for f in d.get("files", []) if os.path.exists(f["path"])])
            by_path = {s.path: s for s in self.samples}
            for f in d.get("files", []):
                s = by_path.get(os.path.abspath(f["path"]))
                if s:
                    s.role, s.stain, s.group, s.include = f.get("role", "sample"), f.get("stain", ""), f.get("group", ""), f.get("include", True)
            for k, v in (d.get("transforms") or {}).items():
                if k in self.transforms:
                    self.transforms[k] = v
            self.comp = d.get("comp") or self.comp
            self.gates = d.get("gates") or []
            self.overrides = {by_path[os.path.abspath(k)].id: v for k, v in (d.get("overrides") or {}).items()
                              if os.path.abspath(k) in by_path}
            self.objects = d.get("objects") or self.objects
            self.statdefs = d.get("statdefs") or []
            self.prism_path = d.get("prism_path")
            self.group_order = d.get("group_order") or []
            self.example = d.get("example")
            for k, v in (d.get("counters") or {}).items():
                self._n[k] = max(self._n.get(k, 0), int(v))
            cur = by_path.get(os.path.abspath(d["current"])) if d.get("current") else None
            if cur:
                self.current = cur.id
            self.comp_ver += 1
            self.gate_ver += 1
            self.tf_ver += 1
            self.project_path = path
            self.dirty = False
            return {"missing": missing}


PROJECT = Project()
