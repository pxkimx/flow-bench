"""Axis scales. Every scale maps data values to a display coordinate in 0..1 and back.

The definitions are the Gating-ML 2.0 ones (flin, flog, fasinh, logicle); logicle is computed by
FlowUtils' C implementation (Moore & Parks). Display coordinates are what the page draws and what
polygon gates are tested in.
"""
from __future__ import annotations

import math

import numpy as np
from flowutils import transforms as FT

LN10 = math.log(10.0)
# Proposed defaults, shown in the app for the person to confirm or change:
LOGICLE = {"M": 4.5, "W": 0.5, "A": 0.0}


def default_for(kind: str, rng: float) -> dict:
    t = float(rng) if rng and rng > 0 else 262144.0
    if kind == "fluor":
        return {"type": "logicle", "T": t, **LOGICLE}
    return {"type": "linear", "T": t, "A": 0.0}


def _col(x):
    return np.ascontiguousarray(np.asarray(x, dtype=np.float64).reshape(-1, 1))


def fwd(s: dict, x) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    shape = x.shape
    k = s["type"]
    if k == "linear":
        a = s.get("A", 0.0)
        out = (x + a) / (s["T"] + a)
    elif k == "log":
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.log10(np.maximum(x, 1e-300) / s["T"]) / s["M"] + 1.0
        out = np.where(x > 0, out, -1.0)
    elif k == "asinh":
        m, a, t = s["M"], s.get("A", 0.0), s["T"]
        out = (np.arcsinh(x * math.sinh(m * LN10) / t) + a * LN10) / (LN10 * (m + a))
    elif k == "logicle":
        out = FT.logicle(_col(x), [0], t=s["T"], m=s["M"], w=s["W"], a=s.get("A", 0.0))[:, 0]
    else:
        raise ValueError(f"Unknown scale: {k}")
    return np.asarray(out, dtype=np.float64).reshape(shape)


def inv(s: dict, d) -> np.ndarray:
    d = np.asarray(d, dtype=np.float64)
    shape = d.shape
    k = s["type"]
    if k == "linear":
        a = s.get("A", 0.0)
        out = d * (s["T"] + a) - a
    elif k == "log":
        out = s["T"] * np.power(10.0, (d - 1.0) * s["M"])
    elif k == "asinh":
        m, a, t = s["M"], s.get("A", 0.0), s["T"]
        out = np.sinh(d * LN10 * (m + a) - a * LN10) * t / math.sinh(m * LN10)
    elif k == "logicle":
        out = FT.logicle_inverse(_col(d), [0], t=s["T"], m=s["M"], w=s["W"], a=s.get("A", 0.0))[:, 0]
    else:
        raise ValueError(f"Unknown scale: {k}")
    return np.asarray(out, dtype=np.float64).reshape(shape)


_SUP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def _nice(step: float) -> float:
    e = 10 ** math.floor(math.log10(step))
    for m in (1, 2, 2.5, 5, 10):
        if step <= m * e:
            return m * e
    return 10 * e


def _short(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:g}M"
    if a >= 1e3:
        return f"{v / 1e3:g}K"
    return f"{v:g}"


def ticks(s: dict) -> list:
    """[[display position, label or ''], ...] within 0..1."""
    out = []
    if s["type"] == "linear":
        step = _nice(s["T"] / 5.0)
        v = 0.0
        while v <= s["T"] * 1.0001:
            out.append([float(fwd(s, v)), _short(v)])
            if v + step / 2 <= s["T"]:
                out.append([float(fwd(s, v + step / 2)), ""])
            v += step
        return out
    top = int(math.floor(math.log10(s["T"])))
    if s["type"] != "log":
        out.append([float(fwd(s, 0.0)), "0"])
    lo = 2 if s["type"] != "log" else max(0, top - int(math.ceil(s["M"])))
    for k in range(lo, top + 1):
        d = float(fwd(s, 10.0 ** k))
        if 0 <= d <= 1:
            out.append([d, ("10" + str(k).translate(_SUP)) if k >= 3 or s["type"] == "log" else ""])
        for j in range(2, 10):
            d = float(fwd(s, j * 10.0 ** k))
            if 0 <= d <= 1:
                out.append([d, ""])
    if s["type"] != "log":
        for k in range(2, top + 1):
            d = float(fwd(s, -(10.0 ** k)))
            if 0.0 <= d <= 1:
                out.append([d, ("-10" + str(k).translate(_SUP)) if k >= 3 else ""])
    return out
