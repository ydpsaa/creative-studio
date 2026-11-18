"""Durable job queue in SQLite. Job key = content hash + stage + stage version, so
re-importing identical content never re-runs (or re-pays for) a finished stage."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import traceback
from pathlib import Path

from . import config, db, processors

SOFT_DEPS = {"context_pack"}          # runs with whatever its inputs produced
TERMINAL = {"completed", "partial", "failed", "blocked", "cancelled"}
OK = {"completed", "partial"}
STALE_AFTER = 120                     # s without heartbeat -> worker presumed dead


def job_key(source: dict, stage: str) -> str:
    content = source.get("sha256") or source.get("url") or source["id"]
    return hashlib.sha256(f"{content}|{stage}|{config.STAGE_VERSIONS.get(stage, '1')}".encode()).hexdigest()[:32]


def enqueue_plan(con, source: dict) -> list[str]:
    """Create jobs for the source's processing plan (idempotent)."""
    plan = processors.PLANS.get(source["kind"], {})
    created, ids = [], {}
    for stage in plan:
        key = job_key(source, stage)
        ex = con.execute("SELECT id FROM jobs WHERE key=?", (key,)).fetchone()
        if ex:
            ids[stage] = ex["id"]
            continue
        jid = db.new_id("job")
        ids[stage] = jid
        con.execute("INSERT INTO jobs(id,key,source_id,stage,depends_on,state,created_at) VALUES (?,?,?,?,?,?,?)",
                    (jid, key, source["id"], stage, "[]", "queued", db.now()))
        created.append(jid)
    for stage, deps in plan.items():
        con.execute("UPDATE jobs SET depends_on=? WHERE id=? AND state!='running'",
                    (json.dumps([ids[d] for d in deps]), ids[stage]))
    _status_from_plan(con, source)
    return created


def _status_from_plan(con, source):
    k = source["kind"]
    upd = {}
    if k in ("video", "audio"):
        upd = {"st_speech": "queued", "st_audio": "queued", "st_analysis": "queued"}
        if k == "video":
            upd["st_visual"] = "queued"
    elif k == "image":
        upd = {"st_visual": "queued", "st_analysis": "queued"}
    elif k == "document":
        upd = {"st_analysis": "queued"}
    for col, v in upd.items():
        con.execute(f"UPDATE sources SET {col}=? WHERE id=? AND {col} IN ('n/a','pending')", (v, source["id"]))


def recover(con) -> int:
    """Requeue jobs whose worker died mid-run (stale heartbeat)."""
    cutoff = time.time() - STALE_AFTER
    cur = con.execute("UPDATE jobs SET state='queued', note='Возобновлено после остановки worker' "
                      "WHERE state='running' AND (heartbeat IS NULL OR heartbeat < ?)", (cutoff,))
    return cur.rowcount


def claim(con) -> dict | None:
    with db.tx(con):
        for j in con.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at, rowid"):
            deps = json.loads(j["depends_on"] or "[]")
            states = [con.execute("SELECT state FROM jobs WHERE id=?", (d,)).fetchone()["state"] for d in deps]
            if any(s not in TERMINAL for s in states):
                continue
            if j["stage"] not in SOFT_DEPS and any(s not in OK for s in states):
                con.execute("UPDATE jobs SET state='blocked', error=?, finished_at=? WHERE id=?",
                            ("Зависимый этап не выполнен: " + ", ".join(states), db.now(), j["id"]))
                continue
            if paid_limit_reached(con) and j["paid"]:
                continue
            run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{j['stage']}-{os.urandom(2).hex()}"
            con.execute("UPDATE jobs SET state='running', attempts=attempts+1, run_id=?, started_at=?, heartbeat=?, error=NULL "
                        "WHERE id=?", (run_id, db.now(), time.time(), j["id"]))
            d = dict(j)
            d["run_id"] = run_id
            return d
    return None


def paid_limit_reached(con) -> bool:
    return False   # no paid provider is connected in this build; hook kept for the AI adapter


def _inputs(con, source_id: str) -> dict[str, Path]:
    out = {}
    for j in con.execute("SELECT stage, run_id FROM jobs WHERE source_id=? AND state IN ('completed','partial')",
                         (source_id,)):
        p = config.DATA_DIR / "derived" / source_id / j["run_id"]
        if p.exists():
            out[j["stage"]] = p
    return out


def run_job(con, job: dict) -> str:
    src = db.row(con.execute("SELECT * FROM sources WHERE id=?", (job["source_id"],)).fetchone())
    base = config.DATA_DIR / "derived" / src["id"]
    tmp = base / f".tmp-{job['run_id']}"
    final = base / job["run_id"]
    tmp.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()

    def beat():
        c2 = db.connect()
        while not stop.wait(10):
            c2.execute("UPDATE jobs SET heartbeat=? WHERE id=?", (time.time(), job["id"]))
        c2.close()

    th = threading.Thread(target=beat, daemon=True)
    th.start()
    try:
        ctx = processors.Ctx(source=src, out=tmp, run_id=job["run_id"], inputs=_inputs(con, src["id"]))
        _set_running_status(con, src, job["stage"])
        res = processors.STAGES[job["stage"]](ctx)
    except Exception as e:  # noqa: BLE001
        res = processors.StageResult(state="failed", note=f"{type(e).__name__}: {e}")
        (tmp / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        stop.set()
    cur = con.execute("SELECT state, attempts, max_attempts FROM jobs WHERE id=?", (job["id"],)).fetchone()
    if cur["state"] == "cancelled":
        shutil.rmtree(tmp, ignore_errors=True)
        return "cancelled"
    if res.state == "failed" and cur["attempts"] < cur["max_attempts"] and job["stage"] != "page_fetch":
        shutil.rmtree(tmp, ignore_errors=True)
        con.execute("UPDATE jobs SET state='queued', error=? WHERE id=?", (res.note, job["id"]))
        _log(src["id"], job, "retry", res.note)
        return "retry"
    # atomic publish: rename temp dir, then commit DB in one transaction
    os.replace(tmp, final)
    with db.tx(con):
        for a in res.artifacts:
            con.execute("INSERT INTO artifacts(id,source_id,run_id,stage,kind,rel_path,t,meta,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (db.new_id("art"), src["id"], job["run_id"], job["stage"], a["kind"],
                         config.rel(final / a["file"]), a.get("t"), db.j(a.get("meta", {})), db.now()))
        upd = dict(res.source_updates)
        _final_status(src, job["stage"], res.state, upd)
        if upd:
            cols = ", ".join(f"{k}=?" for k in upd)
            con.execute(f"UPDATE sources SET {cols} WHERE id=?", (*upd.values(), src["id"]))
        for s in res.search:
            db.index(con, s["etype"], s["eid"], s["title"], s["body"], sid=src["id"], t=s.get("t"))
        con.execute("UPDATE jobs SET state=?, finished_at=?, note=?, error=? WHERE id=?",
                    (res.state, db.now(), res.note, res.note if res.state in ("failed", "blocked") else None, job["id"]))
    _log(src["id"], job, res.state, res.note)
    try:
        from . import coverage
        coverage.write(con, src["id"])
        from . import sources
        sources.write_manifest(con, src["id"])
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    return res.state


_STAGE_COL = {"sound_events": "st_audio", "transcribe": "st_speech", "frames": "st_visual", "ocr": "st_visual", "audio_features": "st_audio",
              "context_pack": "st_analysis", "image_probe": "st_visual", "page_fetch": "st_acquire"}


def _set_running_status(con, src, stage):
    col = _STAGE_COL.get(stage)
    if col:
        con.execute(f"UPDATE sources SET {col}='running' WHERE id=?", (src["id"],))


def _final_status(src, stage, state, upd):
    col = _STAGE_COL.get(stage)
    if col and state in ("failed", "blocked") and col not in upd:
        upd[col] = state
    if stage == "audio" and state == "failed":
        upd.setdefault("st_speech", "blocked")
        upd.setdefault("st_audio", "blocked")


def _log(sid, job, state, note):
    p = config.DATA_DIR / "logs" / "worker.log"
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{db.now()} {sid} {job['stage']} {job['id']} {state} {note}\n")


def cancel(con, job_id: str) -> bool:
    cur = con.execute("UPDATE jobs SET state='cancelled', finished_at=?, note='Отменено пользователем' "
                      "WHERE id=? AND state IN ('queued','running','blocked')", (db.now(), job_id))
    return cur.rowcount > 0


def retry(con, job_id: str, force: bool = False) -> bool:
    """Re-queue a job. force=True also re-runs a completed stage (explicit user request, e.g. after a
    processor fix). Previous outputs stay on disk until the new run publishes its own run dir."""
    j = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    allowed = ("failed", "blocked", "cancelled", "partial") + (("completed",) if force else ())
    if not j or j["state"] not in allowed:
        return False
    with db.tx(con):
        con.execute("UPDATE jobs SET state='queued', attempts=0, error=NULL, note='Повторный запуск' WHERE id=?", (job_id,))
        # downstream jobs (transitively) run again: blocked ones get a chance, soft-dependency
        # ones (context pack) are rebuilt with the new input. Finished outputs stay until replaced.
        todo, seen = [job_id], set()
        while todo:
            cur = todo.pop()
            for d in con.execute("SELECT id, depends_on, state, stage FROM jobs WHERE source_id=?", (j["source_id"],)):
                if d["id"] in seen or cur not in json.loads(d["depends_on"] or "[]"):
                    continue
                seen.add(d["id"])
                if d["state"] in ("blocked", "cancelled") or (d["stage"] in SOFT_DEPS and d["state"] in OK):
                    con.execute("UPDATE jobs SET state='queued', attempts=0, error=NULL, note='Перезапуск после повтора зависимости' WHERE id=?", (d["id"],))
                    todo.append(d["id"])
    return True


def worker_loop(once: bool = False, idle_sleep: float = 2.0) -> None:
    con = db.open_db()
    n = recover(con)
    if n:
        print(f"[worker] возобновлено задач: {n}", flush=True)
    pidf = config.DATA_DIR / "cache" / "worker.heartbeat"
    while True:
        pidf.write_text(f"{os.getpid()} {time.time()}")
        recover(con)
        job = claim(con)
        if not job:
            if once:
                return
            time.sleep(idle_sleep)
            continue
        print(f"[worker] {job['source_id']} {job['stage']} …", flush=True)
        st = run_job(con, job)
        print(f"[worker] {job['source_id']} {job['stage']} → {st}", flush=True)
