"""Fast smoke test that runs anywhere (GitHub Actions on Linux, no MLX / Apple Vision / models needed).
The full acceptance suite (tests/acceptance.py) needs a Mac with the models; this one checks the parts every user hits first:
the API starts, the UI is served, STACKER renders, the Review board scans, `doctor` reports.

    python tests/ci_smoke.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cs-smoke-"))
os.environ["STUDIO_DATA_DIR"] = str(TMP / "data")
sys.path.insert(0, str(ROOT / "app" / "api"))

from fastapi.testclient import TestClient  # noqa: E402

from studio import db, doctor  # noqa: E402
from studio.main import app  # noqa: E402

failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'OK  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)
    if not ok:
        failed.append(name)


c = TestClient(app, headers={"host": "127.0.0.1:8765", "x-studio": "1"})
db.open_db()

check("API health", c.get("/api/health").json().get("ok") is True)
check("write without X-Studio refused", TestClient(app, headers={"host": "127.0.0.1:8765"}).post("/api/import/note", json={}).status_code == 403)
ui = c.get("/")
check("UI served", ui.status_code == 200 and "<div id=\"root\"" in ui.text, f"HTTP {ui.status_code}")

st = c.get("/api/stacker/stickers").json()
check("sticker catalog", len(st) == 162, f"{len(st)} stickers")
p = c.post("/api/stacker/projects", json={"title": "smoke"}).json()
pv = c.post(f"/api/stacker/projects/{p['id']}/preview", json={}).json()
check("stacker preview frames", len(pv.get("previews", [])) >= 5, f"{len(pv.get('previews', []))} frames, intro {pv.get('preview_info', {}).get('intro_end')} s")
c.post(f"/api/stacker/projects/{p['id']}/render")
state = {}
for _ in range(240):
    state = c.get(f"/api/stacker/projects/{p['id']}").json()["status"]
    if state.get("state") in ("done", "failed"):
        break
    time.sleep(.5)
check("stacker render", state.get("state") == "done", json.dumps({k: state.get(k) for k in ("state", "duration", "seconds", "error")}))

rv = c.get("/api/review/summary").json()
check("review board scans", "kinds" in rv, f"{sum(k['total'] for k in rv.get('kinds', []))} assets")

res = doctor.checks()
fails = [x["what"] for x in res if x["status"] == "fail"]
check("doctor: no blocking problems", not fails, "; ".join(fails) or f"{len(res)} checks")

shutil.rmtree(TMP, ignore_errors=True)
print("\nfailed:", failed if failed else "none")
sys.exit(1 if failed else 0)
