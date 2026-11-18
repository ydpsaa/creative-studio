"""Consistent backup (SQLite online-backup API + file copy + manifest) and verified restore into a separate folder."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path

from . import config, db

INCLUDE = ["originals", "derived", "analyses", "knowledge", "brands", "projects"]
SKIP_SUFFIX = {".wav", ".part", ".tmp"}        # regenerable / incomplete


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def create(con, dest: Path | None = None) -> dict:
    root = (dest.expanduser().resolve() if dest else config.DATA_DIR / "backups")
    root.mkdir(parents=True, exist_ok=True)
    b = root / f"backup-{time.strftime('%Y%m%d-%H%M%S')}"
    (b / "database").mkdir(parents=True)
    # 1. DB snapshot through the backup API (consistent even while API/worker are writing)
    snap = sqlite3.connect(b / "database" / "studio.sqlite3")
    con.backup(snap)
    snap.close()
    # 2. files referenced at snapshot time
    files = []
    for d in INCLUDE:
        src = config.DATA_DIR / d
        if not src.exists():
            continue
        for f in src.rglob("*"):
            if not f.is_file() or f.suffix in SKIP_SUFFIX or "/.tmp-" in str(f):
                continue
            relp = f.relative_to(config.DATA_DIR)
            out = b / relp
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
            files.append({"path": str(relp), "bytes": f.stat().st_size, "sha256": _sha(out)})
    dbf = b / "database" / "studio.sqlite3"
    files.append({"path": "database/studio.sqlite3", "bytes": dbf.stat().st_size, "sha256": _sha(dbf)})
    man = {"backup": b.name, "created_at": db.now(), "data_dir": str(config.DATA_DIR), "schema_version":
           con.execute("PRAGMA user_version").fetchone()[0], "files": files,
           "excluded": "cache/, logs/, exports/ (пересобираются), *.wav (восстанавливаемая дорожка)",
           "note": "Копия на том же диске не защищает от потери диска — укажите внешний путь: ./studio backup --dest /Volumes/…"}
    (b / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"backup": str(b), "files": len(files), "bytes": sum(f["bytes"] for f in files)}


def restore_and_verify(backup_dir: Path, target: Path) -> dict:
    backup_dir, target = backup_dir.expanduser().resolve(), target.expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        return {"ok": False, "error": "Целевая папка не пуста"}
    man = json.loads((backup_dir / "manifest.json").read_text())
    problems = []
    for f in man["files"]:
        s = backup_dir / f["path"]
        t = target / f["path"]
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, t)
        if _sha(t) != f["sha256"]:
            problems.append(f"хеш не совпал: {f['path']}")
    c = sqlite3.connect(target / "database" / "studio.sqlite3")
    c.row_factory = sqlite3.Row
    integ = c.execute("PRAGMA integrity_check").fetchone()[0]
    missing = []
    for tbl, col in (("sources", "rel_path"), ("artifacts", "rel_path")):
        for r in c.execute(f"SELECT id, {col} p FROM {tbl} WHERE {col} IS NOT NULL"):
            if r["p"].endswith(".wav"):
                continue
            if not (target / r["p"]).exists():
                missing.append(f"{tbl}:{r['id']} → {r['p']}")
    orphan_refs = c.execute("SELECT count(*) FROM knowledge_refs r LEFT JOIN sources s ON s.id=r.source_id WHERE s.id IS NULL").fetchone()[0]
    counts = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in
              ("sources", "artifacts", "knowledge", "knowledge_refs", "projects", "project_docs", "analyses")}
    c.close()
    ok = not problems and integ == "ok" and not missing and orphan_refs == 0
    return {"ok": ok, "restored_to": str(target), "files": len(man["files"]), "hash_problems": problems,
            "integrity_check": integ, "missing_files": missing[:50], "missing_count": len(missing),
            "orphan_knowledge_refs": orphan_refs, "counts": counts}
