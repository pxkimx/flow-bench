"""The local server: only this Mac may talk to it, and it quits when its window closes."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(port, path, body=None, headers=None, raw=None, timeout=20):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=dict({"Host": f"localhost:{port}"}, **(headers or {})),
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture
def server(tmp_path):
    port = free_port()
    env = dict(os.environ, FB_NO_DIALOG="1", FB_PREFS=str(tmp_path / "prefs.json"), PYTHONUNBUFFERED="1")
    p = subprocess.Popen([sys.executable, "-m", "flowbench.server", "--port", str(port), "--no-browser"], cwd=ROOT, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            if call(port, "/api/health", timeout=1)[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    yield port, p
    if p.poll() is None:
        p.kill()
    p.wait(timeout=10)


def test_health_state_and_page(server):
    port, _ = server
    code, body = call(port, "/api/health")
    assert code == 200 and json.loads(body)["app"] == "flow-bench"
    st = json.loads(call(port, "/api/state")[1])
    assert st["samples"] == [] and {e["key"] for e in st["examples"]} == {"fuchi", "caerin"}
    code, page = call(port, "/")
    assert code == 200 and b"Flow Bench" in page and b"toolbox-appearance" in page
    assert call(port, "/styles.css")[0] == 200 and call(port, "/app.js")[0] == 200
    assert call(port, "/../server.py")[0] == 404


def test_other_sites_are_refused(server):
    port, p = server
    assert call(port, "/api/state", headers={"Host": "evil.example"})[0] == 403
    assert call(port, "/api/quit", body={}, headers={"Origin": "https://evil.example"})[0] == 403
    assert call(port, "/api/project", body={"op": "new"}, headers={"Origin": "http://evil.example:8800"})[0] == 403
    time.sleep(0.5)
    assert p.poll() is None
    assert call(port, "/api/project", body={"op": "new"}, headers={"Origin": f"http://localhost:{port}"})[0] == 200


def test_upload_gate_render_and_exports(server, tmp_path):
    from test_engine import write
    port, _ = server
    for name in ("A_1", "A_2", "B_1", "B_2"):
        f = tmp_path / f"{name}.fcs"
        write(f, seed=len(name) + ord(name[0]) + int(name[-1]))
        code, body = call(port, f"/api/upload?name={name}.fcs", raw=f.read_bytes())
        assert code == 200 and json.loads(body)["added"] == 1, body
    assert call(port, "/api/upload?name=notes.txt", raw=b"x")[0] == 400
    st = json.loads(call(port, "/api/state")[1])
    assert len(st["samples"]) == 4 and st["groups"] == ["A", "B"] and st["current"]
    code, body = call(port, "/api/gate", body={"op": "add", "parent": None, "type": "range", "x": "FL1-A", "y": None, "geom": {"x0": .6, "x1": 1.0}, "name": "FL1 high"})
    gid = json.loads(body)["ids"][0]
    r = json.loads(call(port, "/api/render")[1])
    assert r["tree"][1]["name"] == "FL1 high" and r["objects"][0]["px"]
    assert json.loads(call(port, "/api/gate", body={"op": "add", "parent": "nope", "type": "range", "x": "FL1-A", "geom": {"x0": 0, "x1": 1}})[1])["error"]
    assert json.loads(call(port, "/api/table")[1])["rows"][0]["values"][0] is not None
    key = json.loads(call(port, "/api/state")[1])["stats"][0]["key"]
    cmp = json.loads(call(port, "/api/compare?key=" + urllib.request.quote(key))[1])
    assert [g["n"] for g in cmp["groups"]] == [2, 2]
    assert json.loads(call(port, "/api/checks")[1])["checks"]
    assert json.loads(call(port, "/api/comp")[1])["mode"] == "file"
    code, pz = call(port, "/api/export/pzfx")
    assert code == 200 and b"<GraphPadPrismFile" in pz and b"FL1 high" in pz
    assert call(port, "/api/export/xlsx")[1][:2] == b"PK"
    assert b"Gating-ML" in call(port, "/api/export/gml")[1]
    assert call(port, "/api/export/pdf", timeout=120)[1][:5] == b"%PDF-"
    assert call(port, "/api/gate", body={"op": "delete", "id": gid})[0] == 200
    assert call(port, "/api/prism", body={"op": "new"})[0] == 400           # dialogs are off in tests: a clear refusal, not a hang


def test_quits_when_asked_and_leaves_nothing_listening(server):
    port, p = server
    assert call(port, "/api/quit", body={})[0] == 200
    p.wait(timeout=10)
    s = socket.socket()
    assert s.connect_ex(("127.0.0.1", port)) != 0
    s.close()


def test_quits_a_few_seconds_after_its_window_closes(server):
    port, p = server
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(f"GET /api/window HTTP/1.1\r\nHost: localhost:{port}\r\n\r\n".encode())
    time.sleep(1.5)
    assert p.poll() is None
    s.close()                                  # the window goes away
    time.sleep(3)
    assert p.poll() is None                    # a reload would have reconnected by now: still running
    p.wait(timeout=12)                         # ... and then it quits on its own
