"""Local web server for Flow Bench. Standard-library HTTP on 127.0.0.1; nothing leaves the computer
except, when asked, the download of a published example from Zenodo."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import numpy as np

from . import __version__
from . import analysis as A
from . import examples as E
from . import export as X
from . import transforms as TF
from .lifecycle import Lifecycle
from .project import HOME, PROJECT, _b64, save_prefs, DEFAULT_PREFS

WEB = Path(__file__).resolve().parent / "web"
DEFAULT_PORT = 8800
UPLOADS = HOME / "uploads"
RUN = time.strftime("%Y%m%d-%H%M%S")
MAX_UPLOAD = 4 * 1024 ** 3
MAX_JSON = 8 * 1024 ** 2
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
LIFE: Lifecycle | None = None


def _clean(obj):
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return _clean(obj.tolist())
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def dialog(kind: str, prompt: str, name: str = "", types: tuple = ()):
    """A native macOS file dialog. Returns a path, a list of paths (kind 'files'), or None if cancelled."""
    if os.environ.get("FB_NO_DIALOG"):
        raise ValueError("File dialogs are switched off in this run.")
    q = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'  # noqa: E731
    ty = (" of type {" + ", ".join(q(t) for t in types) + "}") if types else ""
    if kind == "folder":
        script = f"POSIX path of (choose folder with prompt {q(prompt)})"
    elif kind == "files":
        script = (f"set fs to choose file with prompt {q(prompt)}{ty} with multiple selections allowed\n"
                  'set out to ""\nrepeat with f in fs\nset out to out & POSIX path of f & linefeed\nend repeat\nout')
    elif kind == "file":
        script = f"POSIX path of (choose file with prompt {q(prompt)}{ty})"
    elif kind == "save":
        script = f"POSIX path of (choose file name with prompt {q(prompt)} default name {q(name)})"
    else:
        raise ValueError(kind)
    r = subprocess.run(["osascript", "-e", "activate", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    out = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if not out:
        return None
    return out if kind == "files" else out[0].strip()


def fcs_in(folder: str) -> list:
    found = []
    for root, _dirs, files in os.walk(folder):
        found += [os.path.join(root, f) for f in files if f.lower().endswith(".fcs") and not f.startswith(".")]
        if len(found) > 2000:
            break
    return found


def comp_pair(p, sid: str, x: str, y: str) -> dict:
    """The same events before and after compensation, for one pair of channels."""
    with p.lock:
        s = p.sample(sid)
        if x not in s.ix or y not in s.ix:
            raise ValueError("Those channels are not in this file.")
        out = {"x": x, "y": y, "xl": p.label(x), "yl": p.label(y), "name": s.name,
               "xt": TF.ticks(p.transforms[x]), "yt": TF.ticks(p.transforms[y])}
        for key, comp in (("raw", False), ("comp", True)):
            a = p.data(sid, comp)
            step = max(1, int(np.ceil(a.shape[0] / 20000)))
            dx = np.clip(TF.fwd(p.transforms[x], a[::step, s.ix[x]].astype(np.float64)), 0, 1)
            dy = np.clip(TF.fwd(p.transforms[y], a[::step, s.ix[y]].astype(np.float64)), 0, 1)
            out[key] = {"px": _b64((dx * 65535).astype("<u2")), "py": _b64((dy * 65535).astype("<u2")), "n": int(dx.size)}
        return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FlowBench"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(_clean(obj), allow_nan=False).encode())

    def _guard(self, fn):
        try:
            self._json(fn())
        except (ValueError, FileNotFoundError, RuntimeError, KeyError) as e:
            self._json({"error": str(e).strip("'")}, 400)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _download(self, fn, name: str, ctype: str):
        try:
            body = fn()
            if isinstance(body, str):
                body = body.encode("utf-8")
            self._send(200, body, ctype, {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
        except (ValueError, FileNotFoundError, RuntimeError, KeyError) as e:
            self._send(400, str(e).strip("'").encode(), "text/plain; charset=utf-8")
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self._send(500, f"{type(e).__name__}: {e}".encode(), "text/plain; charset=utf-8")

    def _local_only(self) -> bool:
        origin = self.headers.get("Origin") or ""
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("localhost", "127.0.0.1"):
            return False
        return not origin or re.match(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", origin) is not None

    def _body_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_JSON:
            raise ValueError("Request too large.")
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _upload(self, name: str) -> Path:
        name = Path(name).name
        if not name or name.startswith(".") or not name.lower().endswith(".fcs"):
            raise ValueError("Only .fcs files can be added.")
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("That file is too large.")
        folder = UPLOADS / RUN
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / name
        left = n
        with open(dest, "wb") as fh:
            while left > 0:
                chunk = self.rfile.read(min(left, 4 * 1024 * 1024))
                if not chunk:
                    break
                fh.write(chunk)
                left -= len(chunk)
        if left:
            dest.unlink(missing_ok=True)
            raise ValueError("The upload was interrupted.")
        return dest

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._local_only():
            return self._send(403, b"forbidden", "text/plain")
        P = PROJECT
        if u.path == "/api/window":
            return self._window()
        if u.path == "/api/health":
            return self._json({"app": "flow-bench", "version": __version__})
        if u.path == "/api/state":
            return self._guard(lambda: dict(P.state(), examples=E.listing()))
        if u.path == "/api/render":
            def fn():
                have = dict(kv.split(":", 1) for kv in q.get("have", "").split(",") if ":" in kv)
                sid = q.get("sid") or P.current
                if not sid:
                    return {"objects": [], "tree": []}
                return P.render(sid, have)
            return self._guard(fn)
        if u.path == "/api/checks":
            return self._guard(lambda: {"checks": A.checks(P)})
        if u.path == "/api/table":
            return self._guard(P.table)
        if u.path == "/api/compare":
            return self._guard(lambda: A.compare(P, q.get("key", "")))
        if u.path == "/api/comp":
            return self._guard(lambda: A.comp_view(P))
        if u.path == "/api/comp/pair":
            return self._guard(lambda: comp_pair(P, q.get("sid") or P.current, q.get("x", ""), q.get("y", "")))
        if u.path == "/api/job":
            return self._guard(lambda: E.JOBS.get(q.get("id", ""), {"state": "none"}))
        if u.path == "/api/export/xlsx":
            return self._download(lambda: X.xlsx(P), f"Flow Bench statistics {X.stamp()}.xlsx",
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        if u.path == "/api/export/pdf":
            from . import report
            return self._download(lambda: report.pdf(P), f"Flow Bench report {X.stamp()}.pdf", "application/pdf")
        if u.path == "/api/export/gml":
            return self._download(lambda: X.gatingml(P), f"Flow Bench gates {X.stamp()}.xml", "application/xml")
        if u.path == "/api/export/pzfx":
            return self._download(lambda: X.new_pzfx(P), f"Flow Bench {X.stamp()}.pzfx", "application/xml")
        rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
        f = (WEB / rel).resolve()
        if WEB in f.parents and f.is_file():
            return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
        self._send(404, b"not found", "text/plain")

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._local_only():
            return self._send(403, b"forbidden", "text/plain")
        P = PROJECT
        if u.path == "/api/quit":
            self._json({"ok": True})
            if LIFE:
                LIFE.quit_now()
            return
        if u.path == "/api/upload":
            return self._guard(lambda: P.add_files([str(self._upload(q.get("name", "")))]))
        try:
            b = self._body_json()
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        op = b.get("op", "")

        def files():
            if op == "folder":
                d = dialog("folder", "Choose a folder of FCS files")
                if not d:
                    return {"cancelled": True}
                found = fcs_in(d)
                if not found:
                    raise ValueError("There are no .fcs files in that folder.")
                return P.add_files(found)
            if op == "files":
                fs = dialog("files", "Choose FCS files", types=("fcs", "FCS"))
                return P.add_files(fs) if fs else {"cancelled": True}
            if op == "paths":
                paths = []
                for x in b.get("paths", []):
                    paths += fcs_in(x) if os.path.isdir(x) else [x]
                return P.add_files(paths)
            raise ValueError("Unknown request.")

        def example():
            key = b.get("key", "")
            if op == "download":
                return {"job": E.download(key)}
            E.load(P, key)
            return {"ok": True}

        def sample():
            if op == "remove":
                P.remove_sample(b["id"])
            elif op == "current":
                P.sample(b["id"])
                P.current = b["id"]
            elif op == "guess":
                with P.lock:
                    for s in P.samples:
                        s.role, s.stain, s.group = "sample", "", ""
                    P._guess(P.samples)
            else:
                P.set_sample(b["id"], **{k: b[k] for k in ("role", "stain", "group", "include") if k in b})
            return {"ok": True}

        def gate():
            if op == "add":
                return {"ids": P.add_gate(b.get("parent"), b["type"], b["x"], b.get("y"), b["geom"], b.get("name", ""))}
            if op == "update":
                P.update_gate(b["id"], b["geom"], b.get("scope", "all"), b.get("sid") or P.current)
            elif op == "reset":
                P.reset_gate(b["id"], b.get("sid") or P.current)
            elif op == "rename":
                P.rename_gate(b["id"], b.get("name", ""))
            elif op == "delete":
                P.delete_gate(b["id"])
            else:
                raise ValueError("Unknown request.")
            return {"ok": True}

        def obj():
            if op == "add":
                return {"id": P.add_object(b["type"], b.get("pop"), b.get("x"), b.get("y"), b.get("stat"))}
            if op == "update":
                P.update_object(b["id"], **{k: b[k] for k in ("x", "y", "pop", "stat") if k in b})
            elif op == "delete":
                P.delete_object(b["id"])
            elif op == "move":
                P.move_object(b["id"], int(b.get("delta", 0)))
            else:
                raise ValueError("Unknown request.")
            return {"ok": True}

        def comp():
            if op == "mode":
                P.set_comp(mode=b["mode"])
            elif op == "compute":
                r = A.comp_from_controls(P)
                P.set_comp(names=r["names"], matrix=r["matrix"])
                P.set_comp(mode="matrix")
                return r
            elif op == "copy":
                s = P.sample(P.current)
                if not s.spill:
                    raise ValueError("The current file has no stored matrix to copy.")
                P.set_comp(names=list(s.spill[0]), matrix=s.spill[1].tolist())
                P.set_comp(mode="matrix")
            elif op == "cell":
                with P.lock:
                    m = [list(r) for r in P.comp["matrix"]]
                    i, j = int(b["i"]), int(b["j"])
                    if i == j:
                        raise ValueError("The diagonal is always 100 %.")
                    m[i][j] = float(b["pct"]) / 100.0
                    P.set_comp(names=P.comp["names"], matrix=m)
            else:
                raise ValueError("Unknown request.")
            return {"ok": True}

        def stat():
            if op == "add":
                P.add_stat(b.get("pop"), b["stat"], b.get("channel"))
            elif op == "remove":
                P.remove_stat(b["key"])
            else:
                raise ValueError("Unknown request.")
            return {"ok": True}

        def prefs():
            with P.lock:
                if op == "reset":
                    P.prefs = dict(DEFAULT_PREFS)
                else:
                    for k, v in (b.get("values") or {}).items():
                        if k in DEFAULT_PREFS:
                            v = float(v)
                            if not (v >= 0) or v > 1e9:
                                raise ValueError("Limits must be zero or more.")
                            P.prefs[k] = v
                save_prefs(P.prefs)
            return {"ok": True}

        def prism():
            if op == "new":
                path = dialog("save", "Save the Prism project as", name="Flow Bench statistics.pzfx")
                if not path:
                    return {"cancelled": True}
                if not path.lower().endswith(".pzfx"):
                    path += ".pzfx"
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(X.new_pzfx(P))
                P.prism_path = path
                P.dirty = True
                return {"path": path, "written": len(X.prism_tables(P))}
            if op == "link":
                path = dialog("file", "Choose the Prism file to keep up to date", types=("pzfx",))
                if not path:
                    return {"cancelled": True}
                r = X.update_pzfx(P, path)
                P.prism_path = path
                P.dirty = True
                return dict(r, path=path)
            if op == "update":
                if not P.prism_path or not os.path.exists(P.prism_path):
                    raise ValueError("The linked Prism file is not where it was. Link it again.")
                return dict(X.update_pzfx(P, P.prism_path), path=P.prism_path)
            if op == "unlink":
                P.prism_path = None
                return {"ok": True}
            if op == "open":
                if not P.prism_path or not os.path.exists(P.prism_path):
                    raise ValueError("The linked Prism file is not where it was.")
                subprocess.Popen(["open", P.prism_path])
                return {"ok": True}
            raise ValueError("Unknown request.")

        def project():
            if op == "new":
                P.reset()
                return {"ok": True}
            if op == "save":
                path = P.project_path if (P.project_path and not b.get("as")) else dialog("save", "Save the Flow Bench project as", name="Analysis.flowbench")
                if not path:
                    return {"cancelled": True}
                if not path.lower().endswith(".flowbench"):
                    path += ".flowbench"
                P.save(path)
                return {"path": path}
            if op == "open":
                path = dialog("file", "Open a Flow Bench project", types=("flowbench",))
                if not path:
                    return {"cancelled": True}
                return P.open(path)
            raise ValueError("Unknown request.")

        def transform():
            P.set_transform(b["channel"], b.get("scale") or {})
            return {"ok": True}

        routes = {"/api/files": files, "/api/example": example, "/api/sample": sample, "/api/gate": gate, "/api/object": obj,
                  "/api/comp": comp, "/api/stat": stat, "/api/prefs": prefs, "/api/prism": prism, "/api/project": project,
                  "/api/transform": transform}
        fn = routes.get(u.path)
        if not fn:
            return self._send(404, b"not found", "text/plain")
        self._guard(fn)

    def _window(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if LIFE:
            LIFE.window_opened()
        try:
            while True:
                self.wfile.write(b": hold\n\n")
                self.wfile.flush()
                threading.Event().wait(2.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if LIFE:
                LIFE.window_closed()
        self.close_connection = True


def remove_stale_uploads(days: int = 7) -> None:
    """Dropped files are copied into Application Support; copies from old runs are removed."""
    if not UPLOADS.exists():
        return
    cut = time.time() - days * 86400
    for d in UPLOADS.iterdir():
        try:
            if d.is_dir() and d.name != RUN and d.stat().st_mtime < cut:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def serve(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    global LIFE
    remove_stale_uploads()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True

    def quit_():
        sys.stdout.flush()
        os._exit(0)

    LIFE = Lifecycle(on_quit=quit_)
    LIFE.watch()
    url = f"http://localhost:{port}"
    print(f"Flow Bench {__version__} at {url}", flush=True)
    if open_browser:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        quit_()


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(prog="flow-bench", description="Flow Bench local app")
    ap.add_argument("--port", type=int, default=int(os.environ.get("FB_PORT", DEFAULT_PORT)))
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    serve(a.port, not a.no_browser)


if __name__ == "__main__":
    main()
