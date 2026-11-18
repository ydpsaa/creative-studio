"""Макет сайта-кабинета по контракту SITE-SYNC-TASK.md — для проверки агента синхронизации без настоящего сайта.
  STUDIO_MOCK_TOKEN=... uvicorn tests.mock_site:app --port 8799"""
import hashlib, hmac, json, os, secrets, tempfile, time
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse

TOKEN_HASH = hashlib.sha256(os.environ["STUDIO_MOCK_TOKEN"].encode()).hexdigest()
FILES = Path(tempfile.mkdtemp(prefix="mock-r2-"))
S = {"snapshots": [], "heartbeat": None, "tasks": {}}
app = FastAPI()
ORDER = ["new", "claimed", "processing", "analyzing", "done"]

@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path.startswith("/api/studio/"):
        h = request.headers.get("authorization", "")
        got = hashlib.sha256(h.removeprefix("Bearer ").encode()).hexdigest()
        if not h.startswith("Bearer ") or not hmac.compare_digest(got, TOKEN_HASH):
            from fastapi.responses import JSONResponse
            return JSONResponse({"ok": False}, status_code=401)
    return await call_next(request)

@app.put("/api/studio/snapshot")
async def snapshot(request: Request):
    b = await request.json()
    errs = [f"нет {k}" for k in ("schema", "snapshot_id", "methods", "sources") if k not in b]
    if b.get("schema") not in ("dolishx-site-data/1", "dolishx-site-data/2"):
        errs.append("schema")
    if errs:
        raise HTTPException(422, {"ok": False, "errors": errs})
    if not any(s["snapshot_id"] == b["snapshot_id"] for s in S["snapshots"]):
        S["snapshots"] = (S["snapshots"] + [b])[-10:]
    return {"ok": True, "snapshot_id": b["snapshot_id"], "stored_at": time.strftime("%FT%T")}

@app.get("/api/studio/snapshot/meta")
def meta():
    s = S["snapshots"][-1] if S["snapshots"] else {}
    return {"snapshot_id": s.get("snapshot_id"), "exported_at": s.get("exported_at")}

@app.post("/api/studio/heartbeat")
async def hb(request: Request):
    S["heartbeat"] = {**await request.json(), "at": time.time()}
    return {"ok": True}

@app.get("/api/studio/tasks")
def tasks(status: str = "new"):
    return {"tasks": [t for t in S["tasks"].values() if t["status"] == status][:20]}

@app.post("/api/studio/tasks/{tid}/claim")
def claim(tid: str):
    t = S["tasks"].get(tid) or (_ for _ in ()).throw(HTTPException(404))
    if t["status"] != "new":
        raise HTTPException(409)
    t["status"] = "claimed"
    return {"ok": True}

@app.get("/api/studio/tasks/{tid}/file")
def file(tid: str):
    t = S["tasks"][tid]
    if t["status"] not in ("claimed", "processing") or not t.get("file_key"):
        raise HTTPException(409)
    return FileResponse(FILES / t["file_key"])

@app.post("/api/studio/tasks/{tid}/progress")
async def progress(tid: str, request: Request):
    b = await request.json(); t = S["tasks"][tid]
    if b["status"] != "failed" and ORDER.index(b["status"]) < ORDER.index(t["status"]):
        raise HTTPException(409, "назад нельзя")
    t.update({k: b[k] for k in ("status", "progress", "message", "result", "error") if k in b})
    t["log"].append({"t": time.strftime("%T"), "message": b.get("message")})
    if t["status"] in ("done", "failed") and t.get("file_key"):
        (FILES / t["file_key"]).unlink(missing_ok=True); t["file_deleted"] = True
    return {"ok": True}

# ---- только для теста: то, что владелец делает в интерфейсе
@app.post("/test/learn")
async def learn(file: UploadFile = File(...), goals: str = Form("full"), ai: str = Form("claude"), note: str = Form("")):
    tid = "task_" + secrets.token_hex(4); key = tid + "_" + file.filename
    (FILES / key).write_bytes(await file.read())
    S["tasks"][tid] = {"id": tid, "type": "learn", "status": "new", "file_key": key, "filename": file.filename, "goals": goals.split(","),
                       "ai": ai, "note": note, "usage_status": "только референс", "log": []}
    return S["tasks"][tid]

@app.post("/test/analyze")
async def analyze(source_id: str = Form(...), goals: str = Form("full"), ai: str = Form("claude")):
    tid = "task_" + secrets.token_hex(4)
    S["tasks"][tid] = {"id": tid, "type": "analyze", "status": "new", "source_id": source_id, "goals": goals.split(","), "ai": ai, "log": []}
    return S["tasks"][tid]

@app.get("/test/state")
def state():
    return {"heartbeat": S["heartbeat"], "snapshots": [(s["snapshot_id"], s["schema"], len(s["methods"]), len(s["sources"])) for s in S["snapshots"]],
            "tasks": S["tasks"]}
