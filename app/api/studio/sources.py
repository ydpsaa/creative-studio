"""Source registry: import of files, URLs, accounts, notes and archives."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
import zipfile
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import config, db, jobs, media

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".tif", ".tiff"}
DOC_EXT = {".txt", ".md", ".srt", ".vtt", ".json", ".csv", ".pdf", ".docx"}
MAX_ZIP_FILES, MAX_ZIP_BYTES = 2000, 20 * 1024**3


def kind_for(p: Path) -> str:
    e = p.suffix.lower()
    if e in VIDEO_EXT:
        return "video"
    if e in AUDIO_EXT:
        return "audio"
    if e in IMAGE_EXT:
        return "image"
    if e in DOC_EXT:
        return "document"
    if e == ".zip":
        return "archive"
    return "other"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name: str) -> str:
    name = unicodedata.normalize("NFC", Path(name).name)
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name).strip(" .") or "file"
    return name[:150]


def normalize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    u = urlsplit(url)
    host = u.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = re.sub(r"/+$", "", u.path) + "/" if "instagram.com" in host else u.path
    if host.endswith("instagram.com") or host.endswith("tiktok.com"):
        q = ""            # share/tracking params only
    else:
        q = urlencode([(k, v) for k, v in parse_qsl(u.query) if not k.lower().startswith(("utm_", "fbclid", "igsh", "si"))])
    return urlunsplit(("https", host, path, q, ""))


def platform_of(url: str) -> str:
    h = urlsplit(url).netloc
    for k, v in (("instagram", "Instagram"), ("tiktok", "TikTok"), ("youtu", "YouTube"), ("t.me", "Telegram"),
                 ("facebook", "Facebook"), ("x.com", "X"), ("twitter", "X")):
        if k in h:
            return v
    return h or "неизвестно"


def get(con, sid: str) -> dict | None:
    return db.row(con.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone())


def _add_link(con, sid, url=None, via=None, note=None):
    if con.execute("SELECT 1 FROM source_links WHERE source_id=? AND IFNULL(url,'')=IFNULL(?,'') AND IFNULL(via,'')=IFNULL(?,'')",
                   (sid, url, via)).fetchone():
        return
    con.execute("INSERT INTO source_links(source_id,url,via,note,added_at) VALUES (?,?,?,?,?)",
                (sid, url, via, note, db.now()))


def _add_collections(con, sid, names):
    for n in names or []:
        n = n.strip()
        if not n:
            continue
        c = con.execute("SELECT id FROM collections WHERE name=?", (n,)).fetchone()
        cid = c["id"] if c else db.new_id("col")
        if not c:
            con.execute("INSERT INTO collections(id,name,created_at) VALUES (?,?,?)", (cid, n, db.now()))
        con.execute("INSERT OR IGNORE INTO source_collections VALUES (?,?)", (sid, cid))


META_FIELDS = ("title", "author", "platform", "published_at", "language", "purpose", "usage_status",
               "provenance", "brand_id", "notes")


def import_file(con, path: Path, meta: dict | None = None, *, move: bool = False, attach_to: str | None = None) -> dict:
    """Copy a file into originals (once per content hash) and queue processing.
    Returns {source, duplicate, created_jobs}."""
    meta = dict(meta or {})
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    k = kind_for(path)
    if k == "archive":
        return import_archive(con, path, meta)
    digest = sha256_file(path)
    dup = db.row(con.execute("SELECT * FROM sources WHERE sha256=? AND rel_path IS NOT NULL ORDER BY imported_at LIMIT 1",
                             (digest,)).fetchone())
    with db.tx(con):
        if dup:
            sid = dup["id"]
            _add_link(con, sid, meta.get("url"), meta.get("via") or "повторный импорт", f"файл: {path.name}")
            _add_collections(con, sid, meta.get("collections"))
            if attach_to and attach_to != sid:
                con.execute("INSERT OR IGNORE INTO source_relations VALUES (?,?,?,?)",
                            (attach_to, sid, "same_content", "Файл уже был в медиатеке"))
                con.execute("UPDATE sources SET st_acquire='merged', blocked_reason=NULL, next_action=?, notes=notes||? WHERE id=?",
                            (f"См. {sid}", f"\nСодержимое уже в медиатеке как {sid}", attach_to))
            created = jobs.enqueue_plan(con, dup)   # no-op for finished stages (same keys)
            res = {"source": get(con, sid), "duplicate": True, "created_jobs": created}
        else:
            if attach_to:
                sid = attach_to
                base = get(con, sid)
                if not base:
                    raise KeyError(sid)
                if base.get("rel_path"):
                    raise ValueError("У карточки уже есть файл; новая версия создаётся отдельным импортом")
            else:
                sid = db.new_id("src")
            dest_dir = config.DATA_DIR / "originals" / sid
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / safe_name(path.name)
            tmp = dest.with_suffix(dest.suffix + ".part")
            (shutil.move if move else shutil.copy2)(str(path), str(tmp))
            if sha256_file(tmp) != digest:
                tmp.unlink(missing_ok=True)
                raise IOError("Хеш копии не совпал с исходником")
            tmp.replace(dest)
            dest.chmod(0o444)
            fields = {"kind": k, "original_name": path.name, "rel_path": config.rel(dest), "size": dest.stat().st_size,
                      "mime": media.mime_for(dest), "sha256": digest, "st_acquire": "file",
                      "blocked_reason": None, "next_action": None}
            for f in META_FIELDS:
                if meta.get(f) is not None:
                    fields[f] = meta[f]
            if meta.get("is_test"):
                fields["is_test"] = 1
            fields.setdefault("title", path.stem)
            if attach_to:
                cols = ", ".join(f"{c}=?" for c in fields)
                con.execute(f"UPDATE sources SET {cols} WHERE id=?", (*fields.values(), sid))
            else:
                fields.update(id=sid, imported_at=db.now(), url=None)
                if meta.get("url"):
                    fields["platform"] = meta.get("platform") or platform_of(meta["url"])
                cols = ", ".join(fields)
                con.execute(f"INSERT INTO sources({cols}) VALUES ({','.join('?' * len(fields))})", tuple(fields.values()))
            _add_link(con, sid, meta.get("url"), meta.get("via") or "загрузка файла", f"файл: {path.name}")
            _add_collections(con, sid, meta.get("collections"))
            src = get(con, sid)
            db.index(con, "source", sid, src["title"] or "", " ".join(filter(None, [src.get("notes"), src.get("author"),
                                                                                  path.name, " ".join(src.get("tags") or [])])), sid=sid)
            created = jobs.enqueue_plan(con, src)
            res = {"source": get(con, sid), "duplicate": False, "created_jobs": created}
    write_manifest(con, res["source"]["id"])
    return res


def import_url(con, url: str, meta: dict | None = None) -> dict:
    meta = dict(meta or {})
    raw = url.strip()
    if "/" not in raw:
        raise ValueError("Не похоже на ссылку")
    norm = normalize_url(raw)
    acct = re.match(r"^https://instagram\.com/([A-Za-z0-9_.]+)/$", norm)
    if acct and acct.group(1) not in ("reel", "p", "reels", "stories", "explore"):
        return import_account(con, norm, acct.group(1), meta)
    with db.tx(con):
        ex = con.execute("SELECT * FROM sources WHERE url=?", (norm,)).fetchone() or \
            con.execute("SELECT s.* FROM sources s JOIN source_links l ON l.source_id=s.id WHERE l.url=?", (norm,)).fetchone()
        if ex:
            _add_link(con, ex["id"], raw, meta.get("via") or "повторная ссылка")
            _add_collections(con, ex["id"], meta.get("collections"))
            return {"source": get(con, ex["id"]), "duplicate": True, "created_jobs": []}
        sid = db.new_id("src")
        fields = {"id": sid, "kind": "url", "url": norm, "title": meta.get("title") or norm, "platform": platform_of(norm),
                  "imported_at": db.now(), "st_acquire": "queued", "is_test": 1 if meta.get("is_test") else 0}
        for f in META_FIELDS:
            if meta.get(f) is not None and f not in fields:
                fields[f] = meta[f]
        con.execute(f"INSERT INTO sources({', '.join(fields)}) VALUES ({','.join('?' * len(fields))})", tuple(fields.values()))
        _add_link(con, sid, raw, meta.get("via") or "ссылка от пользователя")
        _add_collections(con, sid, meta.get("collections"))
        db.index(con, "source", sid, fields["title"], norm + " " + (meta.get("notes") or ""), sid=sid)
        created = jobs.enqueue_plan(con, get(con, sid))
    write_manifest(con, sid)
    return {"source": get(con, sid), "duplicate": False, "created_jobs": created}


def import_account(con, url: str, handle: str, meta: dict) -> dict:
    with db.tx(con):
        ex = con.execute("SELECT * FROM accounts WHERE url=?", (url,)).fetchone()
        if ex:
            return {"account": dict(ex), "duplicate": True}
        aid = db.new_id("acc")
        con.execute("INSERT INTO accounts(id,platform,handle,url,snapshot_at,period,sample_note,status,notes) VALUES (?,?,?,?,?,?,?,?,?)",
                    (aid, platform_of(url), handle, url, None, "не задан пользователем",
                     "Предварительная выборка: до 20 релевантных публикаций (границы не заданы)",
                     "needs_post_list", "Список публикаций не получен: платформа не отдаёт ленту без входа. "
                     "Добавьте ссылки на публикации или файлы — они привяжутся к аккаунту."))
        db.index(con, "account", aid, f"@{handle}", url)
    return {"account": dict(con.execute("SELECT * FROM accounts WHERE id=?", (aid,)).fetchone()), "duplicate": False}


def link_account_post(con, account_id: str, source_id: str, studied: bool = False):
    con.execute("INSERT OR REPLACE INTO account_posts VALUES (?,?,?)", (account_id, source_id, int(studied)))


def add_note(con, title: str, text: str, meta: dict | None = None) -> dict:
    meta = dict(meta or {})
    sid = db.new_id("src")
    d = config.DATA_DIR / "originals" / sid
    d.mkdir(parents=True)
    p = d / "note.md"
    p.write_text(f"# {title}\n\n{text}\n", encoding="utf-8")
    with db.tx(con):
        con.execute("INSERT INTO sources(id,kind,title,original_name,rel_path,size,mime,sha256,imported_at,purpose,usage_status,"
                    "provenance,st_acquire,is_test,author) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, "document", title, "note.md", config.rel(p), p.stat().st_size, "text/markdown", sha256_file(p),
                     db.now(), meta.get("purpose", "мой материал"), "мой материал", "заметка пользователя в студии",
                     "file", 1 if meta.get("is_test") else 0, "пользователь"))
        db.index(con, "source", sid, title, text, sid=sid)
        jobs.enqueue_plan(con, get(con, sid))
    write_manifest(con, sid)
    return {"source": get(con, sid)}


def import_archive(con, path: Path, meta: dict) -> dict:
    if not zipfile.is_zipfile(path):
        raise ValueError("Поддерживаются только ZIP-архивы")
    tmp = config.DATA_DIR / "cache" / f"extract-{db.new_id('x')}"
    tmp.mkdir(parents=True)
    results, skipped = [], []
    try:
        with zipfile.ZipFile(path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if len(infos) > MAX_ZIP_FILES or sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
                raise ValueError("Архив слишком большой для безопасной распаковки")
            for i in infos:
                name = i.filename
                if name.startswith(("/", "\\")) or ".." in Path(name).parts or (i.external_attr >> 16) & 0o170000 == 0o120000:
                    skipped.append({"name": name, "reason": "небезопасный путь/ссылка"})
                    continue
                if "__MACOSX" in name or Path(name).name.startswith("."):
                    continue
                out = (tmp / name).resolve()
                if tmp.resolve() not in out.parents:
                    skipped.append({"name": name, "reason": "выход за папку"})
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                if kind_for(out) in ("other", "archive"):
                    skipped.append({"name": name, "reason": "формат не поддержан"})
                    continue
                m = dict(meta, via=f"архив: {path.name}", provenance=meta.get("provenance") or f"из архива {path.name}")
                m.pop("title", None)
                results.append(import_file(con, out, m))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"archive": path.name, "imported": results, "skipped": skipped}


def write_manifest(con, sid: str) -> None:
    s = get(con, sid)
    if not s:
        return
    d = config.DATA_DIR / "originals" / sid
    d.mkdir(parents=True, exist_ok=True)
    m = {"source": s,
         "links": db.rows(con.execute("SELECT url, via, note, added_at FROM source_links WHERE source_id=?", (sid,))),
         "collections": [r["name"] for r in con.execute(
             "SELECT c.name FROM collections c JOIN source_collections sc ON sc.collection_id=c.id WHERE sc.source_id=?", (sid,))],
         "relations": db.rows(con.execute("SELECT * FROM source_relations WHERE a=? OR b=?", (sid, sid))),
         "jobs": db.rows(con.execute("SELECT stage, state, run_id, attempts, note, error, finished_at FROM jobs WHERE source_id=?", (sid,))),
         "written_at": db.now()}
    tmp = d / "manifest.json.tmp"
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "manifest.json")


def update_meta(con, sid: str, fields: dict) -> dict:
    allowed = set(META_FIELDS) | {"tags"}
    upd = {k: (db.j(v) if k == "tags" else v) for k, v in fields.items() if k in allowed}
    if upd:
        with db.tx(con):
            con.execute(f"UPDATE sources SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?", (*upd.values(), sid))
            s = get(con, sid)
            db.index(con, "source", sid, s["title"] or "", " ".join(filter(None, [s.get("notes"), s.get("author"), s.get("url"),
                                                                                " ".join(s.get("tags") or [])])), sid=sid)
    write_manifest(con, sid)
    return get(con, sid)
