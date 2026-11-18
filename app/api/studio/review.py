"""«Ревью»: всё, что AI сгенерировал для стилей владельца, в одном месте + решения владельца.

Файлы только читаются по пути из `config/review_sets.json` (в базу не копируются; миниатюры — в cache/).
Решения (статус, комментарий, теги, «отправить Claude») — в SQLite, история — в review_events.
«Удалить» = статус rejected. Перемещение файла в Корзину macOS — отдельное действие с confirm, никогда не удаление.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from . import config, db, media

SETS_FILE = Path(os.environ.get("STUDIO_REVIEW_CONFIG") or config.PROJECT_ROOT / "config" / "review_sets.json")
STATUSES = ("new", "selected", "rejected", "needs_changes")
KIND_LABELS = {"sticker_xerox": "Стикеры XEROX", "sticker_pixel": "Стикеры PIXEL", "video": "Видео",
               "sound": "Звуки", "doc": "Документы стиля"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
THUMB_W = 360


# ------------------------------------------------------------------ registry
def load_sets() -> dict:
    return json.loads(SETS_FILE.read_text(encoding="utf-8"))


def roots() -> dict[str, Path]:
    out = {}
    for k, v in load_sets()["roots"].items():
        p = Path(os.path.expanduser(v))
        out[k] = (p if p.is_absolute() else config.PROJECT_ROOT / p).resolve()
    return out


def item_id(root: str, rel_path: str) -> str:
    return "rv_" + hashlib.sha1(f"{root}:{rel_path}".encode()).hexdigest()[:12]


def group_id(kind: str, grp: str) -> str:
    return f"rg:{kind}:{grp}"


def abs_path(it: dict) -> Path:
    """Absolute path of a registry item, refusing anything that escapes its root."""
    r = roots().get(it["root"])
    if r is None:
        raise ValueError(f"неизвестный корень {it['root']}")
    p = (r / it["rel_path"]).resolve()
    if r not in p.parents:
        raise ValueError("путь вне корня ревью")
    return p


def _event(con, iid: str, action: str, data: dict | None = None, actor: str = "owner") -> None:
    con.execute("INSERT INTO review_events(item_id, action, data, actor, at) VALUES (?,?,?,?,?)",
                (iid, action, db.j(data or {}), actor, db.now()))


def scan(con) -> dict:
    """(Re)index files of all sets. New files → status new; vanished → missing=1 (decisions kept)."""
    cfg = load_sets()
    rts = roots()
    now = db.now()
    seen: set[str] = set()
    new = changed = 0
    per_kind: dict[str, int] = {}
    with db.tx(con):
        for s in cfg["sets"]:
            base = rts[s["root"]]
            for f in sorted(base.glob(s["glob"])):
                if not f.is_file() or f.name.startswith("."):
                    continue
                rel = str(f.relative_to(base))
                iid = item_id(s["root"], rel)
                if iid in seen:
                    continue
                seen.add(iid)
                per_kind[s["kind"]] = per_kind.get(s["kind"], 0) + 1
                st = f.stat()
                grp = f.parent.name if s.get("group", "{dir}") == "{dir}" else s["group"]
                old = con.execute("SELECT size, mtime FROM review_items WHERE id=?", (iid,)).fetchone()
                if old is None:
                    con.execute("INSERT INTO review_items(id, kind, grp, title, root, rel_path, ext, size, mtime, first_seen, last_seen) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                (iid, s["kind"], grp, f.stem.replace("_", " "), s["root"], rel, f.suffix.lower(),
                                 st.st_size, st.st_mtime, now, now))
                    _event(con, iid, "scan_new", {"path": rel}, "studio")
                    new += 1
                else:
                    upd = old["size"] != st.st_size or abs((old["mtime"] or 0) - st.st_mtime) > 1e-3
                    con.execute("UPDATE review_items SET kind=?, grp=?, size=?, mtime=?, last_seen=?, missing=0"
                                + (", file_changed_at=?" if upd else "") + " WHERE id=?",
                                (s["kind"], grp, st.st_size, st.st_mtime, now, *([now] if upd else []), iid))
                    if upd:
                        _event(con, iid, "file_changed", {}, "studio")
                        changed += 1
        gone = [r["id"] for r in con.execute("SELECT id FROM review_items WHERE missing=0")
                if r["id"] not in seen]
        for iid in gone:
            con.execute("UPDATE review_items SET missing=1 WHERE id=?", (iid,))
            _event(con, iid, "missing", {}, "studio")
    return {"total": len(seen), "new": new, "changed": changed, "missing_now": len(gone), "by_kind": per_kind,
            "config": str(SETS_FILE)}


# ------------------------------------------------------------------ read
def _out(it: dict, with_path: bool = True) -> dict:
    d = {k: it.get(k) for k in ("id", "kind", "grp", "title", "status", "comment", "tags", "forward", "forward_at",
                                "handled_at", "handled_note", "decided_at", "file_changed_at", "missing", "trashed_at",
                                "ext", "size", "first_seen")}
    d["group"] = d.pop("grp")
    d["forward"] = bool(d["forward"])
    d["missing"] = bool(d["missing"])
    if not isinstance(d["tags"], list):
        d["tags"] = []
    if with_path:
        d["rel_path"] = it["rel_path"]
        try:
            d["path"] = str(abs_path(it))
        except ValueError:
            d["path"] = None
    return d


def get_item(con, iid: str) -> dict | None:
    return db.row(con.execute("SELECT * FROM review_items WHERE id=?", (iid,)).fetchone())


def list_items(con, kind: str = "", status: str = "", group: str = "", q: str = "", include_missing: bool = False,
               limit: int = 0) -> list[dict]:
    sql, a = "SELECT * FROM review_items WHERE 1=1", []
    if kind:
        sql += " AND kind=?"
        a.append(kind)
    if status == "forward":
        sql += " AND forward=1"
    elif status == "commented":
        sql += " AND comment<>''"
    elif status:
        sql += " AND status=?"
        a.append(status)
    if group:
        sql += " AND grp=?"
        a.append(group)
    if q:
        sql += " AND (title LIKE ? OR comment LIKE ? OR tags LIKE ? OR rel_path LIKE ?)"
        a += [f"%{q}%"] * 4
    if not include_missing:
        sql += " AND missing=0"
    sql += " ORDER BY kind, grp, rel_path"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [_out(r) for r in db.rows(con.execute(sql, a))]


def summary(con) -> dict:
    kinds: dict[str, dict] = {}
    for r in con.execute("SELECT kind, grp, status, forward, comment, missing FROM review_items"):
        if r["missing"]:
            continue
        k = kinds.setdefault(r["kind"], {"kind": r["kind"], "label": KIND_LABELS.get(r["kind"], r["kind"]), "total": 0,
                                         "by_status": {s: 0 for s in STATUSES}, "forward": 0, "groups": {}})
        g = k["groups"].setdefault(r["grp"], {"group": r["grp"], "total": 0, "by_status": {s: 0 for s in STATUSES}, "forward": 0})
        for x in (k, g):
            x["total"] += 1
            x["by_status"][r["status"]] = x["by_status"].get(r["status"], 0) + 1
            x["forward"] += r["forward"]
    gc = {r["id"]: db.row(r) for r in con.execute("SELECT * FROM review_groups")}
    out = []
    for k in kinds.values():
        groups = []
        for g in sorted(k["groups"].values(), key=lambda x: x["group"]):
            row = gc.get(group_id(k["kind"], g["group"])) or {}
            groups.append({**g, "id": group_id(k["kind"], g["group"]), "comment": row.get("comment") or "",
                           "group_forward": bool(row.get("forward")), "handled_note": row.get("handled_note")})
        out.append({**k, "groups": groups})
    order = list(KIND_LABELS)
    out.sort(key=lambda k: order.index(k["kind"]) if k["kind"] in order else 99)
    pending = con.execute("SELECT count(*) FROM review_items WHERE forward=1").fetchone()[0] + \
        con.execute("SELECT count(*) FROM review_groups WHERE forward=1").fetchone()[0]
    return {"kinds": out, "pending_for_claude": pending, "statuses": list(STATUSES), "kind_labels": KIND_LABELS}


# ------------------------------------------------------------------ owner decisions
def decide(con, ids: list[str], status: str | None = None, comment: str | None = None, tags: list[str] | None = None,
           forward: bool | None = None, actor: str = "owner") -> dict:
    if status is not None and status not in STATUSES:
        raise ValueError(f"статус должен быть одним из {STATUSES}")
    if not ids:
        raise ValueError("не выбрано ни одного элемента")
    now = db.now()
    done = []
    with db.tx(con):
        for iid in dict.fromkeys(ids):
            it = get_item(con, iid)
            if not it:
                raise ValueError(f"нет элемента {iid}")
            sets, a, ch = [], [], {}
            if status is not None and status != it["status"]:
                sets.append("status=?"); a.append(status); ch["status"] = [it["status"], status]
            if comment is not None and comment != (it["comment"] or ""):
                sets.append("comment=?"); a.append(comment.strip()); ch["comment"] = comment.strip()
            if tags is not None:
                t = sorted({x.strip() for x in tags if x and x.strip()})
                if t != (it["tags"] or []):
                    sets.append("tags=?"); a.append(db.j(t)); ch["tags"] = t
            if forward is not None and bool(forward) != bool(it["forward"]):
                sets.append("forward=?"); a.append(1 if forward else 0); ch["forward"] = bool(forward)
                if forward:
                    sets.append("forward_at=?"); a.append(now)
            if not sets:
                continue
            sets.append("decided_at=?"); a.append(now)
            con.execute(f"UPDATE review_items SET {', '.join(sets)} WHERE id=?", (*a, iid))
            _event(con, iid, "decide", ch, actor)
            done.append(iid)
    return {"ok": True, "changed": len(done), "ids": done}


def group_comment(con, kind: str, grp: str, comment: str | None = None, forward: bool | None = None) -> dict:
    gid = group_id(kind, grp)
    if not con.execute("SELECT 1 FROM review_items WHERE kind=? AND grp=? LIMIT 1", (kind, grp)).fetchone():
        raise ValueError(f"нет группы {kind}/{grp}")
    now = db.now()
    with db.tx(con):
        con.execute("INSERT OR IGNORE INTO review_groups(id, kind, grp) VALUES (?,?,?)", (gid, kind, grp))
        if comment is not None:
            con.execute("UPDATE review_groups SET comment=?, decided_at=? WHERE id=?", (comment.strip(), now, gid))
        if forward is not None:
            con.execute("UPDATE review_groups SET forward=?, forward_at=CASE WHEN ? THEN ? ELSE forward_at END, decided_at=? WHERE id=?",
                        (1 if forward else 0, 1 if forward else 0, now, now, gid))
        _event(con, gid, "group", {"comment": comment, "forward": forward})
    return db.row(con.execute("SELECT * FROM review_groups WHERE id=?", (gid,)).fetchone())


def mark_handled(con, iid: str, note: str, actor: str = "claude") -> dict:
    """Claude закрыл запрос: forward снимается, статус владельца не меняется (решает он)."""
    if not (note or "").strip():
        raise ValueError("нужна заметка: что сделано")
    now = db.now()
    with db.tx(con):
        if iid.startswith("rg:"):
            if not con.execute("SELECT 1 FROM review_groups WHERE id=?", (iid,)).fetchone():
                raise ValueError(f"нет группы {iid}")
            con.execute("UPDATE review_groups SET forward=0, handled_at=?, handled_note=? WHERE id=?", (now, note.strip(), iid))
        else:
            if not get_item(con, iid):
                raise ValueError(f"нет элемента {iid}")
            con.execute("UPDATE review_items SET forward=0, handled_at=?, handled_note=? WHERE id=?", (now, note.strip(), iid))
        _event(con, iid, "handled", {"note": note.strip()}, actor)
    return {"ok": True, "id": iid, "handled_at": now}


def feedback(con, since: str | None = None, include_handled: bool = False) -> dict:
    """Решения владельца для Claude: открытые запросы (forward) + все решения/комментарии (после since)."""
    req = [_out(r) for r in db.rows(con.execute("SELECT * FROM review_items WHERE forward=1 ORDER BY forward_at"))]
    greq = db.rows(con.execute("SELECT * FROM review_groups WHERE forward=1 ORDER BY forward_at"))
    sql = "SELECT * FROM review_items WHERE (status<>'new' OR comment<>'' OR tags<>'[]')"
    a: list = []
    if since:
        sql += " AND decided_at > ?"
        a.append(since)
    if not include_handled:
        sql += " AND (handled_at IS NULL OR decided_at > handled_at OR forward=1)"
    decisions = [_out(r) for r in db.rows(con.execute(sql + " ORDER BY decided_at", a))]
    gsql = "SELECT * FROM review_groups WHERE comment<>''" + (" AND decided_at > ?" if since else "")
    groups = db.rows(con.execute(gsql, [since] if since else []))
    counts = {f"{r['kind']}:{r['status']}": r["n"] for r in con.execute(
        "SELECT kind, status, count(*) n FROM review_items WHERE missing=0 GROUP BY kind, status")}
    return {
        "note": ("Решения и комментарии владельца из «Ревью». Комментарий — слова владельца о том, что не так; это запрос к Claude, "
                 "а не правило студии. Статус меняет только владелец. Выполнив запрос, вызовите mark_review_handled."),
        "now": db.now(), "since": since,
        "pending_requests": req,
        "pending_group_requests": [{k: g[k] for k in ("id", "kind", "grp", "comment", "forward_at")} for g in greq],
        "decisions": decisions,
        "group_comments": [{k: g[k] for k in ("id", "kind", "grp", "comment", "decided_at", "handled_at", "handled_note")} for g in groups],
        "counts": counts,
    }


# ------------------------------------------------------------------ files
def thumb(con, iid: str) -> Path | None:
    it = get_item(con, iid)
    if not it:
        return None
    src = abs_path(it)
    if not src.is_file():
        return None
    ext = it["ext"]
    d = config.DATA_DIR / "cache" / "review-thumbs"
    d.mkdir(parents=True, exist_ok=True)
    stamp = int((it["mtime"] or 0) * 1000)
    if ext in IMAGE_EXT:
        out = d / f"{iid}-{stamp}.png"
        if not out.exists():
            from PIL import Image
            with Image.open(src) as im:
                im.thumbnail((THUMB_W, THUMB_W))
                im.save(out, "PNG", optimize=True)
        return out
    if ext in VIDEO_EXT:
        out = d / f"{iid}-{stamp}.jpg"
        if not out.exists():
            ok = media.extract_frame(src, 1.0, out, width=THUMB_W) and out.exists()
            if not ok and not media.extract_frame(src, 0.0, out, width=THUMB_W):
                return None
        return out if out.exists() else None
    return None


def _trash(p: Path) -> None:
    try:
        from Foundation import NSURL, NSFileManager
        ok, _url, err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            NSURL.fileURLWithPath_(str(p)), None, None)
        if ok:
            return
        raise RuntimeError(str(err))
    except ImportError:
        r = subprocess.run(["osascript", "-e", f'tell application "Finder" to delete (POSIX file "{p}")'],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "Finder не переместил файл в Корзину")


def trash_item(con, iid: str, confirm: bool) -> dict:
    """Переместить файл в Корзину macOS (восстанавливается из Корзины). Только с confirm=True. Никогда не удаляет."""
    if not confirm:
        raise ValueError("нужно подтверждение (confirm=true)")
    it = get_item(con, iid)
    if not it:
        raise ValueError(f"нет элемента {iid}")
    p = abs_path(it)
    if not p.is_file():
        raise ValueError("файла уже нет на месте")
    _trash(p)
    now = db.now()
    with db.tx(con):
        con.execute("UPDATE review_items SET trashed_at=?, missing=1, status='rejected', "
                    "decided_at=? WHERE id=?", (now, now, iid))
        _event(con, iid, "trash", {"path": it["rel_path"]})
    return {"ok": True, "id": iid, "trashed": str(p), "note": "Файл в Корзине macOS — его можно вернуть оттуда."}
