"""Read FCS files (FlowIO) into plain numpy arrays, with the keywords Flow Bench uses."""
from __future__ import annotations

import os
import re

import numpy as np
from flowio import FlowData


def _kind(name: str, idx: int, fd) -> str:
    low = name.lower()
    if low == "time" or (fd.time_index is not None and idx == fd.time_index):
        return "time"
    if idx in (fd.scatter_indices or []) or re.match(r"^(fsc|ssc)", low):
        return "scatter"
    return "fluor"


def parse_spill(text: dict):
    """$SPILLOVER / $SPILL / $COMP -> (names, matrix) or None. Rows are fluorochromes, columns detectors."""
    raw = text.get("spillover") or text.get("spill") or text.get("comp")
    if not raw:
        return None
    parts = [p.strip() for p in str(raw).split(",")]
    try:
        n = int(parts[0])
        names = parts[1:1 + n]
        vals = np.array([float(v) for v in parts[1 + n:1 + n + n * n]], dtype=float)
        if len(names) != n or vals.size != n * n:
            return None
        return names, vals.reshape(n, n)
    except (ValueError, IndexError):
        return None


class Sample:
    """One FCS file. Events are loaded on demand by the project (see Project.raw)."""

    def __init__(self, sid: str, path: str):
        fd = FlowData(path, only_text=True)
        t = fd.text
        self.id = sid
        self.path = path
        self.name = os.path.basename(path)
        self.n = int(str(t.get("tot", "0")).strip() or 0)
        self.text = {k: str(v) for k, v in t.items()}
        pnn = list(fd.pnn_labels)
        pns = list(fd.pns_labels) if fd.pns_labels else [""] * len(pnn)
        self.channels = []
        for i, name in enumerate(pnn):
            try:
                rng = float(t.get(f"p{i + 1}r", 262144))
            except ValueError:
                rng = 262144.0
            self.channels.append({"name": name, "marker": (pns[i] or "").strip(), "range": rng,
                                  "kind": _kind(name, i, fd), "voltage": t.get(f"p{i + 1}v")})
        self.ix = {c["name"]: i for i, c in enumerate(self.channels)}
        self.spill = parse_spill(t)
        self.cyt = t.get("cyt", "")
        self.date = t.get("date", "")
        # set by the person (or guessed, then shown for them to confirm)
        self.role = "sample"       # sample | unstained | single | fmo | isotype
        self.stain = ""            # channel name for single-stain and FMO controls
        self.group = ""
        self.include = True

    def load(self) -> np.ndarray:
        fd = FlowData(self.path)
        a = np.asarray(fd.as_array(), dtype=np.float32)
        self.n = a.shape[0]
        return a

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "n": self.n, "role": self.role, "stain": self.stain,
                "group": self.group, "include": self.include, "cyt": self.cyt, "date": self.date,
                "has_spill": self.spill is not None, "channels": [c["name"] for c in self.channels]}
