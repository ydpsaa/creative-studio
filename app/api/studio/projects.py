"""Projects: brief → knowledge selection → versioned documents → generation pack → portable export."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import zipfile

from . import brands, config, db, knowledge

DOCS = ["brief", "concept", "hooks", "script", "voiceover", "storyboard", "assets", "generation-prompts",
        "edit-notes", "sources", "review"]
JSON_DOCS = {"storyboard", "assets"}
SB_COLS = ["scene", "t_start", "t_end", "timing", "visual", "voice", "on_screen_text", "sound", "transition",
           "asset_id", "asset_task", "basis"]
ASSET_COLS = ["asset_id", "description", "status", "source_id", "usage_status", "how_to_get", "prompt_ref", "file"]
BRIEF_FIELDS = ["topic", "goal", "audience", "brand_id", "platform", "language", "duration", "format", "mode", "voice_source",
                "materials", "references", "constraints", "cta"]


def get(con, pid: str) -> dict | None:
    p = db.row(con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())
    if p:
        p["docs"] = {}
        for d in DOCS:
            r = con.execute("SELECT * FROM project_docs WHERE project_id=? AND doc=? ORDER BY version DESC LIMIT 1",
                            (pid, d)).fetchone()
            if r:
                x = db.row(r)
                if d in JSON_DOCS and isinstance(x["content"], str):
                    try:
                        x["content"] = json.loads(x["content"])
                    except ValueError:
                        pass
                p["docs"][d] = x
        p["versions"] = db.rows(con.execute("SELECT doc, version, origin, created_at, note FROM project_docs WHERE project_id=? "
                                            "ORDER BY created_at DESC", (pid,)))
    return p


def create(con, title: str, brief: dict, is_test: bool = False) -> dict:
    pid = db.new_id("prj")
    b = {k: brief.get(k, "") for k in BRIEF_FIELDS}
    b["assumptions"] = brief.get("assumptions") or []
    with db.tx(con):
        con.execute("INSERT INTO projects(id,title,brand_id,mode,status,brief,created_at,updated_at,is_test) VALUES (?,?,?,?,?,?,?,?,?)",
                    (pid, title, b.get("brand_id") or None, b.get("mode") or "standard", "brief", db.j(b), db.now(), db.now(),
                     int(is_test)))
        _save_doc(con, pid, "brief", _brief_md(title, b), "user", note="бриф создан")
    write_files(con, pid)
    return get(con, pid)


def _brief_md(title, b):
    names = {"topic": "Тема", "goal": "Цель", "audience": "Аудитория", "brand_id": "Бренд", "platform": "Площадка",
             "language": "Язык", "duration": "Длительность", "format": "Формат", "mode": "Режим", "voice_source": "Источник голоса", "materials": "Доступные материалы",
             "references": "Референсы", "constraints": "Ограничения", "cta": "Ожидаемое действие зрителя"}
    L = [f"# Бриф: {title}", ""] + [f"- **{n}:** {b.get(k) or '— (не задано)'}" for k, n in names.items()]
    if b.get("assumptions"):
        L += ["", "## Допущения (не подтверждены пользователем)"] + [f"- {a}" for a in b["assumptions"]]
    return "\n".join(L) + "\n"


def update_brief(con, pid: str, title: str, brief: dict) -> dict:
    b = {k: brief.get(k, "") for k in BRIEF_FIELDS}
    b["assumptions"] = brief.get("assumptions") or []
    with db.tx(con):
        con.execute("UPDATE projects SET title=?, brand_id=?, mode=?, brief=?, updated_at=? WHERE id=?",
                    (title, b.get("brand_id") or None, b.get("mode") or "standard", db.j(b), db.now(), pid))
        _save_doc(con, pid, "brief", _brief_md(title, b), "user", note="бриф изменён")
    write_files(con, pid)
    return get(con, pid)


def _save_doc(con, pid, doc, content, origin, kids=None, sids=None, note=None):
    v = (con.execute("SELECT max(version) FROM project_docs WHERE project_id=? AND doc=?", (pid, doc)).fetchone()[0] or 0) + 1
    con.execute("INSERT INTO project_docs VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, doc, v, content if isinstance(content, str) else db.j(content), origin, db.j(kids or []),
                 db.j(sids or []), db.now(), note))
    con.execute("UPDATE projects SET updated_at=? WHERE id=?", (db.now(), pid))
    body = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    db.index(con, "project_doc", f"{pid}:{doc}", doc, body[:20000])
    return v


def save_doc(con, pid: str, doc: str, content, origin="user", note=None, knowledge_ids=None) -> dict:
    if doc not in DOCS or doc == "sources":
        raise ValueError(f"Документ {doc} нельзя сохранить напрямую")
    if doc in JSON_DOCS and isinstance(content, str):
        content = json.loads(content)
    with db.tx(con):
        v = _save_doc(con, pid, doc, content, origin, kids=knowledge_ids, note=note)
    write_files(con, pid)
    return {"ok": True, "version": v}


def _or_query(text: str) -> str:
    words = [w for w in re.findall(r"\w{4,}", text.lower())][:40]
    return " OR ".join(f'"{w[:7]}"*' for w in dict.fromkeys(words))


def select_knowledge(con, pid: str, limit: int = 25) -> dict:
    p = get(con, pid)
    b = p["brief"]
    text = " ".join(str(b.get(k) or "") for k in ("topic", "goal", "format", "platform", "references", "constraints", "cta"))
    if p.get("mode") == "voice_only":
        text += " голос озвучка без лица запись экрана voice-only"
    hits = []
    q = _or_query(text)
    if q:
        for r in con.execute("SELECT eid, bm25(search) s FROM search WHERE search MATCH ? AND etype='knowledge' ORDER BY s LIMIT 200", (q,)):
            hits.append(r["eid"])
    cards = []
    for kid in dict.fromkeys(hits):
        k = knowledge.get(con, kid)
        if not k or k["status"] != "active" or k["is_test"] != p["is_test"] and k["is_test"]:
            continue
        if k["brand_scope"] not in ("all", None, "", p.get("brand_id")):
            continue          # brand-specific card of another brand
        cards.append(k)
        if len(cards) >= limit:
            break
    return {"query": q, "cards": cards, "preferences": brands.prefs_for_project(con, p)}


def generation_pack(con, pid: str) -> dict:
    p = get(con, pid)
    sel = select_knowledge(con, pid)
    brand = db.row(con.execute("SELECT * FROM brands WHERE id=?", (p.get("brand_id"),)).fetchone()) if p.get("brand_id") else None
    frags = []
    for k in sel["cards"]:
        for r in k["refs"]:
            if r.get("t_start") is None:
                continue
            a = con.execute("SELECT rel_path FROM artifacts WHERE source_id=? AND kind='transcript' ORDER BY created_at DESC LIMIT 1",
                            (r["source_id"],)).fetchone()
            if not a:
                continue
            tr = json.loads(config.safe_path(a["rel_path"]).read_text())
            ctx = [s for s in tr["segments"] if s["end"] >= r["t_start"] - 4 and s["start"] <= (r["t_end"] or r["t_start"]) + 4]
            frags.append({"knowledge_id": k["id"], "source_id": r["source_id"], "t_start": r["t_start"], "t_end": r["t_end"],
                          "transcript_with_context": [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in ctx]})
    out = config.DATA_DIR / "projects" / pid / "packs"
    out.mkdir(parents=True, exist_ok=True)
    name = f"project-generation-{db.now().replace(':', '').replace('+0000', '')}"
    pack = {"pack": "project-generation", "project_id": pid, "title": p["title"], "brief": p["brief"], "brand": brand,
            "mode": p["mode"], "mode_label": brands.PROJECT_MODES.get(p["mode"]),
            "preferences": [{"id": x["id"], "scope": x["scope"], "text": x["text"]} for x in sel["preferences"]],
            "knowledge": [{k2: k.get(k2) for k2 in ("id", "title", "kind", "category", "statement", "when_use", "when_not",
                                                     "steps", "example", "mistake", "evidence", "limitations", "refs")} for k in sel["cards"]],
            "source_fragments": frags,
            "instructions": (
                "Создай оригинальные документы проекта по брифу. Используй выбранные карточки как методы, не копируй чужие ролики. "
                "Для каждого существенного решения укажи knowledge_id в поле basis. Отмечай допущения. Тайминги — оценка. "
                "Ответ — JSON project-docs/1: {schema, origin, model?, docs:{concept, hooks, script, voiceover, "
                "storyboard:[{" + ", ".join(SB_COLS) + "}], assets:[{" + ", ".join(ASSET_COLS) + "}], generation-prompts, edit-notes}, "
                "knowledge_ids:[], source_ids:[], assumptions:[]}. Импорт: ./studio import-project " + pid + " answer.json")}
    (out / f"{name}.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"file": config.rel(out / f"{name}.json"), "knowledge": len(sel["cards"]), "fragments": len(frags),
            "preferences": len(sel["preferences"])}


def import_docs(con, pid: str, d: dict) -> dict:
    errs = []
    p = get(con, pid)
    if not p:
        return {"ok": False, "errors": [f"Проект {pid} не найден"]}
    if d.get("schema") != "project-docs/1":
        errs.append("schema должен быть 'project-docs/1'")
    docs = d.get("docs") or {}
    for k in docs:
        if k not in DOCS or k in ("brief", "sources"):
            errs.append(f"Неизвестный/запрещённый документ: {k}")
    kids = d.get("knowledge_ids") or []
    for kid in kids:
        if not con.execute("SELECT 1 FROM knowledge WHERE id=?", (kid,)).fetchone():
            errs.append(f"knowledge_id {kid} не существует")
    for sid in d.get("source_ids") or []:
        if not con.execute("SELECT 1 FROM sources WHERE id=?", (sid,)).fetchone():
            errs.append(f"source_id {sid} не существует")
    for i, row in enumerate(docs.get("storyboard") or []):
        for kid in re.findall(r"kn_\d{6}_[0-9a-f]{6}", str(row.get("basis", ""))):
            if not con.execute("SELECT 1 FROM knowledge WHERE id=?", (kid,)).fetchone():
                errs.append(f"storyboard[{i}].basis: {kid} не существует")
    for i, a in enumerate(docs.get("assets") or []):
        if a.get("source_id") and not con.execute("SELECT 1 FROM sources WHERE id=?", (a["source_id"],)).fetchone():
            errs.append(f"assets[{i}]: source_id {a['source_id']} не существует")
    if errs:
        return {"ok": False, "errors": errs}
    origin = d.get("origin") or "unknown"
    with db.tx(con):
        vers = {}
        for k, v in docs.items():
            vers[k] = _save_doc(con, pid, k, v, origin, kids=kids, sids=d.get("source_ids"), note=d.get("note") or "импорт")
        if d.get("assumptions"):
            b = p["brief"]
            b["assumptions"] = list(dict.fromkeys((b.get("assumptions") or []) + d["assumptions"]))
            con.execute("UPDATE projects SET brief=?, status='draft' WHERE id=?", (db.j(b), pid))
            _save_doc(con, pid, "brief", _brief_md(p["title"], b), origin, note="добавлены допущения")
    write_files(con, pid)
    return {"ok": True, "versions": vers}


# ------------------------------------------------------------------ files and export
def _sources_md(con, p) -> str:
    kids, sids = set(), set()
    for d in p["docs"].values():
        kids |= set(d.get("knowledge_ids") or [])
        sids |= set(d.get("source_ids") or [])
    L = ["# Использованные источники и знания", "", "## Карточки знаний (id · версия · обоснованность)"]
    for kid in sorted(kids):
        k = knowledge.get(con, kid)
        if k:
            L.append(f"- `{kid}` v{k['version']} — {k['title']} [{knowledge.EVIDENCE.get(k['evidence'])}]")
            sids |= {r["source_id"] for r in k["refs"]}
    L += ["", "## Источники"]
    for sid in sorted(sids):
        s = con.execute("SELECT title, url, usage_status FROM sources WHERE id=?", (sid,)).fetchone()
        if s:
            L.append(f"- `{sid}` — {s['title']} · {s['url'] or 'локальный файл'} · статус использования: {s['usage_status']}")
    L += ["", "## Версии документов"] + [f"- {d}: v{x['version']} ({x['origin']}, {x['created_at']})" for d, x in p["docs"].items()]
    return "\n".join(L) + "\n"


def _csv(rows, cols) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows or []:
        w.writerow({c: (db.j(r.get(c)) if isinstance(r.get(c), (list, dict)) else r.get(c, "")) for c in cols})
    return buf.getvalue()


def write_files(con, pid: str) -> None:
    """Readable export of the current versions into studio-data/projects/<pid>/ (SQLite stays the source of truth)."""
    p = get(con, pid)
    d = config.DATA_DIR / "projects" / pid
    d.mkdir(parents=True, exist_ok=True)
    for doc, x in p["docs"].items():
        c = x["content"]
        if doc == "storyboard":
            (d / "storyboard.json").write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
            (d / "storyboard.csv").write_text(_csv(c, SB_COLS), encoding="utf-8")
        elif doc == "assets":
            (d / "assets.csv").write_text(_csv(c, ASSET_COLS), encoding="utf-8")
            (d / "assets.json").write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            (d / f"{doc}.md").write_text(c, encoding="utf-8")
    (d / "sources.md").write_text(_sources_md(con, p), encoding="utf-8")
    (d / "project.json").write_text(json.dumps({k: p[k] for k in ("id", "title", "brand_id", "mode", "status", "brief",
                                                                    "created_at", "updated_at")} |
                                               {"doc_versions": {k: v["version"] for k, v in p["docs"].items()}},
                                               ensure_ascii=False, indent=2), encoding="utf-8")


def export_zip(con, pid: str, mode: str = "portable") -> dict:
    """mode=links: documents + asset references only. mode=portable: also copies asset files that are allowed to travel."""
    write_files(con, pid)
    p = get(con, pid)
    src = config.DATA_DIR / "projects" / pid
    v = len(list((config.DATA_DIR / "exports" / pid).glob("v*"))) + 1 if (config.DATA_DIR / "exports" / pid).exists() else 1
    out = config.DATA_DIR / "exports" / pid / f"v{v}"
    pkg = out / "package"
    pkg.mkdir(parents=True)
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, pkg / f.name)
    assets_out = []
    for a in (p["docs"].get("assets") or {}).get("content") or []:
        entry = {"asset_id": a.get("asset_id"), "description": a.get("description"), "status": a.get("status"),
                 "source_id": a.get("source_id"), "included": False}
        if a.get("source_id"):
            s = con.execute("SELECT rel_path, usage_status, url, original_name FROM sources WHERE id=?", (a["source_id"],)).fetchone()
            entry.update(usage_status=s["usage_status"], url=s["url"])
            if mode == "portable" and s["rel_path"] and s["usage_status"] in ("мой материал", "разрешено"):
                dst = pkg / "assets" / f"{a['asset_id']}_{s['original_name']}"
                dst.parent.mkdir(exist_ok=True)
                shutil.copy2(config.safe_path(s["rel_path"]), dst)
                entry.update(included=True, file=f"assets/{dst.name}")
            elif s["rel_path"]:
                entry["reason_not_included"] = ("режим «только ссылки»" if mode == "links" else
                                                f"статус использования «{s['usage_status']}» — файл не переносится в пакет")
        else:
            entry["reason_not_included"] = "ассета ещё нет: " + (a.get("how_to_get") or a.get("status") or "задача")
        assets_out.append(entry)
    files = []
    for f in sorted(pkg.rglob("*")):
        if f.is_file():
            files.append({"path": str(f.relative_to(pkg)), "bytes": f.stat().st_size,
                          "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
    manifest = {"package": "creative-studio-project", "format_version": 1, "project_id": pid, "title": p["title"],
                "export_version": v, "mode": mode, "exported_at": db.now(), "files": files, "assets": assets_out,
                "doc_versions": {k: x["version"] for k, x in p["docs"].items()},
                "check": "Проверьте целостность: sha256 каждого файла из files должен совпадать."}
    (pkg / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    zp = out / f"{pid}_v{v}_{mode}.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pkg.rglob("*")):
            if f.is_file():
                z.write(f, f"{pid}/{f.relative_to(pkg)}")
    return {"zip": config.rel(zp), "files": len(files), "assets": assets_out}


def list_projects(con):
    return db.rows(con.execute("SELECT id,title,brand_id,mode,status,created_at,updated_at,is_test FROM projects ORDER BY updated_at DESC"))
