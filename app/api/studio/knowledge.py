"""Knowledge cards: storage, versioning, search, Markdown/JSON export, INDEX and playbooks."""
from __future__ import annotations

import json
from pathlib import Path

from . import config, db

KINDS = {"rule": "Правило", "method": "Метод", "script_structure": "Структура сценария", "hook": "Hook",
         "editing_technique": "Монтажный приём", "visual_style": "Визуальный стиль", "sound_technique": "Звуковой приём",
         "template": "Шаблон", "anti_example": "Антипример", "workflow": "Рабочий процесс"}
EVIDENCE = {"author_explained": "Автор прямо объяснил", "observed": "Наблюдается в примере",
            "hypothesis": "Гипотеза анализа", "user_preference": "Предпочтение пользователя",
            "project_confirmed": "Подтверждено результатом проекта"}
FIELDS = ("title", "kind", "category", "statement", "task", "when_use", "when_not", "conditions", "steps", "example",
          "mistake", "evidence", "limitations", "brand_scope", "format_scope", "language", "tags", "status")
PLAYBOOKS = {
    "scripting": ("Написание сценария", {"scripting", "storytelling", "templates"}, None),
    "hooks": ("Построение hook", {"hooks"}, None),
    "storyboard": ("Раскадровка и постановка", {"cinematography", "storytelling"}, None),
    "editing": ("Монтаж", {"editing"}, None),
    "sound": ("Звук", {"sound"}, None),
    "visual": ("Визуальный стиль", {"visual-styles"}, None),
    "no-face": ("Производство без съёмки автора", None, {"voice-only", "no-face", "ai-visuals", "screen-recording"}),
    "workflows": ("Рабочие процессы и инструменты", {"workflows"}, None),
}


def get(con, kid: str) -> dict | None:
    k = db.row(con.execute("SELECT * FROM knowledge WHERE id=?", (kid,)).fetchone())
    if k:
        k["refs"] = db.rows(con.execute("SELECT * FROM knowledge_refs WHERE knowledge_id=? ORDER BY t_start", (kid,)))
        k["history"] = db.rows(con.execute("SELECT version, changed_at, note, author FROM knowledge_versions "
                                           "WHERE knowledge_id=? ORDER BY version DESC", (kid,)))
    return k


def _body(c: dict) -> str:
    steps = c.get("steps") or []
    return " ".join(filter(None, [c.get("statement"), c.get("task"), c.get("when_use"), c.get("when_not"),
                                  c.get("conditions"), " ".join(map(str, steps)), c.get("example"), c.get("mistake"),
                                  c.get("limitations"), " ".join(c.get("tags") or []), KINDS.get(c.get("kind"), "")]))


def insert(con, card: dict, origin: str, author: str) -> str:
    """Insert inside caller's transaction. Card already validated."""
    kid = card.get("id") or db.new_id("kn")
    t = db.now()
    con.execute(
        "INSERT INTO knowledge(id,title,kind,category,statement,task,when_use,when_not,conditions,steps,example,mistake,evidence,"
        "limitations,brand_scope,format_scope,language,tags,origin,status,version,created_at,updated_at,is_test) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (kid, card["title"], card["kind"], card["category"], card.get("statement"), card.get("task"), card.get("when_use"),
         card.get("when_not"), card.get("conditions"), db.j(card.get("steps") or []), card.get("example"),
         card.get("mistake"), card["evidence"], card.get("limitations"), card.get("brand_scope") or "all",
         card.get("format_scope"), card.get("language"), db.j(card.get("tags") or []), origin, "active", 1, t, t,
         1 if card.get("is_test") else 0))
    for r in card.get("refs") or []:
        con.execute("INSERT INTO knowledge_refs(knowledge_id,source_id,t_start,t_end,page,artifact_id,quote,note) VALUES (?,?,?,?,?,?,?,?)",
                    (kid, r["source_id"], r.get("t_start"), r.get("t_end"), r.get("page"), r.get("artifact_id"),
                     r.get("quote"), r.get("frame")))
    con.execute("INSERT INTO knowledge_versions VALUES (?,?,?,?,?,?)",
                (kid, 1, db.j({k: card.get(k) for k in FIELDS} | {"refs": card.get("refs")}), t, "создано", author))
    db.index(con, "knowledge", kid, card["title"], _body(card))
    return kid


def revise(con, kid: str, card: dict, note: str, author: str) -> None:
    """Revise a card in place from a newer analysis (inside caller's transaction): new version, refs replaced, history kept."""
    cur = get(con, kid)
    newv = cur["version"] + 1
    vals = {k: card.get(k) for k in FIELDS if k in card and k != "status"}
    vals = {k: (db.j(v) if k in ("steps", "tags") else v) for k, v in vals.items()}
    con.execute(f"UPDATE knowledge SET {', '.join(f'{k}=?' for k in vals)}, version=?, updated_at=?, status='active' WHERE id=?",
                (*vals.values(), newv, db.now(), kid))
    con.execute("DELETE FROM knowledge_refs WHERE knowledge_id=?", (kid,))
    for r in card.get("refs") or []:
        con.execute("INSERT INTO knowledge_refs(knowledge_id,source_id,t_start,t_end,page,artifact_id,quote,note) VALUES (?,?,?,?,?,?,?,?)",
                    (kid, r["source_id"], r.get("t_start"), r.get("t_end"), r.get("page"), r.get("artifact_id"), r.get("quote"), r.get("frame")))
    merged = {**{k: cur.get(k) for k in FIELDS}, **{k: card.get(k) for k in FIELDS if k in card}}
    con.execute("INSERT INTO knowledge_versions VALUES (?,?,?,?,?,?)",
                (kid, newv, db.j(merged | {"refs": card.get("refs")}), db.now(), note, author))
    db.index(con, "knowledge", kid, merged["title"], _body(merged))


def update(con, kid: str, changes: dict, note: str, author: str = "user", base_version: int | None = None) -> dict:
    cur = get(con, kid)
    if not cur:
        raise KeyError(kid)
    if base_version is not None and base_version != cur["version"]:
        return {"ok": False, "conflict": True,
                "error": f"Карточка изменилась: в базе версия {cur['version']}, правка сделана от {base_version}"}
    upd = {k: v for k, v in changes.items() if k in FIELDS and k != "evidence" or (k == "evidence" and v in EVIDENCE)}
    if not upd:
        return {"ok": True, "unchanged": True, "card": cur}
    newv = cur["version"] + 1
    with db.tx(con):
        vals = [db.j(v) if k in ("steps", "tags") else v for k, v in upd.items()]
        con.execute(f"UPDATE knowledge SET {', '.join(f'{k}=?' for k in upd)}, version=?, updated_at=? WHERE id=?",
                    (*vals, newv, db.now(), kid))
        merged = {**{k: cur.get(k) for k in FIELDS}, **upd}
        con.execute("INSERT INTO knowledge_versions VALUES (?,?,?,?,?,?)",
                    (kid, newv, db.j(merged | {"refs": cur["refs"]}), db.now(), note or "правка", author))
        db.index(con, "knowledge", kid, merged["title"], _body(merged))
    export_card(con, kid)
    export_index(con)
    return {"ok": True, "card": get(con, kid)}


def import_card_file(con, path: Path) -> dict:
    """Accept an externally edited card JSON export, only if it was edited from the current version."""
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("schema") != "knowledge-card/1":
        return {"ok": False, "error": "Ожидается JSON-экспорт карточки (schema knowledge-card/1)"}
    return update(con, d["id"], d, note=d.get("change_note") or f"импорт файла {path.name}", author="user:file",
                  base_version=d.get("version"))


# ------------------------------------------------------------------ search
def search(con, q: str, etypes: list[str] | None = None, limit: int = 50, include_test: bool = False) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    terms = [t for t in q.replace('"', " ").split() if t]
    match = " ".join(f'"{t}"*' for t in terms)
    sql = ("SELECT etype, eid, sid, t, title, snippet(search, 5, '[', ']', '…', 14) AS snip, bm25(search) AS score "
           "FROM search WHERE search MATCH ?")
    args: list = [match]
    if etypes:
        sql += f" AND etype IN ({','.join('?' * len(etypes))})"
        args += etypes
    sql += " ORDER BY score LIMIT ?"
    args.append(limit * 2)
    out = []
    for r in con.execute(sql, args):
        d = dict(r)
        if not include_test:
            if d["etype"] == "knowledge":
                k = con.execute("SELECT is_test, status FROM knowledge WHERE id=?", (d["eid"],)).fetchone()
                if not k or k["is_test"] or k["status"] != "active":
                    continue
            elif d["sid"]:
                s = con.execute("SELECT is_test FROM sources WHERE id=?", (d["sid"],)).fetchone()
                if s and s["is_test"]:
                    continue
        out.append(d)
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ export
def _md(c: dict, con) -> str:
    L = [f"# {c['title']}", "",
         f"- **ID:** `{c['id']}` · версия {c['version']} · обновлено {c['updated_at']}",
         f"- **Тип:** {KINDS.get(c['kind'], c['kind'])} · **категория:** {c['category']}",
         f"- **Обоснованность:** {EVIDENCE.get(c['evidence'], c['evidence'])}",
         f"- **Область:** бренд {c.get('brand_scope') or 'all'} · формат {c.get('format_scope') or '—'} · язык {c.get('language') or '—'}",
         f"- **Происхождение:** {c.get('origin')}", ""]
    for label, key in (("Формулировка", "statement"), ("Задача", "task"), ("Когда применять", "when_use"),
                       ("Когда не применять", "when_not"), ("Условия", "conditions")):
        if c.get(key):
            L += [f"## {label}", c[key], ""]
    if c.get("steps"):
        L += ["## Шаги"] + [f"{i}. {s}" for i, s in enumerate(c["steps"], 1)] + [""]
    for label, key in (("Пример", "example"), ("Типичная ошибка", "mistake"), ("Ограничения", "limitations")):
        if c.get(key):
            L += [f"## {label}", c[key], ""]
    L += ["## Доказательства"]
    for r in c.get("refs") or []:
        s = con.execute("SELECT title FROM sources WHERE id=?", (r["source_id"],)).fetchone()
        where = f"{r['t_start']:.1f}–{r['t_end']:.1f} с" if r.get("t_start") is not None else (r.get("page") or "")
        L.append(f"- `{r['source_id']}` ({s['title'] if s else '?'}) · {where}"
                 + (f" · кадр `{r['note']}`" if r.get("note") else "") + (f" · «{r['quote']}»" if r.get("quote") else ""))
    L += ["", f"Тэги: {', '.join(c.get('tags') or [])}", ""]
    return "\n".join(L)


def export_card(con, kid: str) -> Path:
    c = get(con, kid)
    d = config.DATA_DIR / "knowledge" / c["category"]
    d.mkdir(parents=True, exist_ok=True)
    for old in config.DATA_DIR.joinpath("knowledge").glob(f"*/{kid}.*"):
        if old.parent != d:
            old.unlink()
    (d / f"{kid}.md").write_text(_md(c, con), encoding="utf-8")
    js = {"schema": "knowledge-card/1", **{k: c.get(k) for k in FIELDS}, "id": kid, "version": c["version"],
          "refs": c["refs"], "_note": "Чтобы принять правку этого файла: ./studio import-card <файл>. Поле version не меняйте."}
    (d / f"{kid}.json").write_text(json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")
    return d / f"{kid}.md"


def export_index(con) -> None:
    ks = db.rows(con.execute("SELECT * FROM knowledge WHERE status='active' AND is_test=0 ORDER BY category, kind, title"))
    L = ["# Библиотека знаний — INDEX", "",
         f"Сгенерировано {db.now()}. Источник истины — SQLite; этот файл — читаемый экспорт.", "",
         f"Карточек: {len(ks)}. Обозначения: A — автор объяснил, O — наблюдается, H — гипотеза, U — предпочтение пользователя, P — подтверждено проектом.", ""]
    ab = {"author_explained": "A", "observed": "O", "hypothesis": "H", "user_preference": "U", "project_confirmed": "P"}
    cat = None
    for k in ks:
        if k["category"] != cat:
            cat = k["category"]
            L += ["", f"## {cat}", ""]
        srcs = ", ".join(sorted({r["source_id"] for r in con.execute("SELECT source_id FROM knowledge_refs WHERE knowledge_id=?", (k['id'],))}))
        L.append(f"- [{ab.get(k['evidence'], '?')}] [{k['title']}]({k['category']}/{k['id']}.md) — {KINDS.get(k['kind'])} · `{k['id']}` · {srcs}")
    L += ["", "## Playbooks", ""] + [f"- [{t}](playbooks/{key}.md)" for key, (t, _, _) in PLAYBOOKS.items()]
    (config.DATA_DIR / "knowledge" / "INDEX.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def export_playbooks(con) -> None:
    d = config.DATA_DIR / "knowledge" / "playbooks"
    d.mkdir(parents=True, exist_ok=True)
    ks = db.rows(con.execute("SELECT * FROM knowledge WHERE status='active' AND is_test=0 ORDER BY evidence, title"))
    for key, (title, cats, tags) in PLAYBOOKS.items():
        sel = [k for k in ks if (cats and k["category"] in cats) or (tags and set(k.get("tags") or []) & tags)]
        L = [f"# Playbook: {title}", "", f"Сгенерировано {db.now()} из карточек знаний. Каждый пункт ведёт к карточке и её источникам.",
             "Уровень обоснованности указан в скобках; гипотезы не являются инструкциями авторов.", ""]
        if not sel:
            L.append("_Пока нет карточек по этой теме._")
        groups: dict[str, list] = {}
        for k in sel:
            groups.setdefault(EVIDENCE.get(k["evidence"], k["evidence"]), []).append(k)
        for g, items in groups.items():
            L += [f"## {g}", ""]
            for k in items:
                L.append(f"- **{k['title']}** — {k.get('statement') or ''} ([`{k['id']}`](../{k['category']}/{k['id']}.md))")
                if k.get("when_not"):
                    L.append(f"  - не применять: {k['when_not']}")
            L.append("")
        (d / f"{key}.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def export_all(con) -> dict:
    ids = [r["id"] for r in con.execute("SELECT id FROM knowledge")]
    for kid in ids:
        export_card(con, kid)
    export_index(con)
    export_playbooks(con)
    return {"cards": len(ids)}
