"""Gate membership. Polygons are tested in display coordinates (the scale of each axis); rectangles,
ranges and quadrants are tested in data units, which is the same thing for a scale that only rises.

Boundaries follow Gating-ML 2.0: a rectangle includes its lower bound and excludes its upper bound;
a point on a polygon's edge is inside.
"""
from __future__ import annotations

import numpy as np

from . import transforms as TF


def pip(x: np.ndarray, y: np.ndarray, pts) -> np.ndarray:
    """Even-odd point in polygon, edges included."""
    pts = np.asarray(pts, dtype=np.float64)
    inside = np.zeros(x.shape, dtype=bool)
    edge = np.zeros(x.shape, dtype=bool)
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if yi != yj:
            cross = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
            inside ^= cross
        # on the segment itself
        col = (xj - xi) * (y - yi) - (yj - yi) * (x - xi)
        tol = 1e-12 * max(1.0, abs(xj - xi) + abs(yj - yi))
        edge |= (np.abs(col) <= tol) & (x >= min(xi, xj)) & (x <= max(xi, xj)) & (y >= min(yi, yj)) & (y <= max(yi, yj))
        j = i
    return inside | edge


def member(gate: dict, geom: dict, X: np.ndarray, Y, tx: dict, ty) -> np.ndarray:
    """Boolean array: which events fall in this gate (ignoring its parent)."""
    k = gate["type"]
    if k == "poly":
        p = np.asarray(geom["pts"], dtype=np.float64)
        return pip(TF.fwd(tx, X), TF.fwd(ty, Y), np.c_[TF.fwd(tx, p[:, 0]), TF.fwd(ty, p[:, 1])])
    if k == "range":
        return (X >= geom["x0"]) & (X < geom["x1"])
    if k == "rect":
        return (X >= geom["x0"]) & (X < geom["x1"]) & (Y >= geom["y0"]) & (Y < geom["y1"])
    if k == "quad":
        q = gate["q"]
        mx = (X >= geom["cx"]) if q[1] == "R" else (X < geom["cx"])
        my = (Y >= geom["cy"]) if q[0] == "U" else (Y < geom["cy"])
        return mx & my
    raise ValueError(f"Unknown gate type: {k}")


def to_display(gate: dict, geom: dict, tx: dict, ty) -> dict:
    k = gate["type"]
    f = lambda t, v: float(TF.fwd(t, float(v)))  # noqa: E731
    if k == "poly":
        return {"pts": [[f(tx, a), f(ty, b)] for a, b in geom["pts"]]}
    if k == "range":
        return {"x0": f(tx, geom["x0"]), "x1": f(tx, geom["x1"])}
    if k == "rect":
        return {"x0": f(tx, geom["x0"]), "x1": f(tx, geom["x1"]), "y0": f(ty, geom["y0"]), "y1": f(ty, geom["y1"])}
    return {"cx": f(tx, geom["cx"]), "cy": f(ty, geom["cy"])}


def from_display(kind: str, g: dict, tx: dict, ty) -> dict:
    f = lambda t, v: float(TF.inv(t, float(v)))  # noqa: E731
    if kind == "poly":
        if len(g["pts"]) < 3:
            raise ValueError("A polygon gate needs at least three corners.")
        return {"pts": [[f(tx, a), f(ty, b)] for a, b in g["pts"]]}
    if kind == "range":
        a, b = sorted((f(tx, g["x0"]), f(tx, g["x1"])))
        return {"x0": a, "x1": b}
    if kind == "rect":
        a, b = sorted((f(tx, g["x0"]), f(tx, g["x1"])))
        c, d = sorted((f(ty, g["y0"]), f(ty, g["y1"])))
        return {"x0": a, "x1": b, "y0": c, "y1": d}
    if kind == "quad":
        return {"cx": f(tx, g["cx"]), "cy": f(ty, g["cy"])}
    raise ValueError(f"Unknown gate type: {kind}")
