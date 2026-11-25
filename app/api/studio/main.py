"""Local HTTP API + static UI. Binds to loopback only (see ./studio start)."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from . import backup as backup_mod
from . import brands, config, coverage, db, jobs, knowledge, media, projects, results, sources

app = FastAPI(title="Creative Studio", docs_url="/api/docs", openapi_url="/api/openapi.json")
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


@app.middleware("http")
async def guard(request: Request, call_next):
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    if host not in ALLOWED_HOSTS:                       # DNS-rebinding protection
        return JSONResponse({"error": "host not allowed"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1].rsplit(":", 1)[0] not in ALLOWED_HOSTS:
            return JSONResponse({"error": "cross-origin write refused"}, status_code=403)
        if request.headers.get("x-studio") != "1":      # custom header ⇒ cross-site forms can't forge it
            return JSONResponse({"error": "missing X-Studio header"}, status_code=403)
    return await call_next(request)


def con():
    return db.open_db()


# ------------------------------------------------------------------ system
@app.get("/api/health")
def health():
    return {"ok": True, "data_dir": str(config.DATA_DIR)}


def _tools():
    import importlib.util as u
    hb = config.DATA_DIR / "cache" / "worker.heartbeat"
    alive = hb.exists() and time.time() - float(hb.read_text().split()[1]) < 30
    import os
    hub = Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub")
    whisper_cached = (hub / "models--mlx-community--whisper-large-v3-turbo").exists()
    ast_cached = (hub / "models--MIT--ast-finetuned-audioset-10-10-0.4593").exists()
    return {
        "worker": {"alive": alive},
        "ffmpeg": {"available": bool(media.ffmpeg_exe()), "path": media.ffmpeg_exe()},
        "probe": {"available": bool(u.find_spec("av")), "method": "PyAV"},
        "speech": {"available": bool(u.find_spec("mlx_whisper")) and whisper_cached,
                   "method": "mlx-whisper large-v3-turbo (локально)" + ("" if whisper_cached else
                             " — модель не найдена в кэше: скачается при первой транскрипции (~1,6 ГБ, нужна сеть)")},
        "sound_events": {"available": bool(u.find_spec("transformers")) and ast_cached,
                         "method": "AST AudioSet + librosa (локально, оценки)" + ("" if ast_cached else
                                   " — модель не найдена в кэше: скачается при первом анализе (~330 МБ, нужна сеть)")},
        "ocr": {"available": bool(u.find_spec("ocrmac")), "method": "Apple Vision (локально)"},
        "scene_detection": {"available": bool(media.ffmpeg_exe()), "method": "FFmpeg scene score"},
        "audio_signal": {"available": True, "method": "ebur128, silencedetect, RMS — только оценки"},
        "music_separation": {"available": False, "how": "Не подключено. Нужен адаптер (например, Demucs) — см. docs/ADAPTERS.md"},
        "visual_ai": {"available": False, "how": "AI-провайдер не подключён. Используйте пакет source-analysis и импорт ответа."},
        "ai_provider": {"available": False, "mode": "none",
                        "how": "Ключ API не настроен. Режим без AI: пакеты контекста → ручной ответ Claude → проверяемый импорт."},
        "pdf_docx_text": {"available": False, "how": "Экспортируйте документ в .txt/.md; адаптер PDF не установлен"},
    }


@app.get("/api/links")
def links():
    f = config.PROJECT_ROOT / "config" / "links.json"
    d = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return {"site": d.get("site"), "socials": [s for s in d.get("socials", []) if s.get("url")]}


@app.get("/api/tools")
def tools():
    return _tools()


@app.get("/api/stats")
def stats():
    c = con()
    return {
        "sources": db.rows(c.execute("SELECT kind, count(*) n FROM sources WHERE is_test=0 GROUP BY kind")),
        "jobs": db.rows(c.execute("SELECT state, count(*) n FROM jobs GROUP BY state")),
        "knowledge": c.execute("SELECT count(*) FROM knowledge WHERE status='active' AND is_test=0").fetchone()[0],
        "projects": c.execute("SELECT count(*) FROM projects WHERE is_test=0").fetchone()[0],
        "accounts": c.execute("SELECT count(*) FROM accounts").fetchone()[0],
    }


@app.get("/api/file")
def file(path: str, download: int = 0):
    try:
        p = config.safe_path(path)
    except ValueError:
        raise HTTPException(400, "bad path")
    if not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type=media.mime_for(p), filename=p.name if download else None)


# ------------------------------------------------------------------ sources
@app.get("/api/sources")
def list_sources(q: str = "", kind: str = "", status: str = "", collection: str = "", brand: str = "",
                 include_test: int = 0):
    c = con()
    sql = "SELECT s.* FROM sources s WHERE 1=1"
    a: list = []
    if not include_test:
        sql += " AND s.is_test=0"
    if kind:
        sql += " AND s.kind=?"
        a.append(kind)
    if brand:
        sql += " AND s.brand_id=?"
        a.append(brand)
    if collection:
        sql += " AND s.id IN (SELECT source_id FROM source_collections WHERE collection_id=?)"
        a.append(collection)
    if status == "blocked":
        sql += " AND (s.blocked_reason IS NOT NULL OR s.st_acquire IN ('unavailable','login_wall','metadata_only','failed'))"
    elif status == "analyzed":
        sql += " AND s.st_analysis LIKE 'completed%'"
    elif status == "awaiting_ai":
        sql += " AND s.st_analysis='awaiting_ai'"
    if q:
        ids = {r["sid"] for r in knowledge.search(c, q, limit=500, include_test=bool(include_test)) if r["sid"]}
        ids |= {r["eid"] for r in knowledge.search(c, q, ["source", "page"], limit=500, include_test=bool(include_test))}
        if not ids:
            return []
        sql += f" AND s.id IN ({','.join('?' * len(ids))})"
        a += list(ids)
    sql += " ORDER BY s.imported_at DESC"
    out = db.rows(c.execute(sql, a))
    for s in out:
        p = c.execute("SELECT rel_path FROM artifacts WHERE source_id=? AND kind='poster' ORDER BY created_at DESC LIMIT 1",
                      (s["id"],)).fetchone()
        s["poster"] = p["rel_path"] if p else None
        s["jobs"] = db.rows(c.execute("SELECT stage, state FROM jobs WHERE source_id=?", (s["id"],)))
    return out


@app.get("/api/collections")
def collections():
    return db.rows(con().execute("SELECT c.*, (SELECT count(*) FROM source_collections WHERE collection_id=c.id) n FROM collections c"))


def _latest(c, sid, kind):
    a = c.execute("SELECT * FROM artifacts WHERE source_id=? AND kind=? ORDER BY created_at DESC LIMIT 1", (sid, kind)).fetchone()
    return db.row(a)


def _read_json(a):
    if not a:
        return None
    p = config.safe_path(a["rel_path"])
    return json.loads(p.read_text()) if p.exists() else None


@app.get("/api/sources/{sid}")
def source_detail(sid: str):
    c = con()
    s = sources.get(c, sid)
    if not s:
        raise HTTPException(404)
    frames_idx = _latest(c, sid, "frames_index")
    fr = _read_json(frames_idx)
    if fr:
        base = frames_idx["rel_path"].rsplit("/", 1)[0]
        for f in fr["frames"]:
            f["rel_path"] = f"{base}/{f['file']}"
    an = c.execute("SELECT * FROM analyses WHERE source_id=? ORDER BY version DESC LIMIT 1", (sid,)).fetchone()
    kn = db.rows(c.execute("SELECT DISTINCT k.id, k.title, k.kind, k.category, k.evidence, r.t_start, r.t_end FROM knowledge k "
                           "JOIN knowledge_refs r ON r.knowledge_id=k.id WHERE r.source_id=? AND k.status='active' ORDER BY r.t_start", (sid,)))
    pack = _latest(c, sid, "context_pack")
    acct = db.rows(c.execute("SELECT a.* FROM accounts a JOIN account_posts p ON p.account_id=a.id WHERE p.source_id=?", (sid,)))
    return {
        "source": s,
        "links": db.rows(c.execute("SELECT * FROM source_links WHERE source_id=?", (sid,))),
        "collections": [r["name"] for r in c.execute("SELECT c.name FROM collections c JOIN source_collections x ON x.collection_id=c.id WHERE x.source_id=?", (sid,))],
        "relations": db.rows(c.execute("SELECT * FROM source_relations WHERE a=? OR b=?", (sid, sid))),
        "jobs": db.rows(c.execute("SELECT * FROM jobs WHERE source_id=? ORDER BY created_at", (sid,))),
        "technical": _read_json(_latest(c, sid, "technical")),
        "transcript": _read_json(_latest(c, sid, "transcript")),
        "ocr": _read_json(_latest(c, sid, "ocr")),
        "frames": fr,
        "audio_features": _read_json(_latest(c, sid, "audio_features")),
        "page": _read_json(_latest(c, sid, "page_meta")),
        "sound_map": _read_json(_latest(c, sid, "sound_map")),
        "spectrogram": (_latest(c, sid, "spectrogram") or {}).get("rel_path"),
        "dense": [dict(_read_json(a) or {}, rel_dir=a["rel_path"].rsplit("/", 1)[0])
                  for a in db.rows(c.execute("SELECT * FROM artifacts WHERE source_id=? AND kind='dense_index' ORDER BY t", (sid,)))],
        "pack_job": db.row(c.execute("SELECT id, state, finished_at FROM jobs WHERE source_id=? AND stage='context_pack' "
                                     "ORDER BY created_at DESC LIMIT 1", (sid,)).fetchone()),
        "coverage": coverage.build(c, sid),
        "analysis": db.row(an),
        "knowledge": kn,
        "pack": pack["rel_path"] if pack else None,
        "accounts": acct,
    }


@app.patch("/api/sources/{sid}")
def patch_source(sid: str, body: dict = Body(...)):
    return sources.update_meta(con(), sid, body)


def _save_upload(up: UploadFile) -> Path:
    inbox = config.DATA_DIR / "inbox" / f"upload-{db.new_id('u')}"
    inbox.mkdir(parents=True)
    dst = inbox / sources.safe_name(up.filename or "file")
    with dst.open("wb") as f:
        shutil.copyfileobj(up.file, f)
    return dst


@app.post("/api/import/files")
def import_files(files: list[UploadFile] = File(...), meta: str = Form("{}")):
    c = con()
    m = json.loads(meta or "{}")
    out = []
    for up in files:
        p = _save_upload(up)
        try:
            r = sources.import_file(c, p, m | {"via": m.get("via") or "загрузка в интерфейсе"}, move=True)
            out.append({"ok": True, "name": up.filename, **r})
        except Exception as e:  # noqa: BLE001
            out.append({"ok": False, "name": up.filename, "error": str(e)})
        finally:
            shutil.rmtree(p.parent, ignore_errors=True)
    return out


@app.post("/api/sources/{sid}/attach")
def attach(sid: str, file: UploadFile = File(...)):
    c = con()
    p = _save_upload(file)
    try:
        return sources.import_file(c, p, {"via": "дополнено файлом в интерфейсе"}, move=True, attach_to=sid)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    finally:
        shutil.rmtree(p.parent, ignore_errors=True)


@app.post("/api/import/urls")
def import_urls(body: dict = Body(...)):
    c = con()
    out = []
    for u in body.get("urls", []):
        if not u.strip():
            continue
        try:
            out.append({"ok": True, "input": u, **sources.import_url(c, u, body.get("meta") or {})})
        except Exception as e:  # noqa: BLE001
            out.append({"ok": False, "input": u, "error": str(e)})
    return out


@app.post("/api/import/note")
def import_note(body: dict = Body(...)):
    return sources.add_note(con(), body["title"], body.get("text", ""))


@app.post("/api/sources/{sid}/analysis")
def import_analysis(sid: str, body: dict = Body(...), dry_run: int = 0):
    return results.import_analysis(con(), sid, body, dry_run=bool(dry_run))


@app.post("/api/sources/{sid}/pack")
def prepare_pack(sid: str):
    """«Подготовить пакет для Claude»: rebuild source-analysis from the current extraction results (runs in worker)."""
    c = con()
    j = c.execute("SELECT id, state FROM jobs WHERE source_id=? AND stage='context_pack' ORDER BY created_at DESC LIMIT 1", (sid,)).fetchone()
    if not j:
        raise HTTPException(400, "Для этого типа источника пакет не предусмотрен")
    if j["state"] in ("queued", "running"):
        return {"ok": True, "state": j["state"], "job_id": j["id"]}
    jobs.retry(c, j["id"], force=True)
    return {"ok": True, "state": "queued", "job_id": j["id"]}


@app.post("/api/sources/{sid}/dense")
def dense(sid: str, body: dict = Body(...)):
    from . import processors
    c = con()
    try:
        r = processors.dense_pass(c, sid, float(body["t0"]), float(body["t1"]), float(body.get("step", 0.1)))
    except ValueError as e:
        raise HTTPException(400, str(e))
    coverage.write(c, sid)
    return r


@app.get("/api/accounts")
def accounts():
    c = con()
    out = db.rows(c.execute("SELECT * FROM accounts"))
    for a in out:
        a["posts"] = db.rows(c.execute("SELECT s.id, s.title, s.url, p.studied FROM account_posts p JOIN sources s ON s.id=p.source_id "
                                       "WHERE p.account_id=?", (a["id"],)))
    return out


@app.post("/api/accounts/{aid}/posts")
def account_post(aid: str, body: dict = Body(...)):
    c = con()
    r = sources.import_url(c, body["url"], {"via": f"публикация аккаунта {aid}"})
    sources.link_account_post(c, aid, r["source"]["id"])
    return r


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs")
def list_jobs(state: str = ""):
    c = con()
    sql = ("SELECT j.*, s.title, s.original_name FROM jobs j JOIN sources s ON s.id=j.source_id"
           + (" WHERE j.state=?" if state else "") + " ORDER BY j.created_at DESC, j.rowid DESC LIMIT 500")
    return db.rows(c.execute(sql, (state,) if state else ()))


@app.post("/api/jobs/{jid}/retry")
def retry(jid: str):
    return {"ok": jobs.retry(con(), jid)}


@app.post("/api/jobs/{jid}/cancel")
def cancel(jid: str):
    return {"ok": jobs.cancel(con(), jid)}


# ------------------------------------------------------------------ knowledge / search
@app.get("/api/knowledge")
def list_knowledge(q: str = "", category: str = "", evidence: str = "", kind: str = "", include_test: int = 0):
    c = con()
    sql = "SELECT * FROM knowledge WHERE status='active'"
    a: list = []
    if not include_test:
        sql += " AND is_test=0"
    for col, v in (("category", category), ("evidence", evidence), ("kind", kind)):
        if v:
            sql += f" AND {col}=?"
            a.append(v)
    if q:
        ids = [r["eid"] for r in knowledge.search(c, q, ["knowledge"], limit=300, include_test=bool(include_test))]
        if not ids:
            return []
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        a += ids
    out = db.rows(c.execute(sql + " ORDER BY category, title", a))
    for k in out:
        k["sources"] = [r["source_id"] for r in c.execute("SELECT DISTINCT source_id FROM knowledge_refs WHERE knowledge_id=?", (k["id"],))]
    return out


@app.get("/api/knowledge/{kid}")
def get_knowledge(kid: str):
    c = con()
    k = knowledge.get(c, kid)
    if not k:
        raise HTTPException(404)
    for r in k["refs"]:
        s = c.execute("SELECT title, kind FROM sources WHERE id=?", (r["source_id"],)).fetchone()
        r["source_title"] = s["title"] if s else None
        if r.get("artifact_id"):
            a = c.execute("SELECT rel_path FROM artifacts WHERE id=?", (r["artifact_id"],)).fetchone()
            r["frame_path"] = a["rel_path"] if a else None
    return k


@app.patch("/api/knowledge/{kid}")
def patch_knowledge(kid: str, body: dict = Body(...)):
    r = knowledge.update(con(), kid, body.get("changes", {}), body.get("note", ""), "user", body.get("base_version"))
    if not r["ok"]:
        raise HTTPException(409, r["error"])
    return r


@app.get("/api/search")
def search(q: str, include_test: int = 0):
    return knowledge.search(con(), q, limit=80, include_test=bool(include_test))


@app.get("/api/meta")
def meta():
    return {"kinds": knowledge.KINDS, "evidence": knowledge.EVIDENCE, "categories": config.KNOWLEDGE_CATEGORIES,
            "pref_scopes": brands.PREF_SCOPES, "project_modes": brands.PROJECT_MODES, "voice_sources": brands.VOICE_SOURCES, "mode_rules": brands.MODE_RULES, "docs": projects.DOCS,
            "storyboard_cols": projects.SB_COLS, "asset_cols": projects.ASSET_COLS}


# ------------------------------------------------------------------ brands & preferences
@app.get("/api/brands")
def get_brands():
    return brands.list_brands(con())


@app.post("/api/brands")
def save_brand(body: dict = Body(...)):
    return brands.save_brand(con(), body.get("id"), body["name"], body.get("data") or {})


@app.get("/api/preferences")
def get_prefs():
    return db.rows(con().execute("SELECT * FROM preferences ORDER BY active DESC, scope, created_at DESC"))


@app.post("/api/preferences")
def add_pref(body: dict = Body(...)):
    try:
        return brands.add_pref(con(), body["scope"], body.get("scope_id"), body["text"], body.get("replaces"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/preferences/{pid}/active")
def pref_active(pid: str, body: dict = Body(...)):
    brands.set_pref_active(con(), pid, bool(body.get("active")))
    return {"ok": True}


# ------------------------------------------------------------------ projects
@app.get("/api/projects")
def list_projects():
    return projects.list_projects(con())


@app.post("/api/projects")
def new_project(body: dict = Body(...)):
    return projects.create(con(), body.get("title") or "Без названия", body.get("brief") or {})


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = projects.get(con(), pid)
    if not p:
        raise HTTPException(404)
    return p


@app.put("/api/projects/{pid}/brief")
def put_brief(pid: str, body: dict = Body(...)):
    return projects.update_brief(con(), pid, body.get("title") or "Без названия", body.get("brief") or {})


@app.put("/api/projects/{pid}/docs/{doc}")
def put_doc(pid: str, doc: str, body: dict = Body(...)):
    try:
        return projects.save_doc(con(), pid, doc, body["content"], "user", body.get("note"), body.get("knowledge_ids"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/projects/{pid}/knowledge")
def project_knowledge(pid: str):
    return projects.select_knowledge(con(), pid)


@app.post("/api/projects/{pid}/pack")
def project_pack(pid: str):
    return projects.generation_pack(con(), pid)


@app.post("/api/projects/{pid}/import")
def project_import(pid: str, body: dict = Body(...)):
    return projects.import_docs(con(), pid, body)


@app.post("/api/projects/{pid}/export")
def project_export(pid: str, mode: str = "portable"):
    return projects.export_zip(con(), pid, mode)


# ------------------------------------------------------------------ settings & backup
@app.get("/api/settings")
def settings():
    c = con()
    st = {r["key"]: json.loads(r["value"]) for r in c.execute("SELECT * FROM settings")}
    return {"data_dir": str(config.DATA_DIR), "port": config.PORT,
            "ai_mode": st.get("ai_mode", "none"),
            "limits": st.get("limits", {"batch_size": 20, "parallel_jobs": 1, "paid_budget_usd": 0}),
            "outbound": "В режиме «без AI» студия не отправляет материалы наружу. Сеть используется только для "
                        "получения страниц по ссылкам, которые вы добавили.",
            "tools": _tools(),
            "backups": sorted([p.name for p in (config.DATA_DIR / "backups").glob("backup-*")], reverse=True)}


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    c = con()
    if "limits" in body:
        c.execute("INSERT OR REPLACE INTO settings VALUES ('limits', ?)", (json.dumps(body["limits"]),))
    if body.get("ai_mode") not in (None, "none"):
        raise HTTPException(400, "AI-провайдер не подключён: режим можно включить после настройки адаптера")
    return settings()


@app.get("/api/skill/exports")
def skill_exports(name: str = "dolishx-creative-method"):
    from . import skill_export
    root = config.DATA_DIR / "exports" / "skills" / name
    out = []
    for v in sorted([p for p in root.glob("v*") if p.is_dir()], key=lambda p: -int(p.name[1:])):
        pkg = v / name
        man = json.loads((pkg / "provenance" / "manifest.json").read_text()) if (pkg / "provenance" / "manifest.json").exists() else {}
        z = next(v.glob("*.zip"), None)
        out.append({"version": man.get("version"), "exported_at": man.get("exported_at"), "methods": len(man.get("methods", [])),
                    "examples": len(man.get("examples", [])), "zip": config.rel(z) if z else None, "dir": config.rel(pkg),
                    "check": skill_export.check(pkg),
                    "changelog": (pkg / "provenance" / "CHANGELOG.md").read_text(encoding="utf-8") if (pkg / "provenance" / "CHANGELOG.md").exists() else ""})
    return out


@app.post("/api/skill/export")
def skill_export_now(body: dict = Body(default={})):
    from . import skill_export
    return skill_export.export(con(), body.get("name") or skill_export.DEFAULT_NAME)


@app.post("/api/backup")
def do_backup(body: dict = Body(default={})):
    d = body.get("dest")
    return backup_mod.create(con(), Path(d) if d else None)


# ------------------------------------------------------------------ review board («Ревью»)
def _rv(fn, *a, **kw):
    from . import review
    try:
        return fn(review, *a, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/review/summary")
def review_summary(scan: int = 1):
    c = con()
    sc = _rv(lambda r: r.scan(c)) if scan else None     # cheap stat of ~350 files: new files appear without a CLI step
    return {**_rv(lambda r: r.summary(c)), "scan": sc}


@app.get("/api/review/items")
def review_items(kind: str = "", status: str = "", group: str = "", q: str = "", include_missing: int = 0):
    c = con()
    return _rv(lambda r: r.list_items(c, kind, status, group, q, bool(include_missing)))


@app.post("/api/review/scan")
def review_scan():
    c = con()
    return _rv(lambda r: r.scan(c))


@app.post("/api/review/decide")
def review_decide(body: dict = Body(...)):
    c = con()
    return _rv(lambda r: r.decide(c, body.get("ids") or [], body.get("status"), body.get("comment"), body.get("tags"),
                                  body.get("forward")))


@app.post("/api/review/group")
def review_group(body: dict = Body(...)):
    c = con()
    return _rv(lambda r: r.group_comment(c, body.get("kind", ""), body.get("group", ""), body.get("comment"), body.get("forward")))


@app.get("/api/review/feedback")
def review_feedback(since: str = "", include_handled: int = 0):
    c = con()
    return _rv(lambda r: r.feedback(c, since or None, bool(include_handled)))


@app.post("/api/review/items/{iid}/trash")
def review_trash(iid: str, body: dict = Body(default={})):
    c = con()
    try:
        return _rv(lambda r: r.trash_item(c, iid, bool(body.get("confirm"))))
    except RuntimeError as e:
        raise HTTPException(500, f"Не удалось переместить в Корзину: {e}")


@app.get("/api/review/file/{iid}")
def review_file(iid: str):
    c = con()
    it = _rv(lambda r: r.get_item(c, iid))
    if not it:
        raise HTTPException(404, "not found")
    p = _rv(lambda r: r.abs_path(it))
    if not p.is_file():
        raise HTTPException(404, "файла нет на месте")
    mt = "text/markdown; charset=utf-8" if p.suffix == ".md" else media.mime_for(p)
    return FileResponse(p, media_type=mt)


@app.get("/api/review/thumb/{iid}")
def review_thumb(iid: str):
    c = con()
    p = _rv(lambda r: r.thumb(c, iid))
    if not p:
        raise HTTPException(404, "нет миниатюры")
    return FileResponse(p, media_type=media.mime_for(p), headers={"Cache-Control": "max-age=86400"})


# ------------------------------------------------------------------ STACKER («Стакер»)
def _stk(fn):
    from . import stacker
    try:
        return fn(stacker)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/stacker/stickers")
def stk_stickers():
    return _stk(lambda s: s.stickers())


@app.get("/api/stacker/sticker/{group}/{name}")
def stk_sticker(group: str, name: str):
    p = _stk(lambda s: s.sticker_path(group, name))
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/stacker/projects")
def stk_list():
    return _stk(lambda s: s.list_projects())


@app.post("/api/stacker/projects")
def stk_create(body: dict = Body(default={})):
    return _stk(lambda s: s.create(body.get("title", "")))


@app.get("/api/stacker/projects/{pid}")
def stk_get(pid: str):
    return _stk(lambda s: s.get(pid))


@app.put("/api/stacker/projects/{pid}")
def stk_save(pid: str, body: dict = Body(...)):
    return _stk(lambda s: s.save(pid, body))


@app.post("/api/stacker/projects/{pid}/preview")
def stk_preview(pid: str, body: dict = Body(default={})):
    return _stk(lambda s: s.preview(pid, body.get("times")))


@app.post("/api/stacker/projects/{pid}/render")
def stk_render(pid: str):
    return _stk(lambda s: s.render(pid))


@app.post("/api/stacker/projects/{pid}/{kind}")
def stk_attach(pid: str, kind: str, file: UploadFile = File(...)):
    if kind not in ("voice", "talk"):
        raise HTTPException(404)
    data = file.file.read()
    return _stk(lambda s: s.attach(pid, kind, file.filename or "file", data))


@app.delete("/api/stacker/projects/{pid}/{kind}")
def stk_detach(pid: str, kind: str):
    if kind not in ("voice", "talk"):
        raise HTTPException(404)
    return _stk(lambda s: s.detach(pid, kind))


@app.delete("/api/stacker/projects/{pid}")
def stk_delete(pid: str):
    return _stk(lambda s: s.delete(pid))


from .clipping import router as clipping_router
app.include_router(clipping_router)


# ------------------------------------------------------------------ static UI
@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404)
    f = (config.WEB_DIST / full_path).resolve()
    if full_path and f.is_file() and config.WEB_DIST in f.parents:
        return FileResponse(f)
    idx = config.WEB_DIST / "index.html"
    if idx.exists():
        return FileResponse(idx, headers={"Cache-Control": "no-cache"})   # always pick up a fresh UI build
    return JSONResponse({"error": "UI не собран: cd app/web && npm install && npm run build"}, status_code=503)
