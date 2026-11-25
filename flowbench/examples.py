"""Published example data, downloaded on request from Zenodo (never bundled) and checked against the
checksums below. Both datasets are CC BY 4.0; the citation is shown wherever an example is used."""
from __future__ import annotations

import hashlib
import os
import re
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .project import HOME, Project

DIR = HOME / "examples"

EXAMPLES = {
    "fuchi": {
        "title": "Cell-cycle reporter (FuChi)",
        "blurb": "Fucci cell-cycle reporters in chicken cells: three culture conditions in three germ-cell lines, plus cell-line controls. No compensation controls.",
        "citation": "Sudderick et al., PLoS Biology 2026", "doi": "10.1371/journal.pbio.3004036",
        "record": "21995904", "folder": "fuchi-plos-biology-2026", "licence": "CC BY 4.0", "size_mb": 16,
    },
    "caerin": {
        "title": "Tumour immune panel with compensation controls",
        "blurb": "Eight colours on a FACSAria II: seven single-stain controls and an unstained control, and three treatment groups of four mice (Figure 6A of the paper).",
        "citation": "Li et al., Scientific Reports 2025", "doi": "10.1038/s41598-025-87687-0",
        "record": "13977309", "folder": "caerin-sci-rep-2025", "licence": "CC BY 4.0", "size_mb": 163,
    },
}
JOBS: dict = {}


def _manifest(key: str) -> dict:
    """name -> md5. The full list was recorded when the examples were chosen (see .manifest.tsv next to the files);
    if that file is not there yet it is fetched from Zenodo's record."""
    ex = EXAMPLES[key]
    f = DIR / ex["folder"] / ".manifest.tsv"
    out = {}
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            p = line.split("\t")
            if len(p) >= 4:
                out[p[0]] = p[3].replace("md5:", "")
        return out
    import json
    with urllib.request.urlopen(f"https://zenodo.org/api/records/{ex['record']}", timeout=90) as r:
        rec = json.load(r)
    lines = []
    for fl in rec["files"]:
        if fl["key"].lower().endswith((".fcs", ".xlsx")):
            out[fl["key"]] = fl.get("checksum", "").replace("md5:", "")
            lines.append(f"{fl['key']}\t{fl['links']['self']}\t{fl['size']}\t{fl.get('checksum', '')}")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def installed(key: str) -> bool:
    ex = EXAMPLES[key]
    d = DIR / ex["folder"]
    f = d / ".manifest.tsv"
    if not f.exists():
        return False
    names = [ln.split("\t")[0] for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return bool(names) and all((d / n).exists() for n in names)


def listing() -> list:
    return [{"key": k, "title": e["title"], "blurb": e["blurb"], "citation": e["citation"], "doi": e["doi"],
             "record": e["record"], "licence": e["licence"], "size_mb": e["size_mb"], "installed": installed(k)}
            for k, e in EXAMPLES.items()]


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(key: str) -> str:
    """Start (or rejoin) a download. Returns a job id for /api/job."""
    if key not in EXAMPLES:
        raise ValueError("Unknown example.")
    job = JOBS.get(key)
    if job and job["state"] == "running":
        return key
    job = JOBS[key] = {"state": "running", "done": 0, "total": 0, "message": "Asking Zenodo for the file list…", "error": ""}
    ex = EXAMPLES[key]
    d = DIR / ex["folder"]
    d.mkdir(parents=True, exist_ok=True)

    def one(name: str, md5: str):
        dest = d / name
        if dest.exists() and (not md5 or _md5(dest) == md5):
            return
        url = f"https://zenodo.org/records/{ex['record']}/files/{urllib.parse.quote(name)}?download=1"
        tmp = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as fh:
                    while True:
                        chunk = r.read(1 << 18)
                        if not chunk:
                            break
                        fh.write(chunk)
                if md5 and _md5(tmp) != md5:
                    raise OSError("checksum does not match Zenodo's")
                tmp.replace(dest)
                return
            except OSError:
                if attempt == 3:
                    raise

    def run():
        try:
            man = _manifest(key)
            job["total"] = len(man)
            job["message"] = f"Downloading {len(man)} files from Zenodo…"
            with ThreadPoolExecutor(max_workers=8) as pool:
                futs = [pool.submit(one, n, m) for n, m in man.items()]
                for f in futs:
                    f.result()
                    job["done"] += 1
            job["state"] = "done"
            job["message"] = "Downloaded and checked."
        except Exception as e:  # noqa: BLE001
            job["state"] = "error"
            job["error"] = f"The download did not finish ({e}). Check the internet connection and try again."

    threading.Thread(target=run, daemon=True, name=f"example-{key}").start()
    return key


def _poly(p: Project, parent, x, y, pts, name):
    return p.add_gate(parent, "poly", x, y, {"pts": pts}, name)[0]


def load(p: Project, key: str) -> None:
    """Open an example as a new project, with the groups its authors recorded and a starting layout."""
    if not installed(key):
        raise ValueError("That example has not been downloaded yet.")
    ex = EXAMPLES[key]
    d = DIR / ex["folder"]
    with p.lock:
        p.reset()
        p.add_files([str(f) for f in d.glob("*.fcs")])
        by_name = {s.name: s for s in p.samples}
        note = ""
        if key == "fuchi":
            sheet = d / "Sample information.xlsx"
            if sheet.exists():
                from openpyxl import load_workbook
                ws = load_workbook(sheet, read_only=True)["Samples"]
                for name, cell, cond, _fig in list(ws.iter_rows(values_only=True))[1:]:
                    s = by_name.get(str(name))
                    if not s:
                        continue
                    if "non transgenic" in str(cell).lower():
                        s.role, s.group = "unstained", ""
                    else:
                        s.role, s.group = "sample", re.sub(r" Line \d+$", "", str(cell)) + " · " + str(cond)
            p.group_order = [f"{cell} · {cond}" for cell in ("FuChi PGCs", "Fucci(CA)2-DF1", "Fucci(SA)2-DF1")
                             for cond in ("Control", "No Growth Factors", "Nocodazole")]
            cur = by_name.get("Specimen_001_2 FAOT.fcs")
            if cur:
                p.current = cur.id
            # A starting point drawn by Flow Bench on these files. They are not the authors' gates.
            p.objects = []
            cells = _poly(p, None, "FSC-A", "SSC-A", [[.10, .08], [.55, .12], [.95, .35], [.95, .62], [.50, .45], [.10, .30]], "Cells")
            sing = _poly(p, cells, "FSC-A", "FSC-H", [[.05, .02], [.30, .16], [.60, .26], [.97, .36], [.97, .52], [.60, .42], [.30, .30], [.05, .10]], "Singlets")
            hi = _poly(p, sing, "YG 610/20-A", "B 530/30-A", [[.30, .60], [.55, .60], [.80, .72], [.98, .80], [.98, .99], [.30, .99]], "B530 high")
            _poly(p, sing, "YG 610/20-A", "B 530/30-A", [[.52, .30], [.80, .36], [.80, .56], [.62, .56], [.52, .44]], "YG610 high")
            # the blue reporter is read on a linear scale here: its two peaks sit a factor of two apart
            p.set_transform("V 450/50-A", {"type": "linear"})
            p.add_object("density", None, "FSC-A", "SSC-A")
            p.add_object("density", cells, "FSC-A", "FSC-H")
            p.add_object("density", sing, "YG 610/20-A", "B 530/30-A")
            p.add_object("hist", sing, "V 450/50-A")
            p.add_object("stats")
            key_hi = next(q["key"] for q in p.stat_list() if q["pop"] == hi)
            p.add_object("chart", stat={"key": key_hi})
            note = "The four gates are a starting point drawn by Flow Bench, not the authors' gates. Move them as you see fit."
        elif key == "caerin":
            cur = by_name.get("Specimen_001_PBS_1_010.fcs")
            if cur:
                p.current = cur.id
            note = ("Roles and groups were guessed from the file names: check them on the Data tab. Three tubes named "
                    "“… Control” are left without a group because their purpose is not recorded in the files.")
        p.example = {"key": key, "title": ex["title"], "citation": ex["citation"], "doi": ex["doi"],
                     "record": ex["record"], "licence": ex["licence"], "note": note}
        p.dirty = False
