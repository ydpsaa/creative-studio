"""Single verified import path for analysis results (worker AI adapters, manual Claude answers, dev-time analysis).
A result counts as integrated knowledge only after passing this import."""
from __future__ import annotations

import json

from . import config, coverage, db, knowledge

CATEGORIES = set(config.KNOWLEDGE_CATEGORIES)
AI_EVIDENCE = {"author_explained", "observed", "hypothesis"}   # AI may not claim user/project evidence


def _frames(con, sid: str) -> dict[str, dict]:
    """file name (as in frames.json, e.g. frames/f003_0003.00.jpg) -> artifact"""
    out = {}
    for a in con.execute("SELECT id, rel_path, t FROM artifacts WHERE source_id=? AND kind='frame'", (sid,)):
        out[a["rel_path"].split("/", 3)[-1]] = dict(a)          # derived/<sid>/<run>/frames/x.jpg -> frames/x.jpg
    return out


def validate(con, sid: str, d: dict) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    src = db.row(con.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone())
    if not src:
        return [f"Источник {sid} не найден"], []
    if d.get("schema") != "analysis/1":
        errs.append("schema должен быть 'analysis/1'")
    if d.get("source_id") != sid:
        errs.append(f"source_id в ответе ({d.get('source_id')}) не совпадает с {sid}")
    for f in ("origin", "summary", "timeline", "coverage"):
        if f not in d:
            errs.append(f"нет поля {f}")
    if str(d.get("origin", "")).startswith("provider:") and not d.get("model"):
        errs.append("для provider:* нужно поле model")
    dur = float(src.get("duration") or 0)
    frames = _frames(con, sid)

    def chk_t(where, a, b=None):
        for v in (a, b):
            if v is None:
                continue
            if not isinstance(v, (int, float)) or v < 0 or (dur and v > dur + 0.5):
                errs.append(f"{where}: таймкод {v} вне 0…{dur}")
        if a is not None and b is not None and isinstance(a, (int, float)) and isinstance(b, (int, float)) and b < a:
            errs.append(f"{where}: конец {b} раньше начала {a}")

    def chk_ev(where, ev):
        for e in ev or []:
            if e.get("type") == "frame" and e.get("file") not in frames:
                errs.append(f"{where}: кадр {e.get('file')} не существует среди извлечённых кадров")
            if e.get("type") in ("transcript", "ocr"):
                chk_t(where, e.get("t"))

    for i, t in enumerate(d.get("timeline") or []):
        chk_t(f"timeline[{i}]", t.get("t_start"), t.get("t_end"))
        chk_ev(f"timeline[{i}]", t.get("evidence"))
    for i, o in enumerate(d.get("on_screen") or []):
        chk_t(f"on_screen[{i}]", o.get("t"))
        if o.get("frame") and o["frame"] not in frames:
            errs.append(f"on_screen[{i}]: кадр {o['frame']} не найден")
    for i, s in enumerate(((d.get("methodology") or {}).get("steps")) or []):
        chk_t(f"methodology.steps[{i}]", s.get("t_start"), s.get("t_end"))
    for k, v in (d.get("structure") or {}).items():
        if isinstance(v, dict):
            chk_t(f"structure.{k}", v.get("t_start"), v.get("t_end"))
    sheets = {a["rel_path"].rsplit("/", 1)[-1] for a in con.execute(
        "SELECT rel_path FROM artifacts WHERE source_id=? AND kind='contact_sheet'", (sid,))}
    for f in (d.get("coverage") or {}).get("frames_viewed", []):
        if f not in frames and f not in sheets:
            errs.append(f"coverage.frames_viewed: {f} не найден")
    for i, c in enumerate(d.get("knowledge") or []):
        w = f"knowledge[{i}] «{c.get('title', '')[:40]}»"
        for f in ("title", "kind", "category", "statement", "evidence"):
            if not c.get(f):
                errs.append(f"{w}: нет {f}")
        if c.get("kind") not in knowledge.KINDS:
            errs.append(f"{w}: неизвестный kind {c.get('kind')}")
        if c.get("category") not in CATEGORIES:
            errs.append(f"{w}: неизвестная category {c.get('category')}")
        if c.get("evidence") not in AI_EVIDENCE:
            errs.append(f"{w}: evidence {c.get('evidence')} недопустим для анализа (только author_explained|observed|hypothesis)")
        if c.get("id") and not con.execute("SELECT 1 FROM knowledge k JOIN knowledge_refs r ON r.knowledge_id=k.id "
                                           "WHERE k.id=? AND r.source_id=?", (c["id"], sid)).fetchone():
            errs.append(f"{w}: id {c['id']} не найден среди карточек этого источника")
        if not c.get("refs"):
            errs.append(f"{w}: нет ссылок на источник")
        for r in c.get("refs") or []:
            rs = r.get("source_id")
            if rs != sid and not con.execute("SELECT 1 FROM sources WHERE id=?", (rs,)).fetchone():
                errs.append(f"{w}: source_id {rs} не существует")
            if rs == sid:
                chk_t(w, r.get("t_start"), r.get("t_end"))
                if r.get("frame") and r["frame"] not in frames:
                    errs.append(f"{w}: кадр {r['frame']} не найден")
        if c.get("evidence") == "author_explained" and not any(r.get("quote") for r in c.get("refs") or []):
            warns.append(f"{w}: «автор объяснил» без цитаты — проверьте вручную")
    if (d.get("coverage") or {}).get("audio_listened") and str(d.get("origin")).startswith("claude"):
        warns.append("coverage.audio_listened=true заявлено для Claude-анализа: Claude в этой студии звук не прослушивает")
    return errs, warns


def import_analysis(con, sid: str, d: dict, dry_run: bool = False) -> dict:
    errs, warns = validate(con, sid, d)
    imp_id = db.new_id("imp")
    raw_dir = config.DATA_DIR / "analyses" / sid / "_imports"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{imp_id}.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    if errs or dry_run:
        con.execute("INSERT INTO imports VALUES (?,?,?,?,?,?,?)",
                    (imp_id, "analysis", sid, "rejected" if errs else "dry-run", db.j(errs + warns),
                     config.rel(raw_dir / f"{imp_id}.json"), db.now()))
        return {"ok": not errs, "dry_run": dry_run, "errors": errs, "warnings": warns}
    frames = _frames(con, sid)
    with db.tx(con):
        v = (con.execute("SELECT max(version) FROM analyses WHERE source_id=?", (sid,)).fetchone()[0] or 0) + 1
        aid = db.new_id("an")
        out = config.DATA_DIR / "analyses" / sid / f"v{v}"
        out.mkdir(parents=True, exist_ok=True)
        con.execute("INSERT INTO analyses(id,source_id,version,origin,provider,model,prompt_version,created_at,summary,data,validation,rel_dir) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (aid, sid, v, d["origin"], d.get("provider"), d.get("model"), d.get("prompt_version"), db.now(),
                     d["summary"], db.j(d), db.j({"warnings": warns}), config.rel(out)))
        # cards from the previous analysis version that nobody edited are superseded
        keep = [c["id"] for c in d.get("knowledge") or [] if c.get("id")]
        con.execute(f"UPDATE knowledge SET status='superseded' WHERE origin LIKE ? AND version=1"
                    f"{' AND id NOT IN (' + ','.join('?' * len(keep)) + ')' if keep else ''}",
                    (f"analysis:{sid}:v%", *keep))
        kids = []
        for c in d.get("knowledge") or []:
            for r in c.get("refs") or []:
                if r.get("frame") in frames:
                    r["artifact_id"] = frames[r["frame"]]["id"]
            if c.get("id"):
                knowledge.revise(con, c["id"], c, note=c.get("change_note") or f"пересмотр в разборе v{v}", author=d["origin"])
                kids.append(c["id"])
            else:
                kids.append(knowledge.insert(con, c, origin=f"analysis:{sid}:v{v}:{d['origin']}", author=d["origin"]))
        for i, t in enumerate(d.get("timeline") or []):
            db.index(con, "timeline", f"{aid}#{i}", f"{sid} {t.get('t_start')}–{t.get('t_end')}",
                     " ".join(str(t.get(k) or "") for k in ("speech", "visual", "audio", "teaches")), sid=sid, t=t.get("t_start"))
        db.index(con, "analysis", aid, f"Разбор {sid} v{v}", d["summary"] + " " + json.dumps(d.get("editing") or {}, ensure_ascii=False), sid=sid)
        upd = {"st_knowledge": f"{len(kids)} карточек", "stale": 0}
        if d.get("purpose"):
            upd["purpose"] = d["purpose"]
        con.execute(f"UPDATE sources SET {', '.join(f'{k}=?' for k in upd)} WHERE id=?", (*upd.values(), sid))
        con.execute("UPDATE sources SET st_visual='analyzed_sampled_frames' WHERE id=? AND kind='video'", (sid,))
        con.execute("INSERT INTO imports VALUES (?,?,?,?,?,?,?)",
                    (imp_id, "analysis", sid, "accepted", db.j(warns), config.rel(raw_dir / f"{imp_id}.json"), db.now()))
    _write_analysis_files(con, sid, v, d, out, kids)
    for k in kids:
        knowledge.export_card(con, k)
    knowledge.export_index(con)
    knowledge.export_playbooks(con)
    cov = coverage.write(con, sid)
    (out / "coverage.json").write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
    from . import sources
    sources.write_manifest(con, sid)
    return {"ok": True, "analysis_id": aid, "version": v, "knowledge_ids": kids, "warnings": warns, "dir": config.rel(out)}


def _ft(t):
    if t is None:
        return "—"
    m, s = divmod(float(t), 60)
    return f"{int(m):02d}:{s:05.2f}"


def _write_analysis_files(con, sid, v, d, out, kids):
    (out / "analysis.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    s = con.execute("SELECT title, duration FROM sources WHERE id=?", (sid,)).fetchone()
    L = [f"# Разбор: {s['title']} (`{sid}`), версия {v}", "",
         f"Происхождение: **{d['origin']}**{' · модель ' + d['model'] if d.get('model') else ''} · {db.now()}", "",
         "## Кратко", d["summary"], ""]
    st = d.get("structure") or {}
    if st:
        L += ["## Творческая конструкция", ""]
        names = {"hook": "Hook", "development": "Развитие", "proof": "Доказательство", "turn": "Эмоциональный поворот",
                 "ending": "Завершение", "cta": "CTA"}
        for k, n in names.items():
            x = st.get(k)
            L.append(f"- **{n}:** " + (f"{_ft(x.get('t_start'))}–{_ft(x.get('t_end'))} — {x.get('description')}" if isinstance(x, dict) else "отсутствует в ролике"))
        L.append("")
    m = d.get("methodology")
    if m:
        L += ["## Восстановленная методика", f"**Вход:** {m.get('inputs', '—')}", ""]
        for i, stp in enumerate(m.get("steps") or [], 1):
            L.append(f"{i}. [{_ft(stp.get('t_start'))}–{_ft(stp.get('t_end'))}] {stp.get('action')}"
                     + (f" — настройки: {stp['settings']}" if stp.get("settings") else ""))
        L += ["", f"**Результат:** {m.get('result', '—')}", f"**Ошибки/риски:** {m.get('mistakes', '—')}",
              f"**Ограничения:** {m.get('limitations', '—')}", ""]
    if d.get("on_screen"):
        L += ["## Информация на экране (отдельно от речи)", ""] + \
             [f"- {_ft(o.get('t'))} [{o.get('kind')}] {o.get('text')}" for o in d["on_screen"]] + [""]
    if d.get("editing"):
        L += ["## Монтаж и постановка", ""] + [f"- **{k}:** {v_}" for k, v_ in d["editing"].items()] + [""]
    if d.get("sound"):
        L += ["## Звук", ""] + [f"- {o}" for o in d["sound"].get("observations", [])] + \
             [f"- _Ограничение:_ {d['sound'].get('limitations')}", ""]
    L += ["## Временная шкала", "", "| Интервал | Говорится | Видно | Слышно | Чему учит | Доказательство |", "|---|---|---|---|---|---|"]
    for t in d.get("timeline") or []:
        ev = "; ".join(e.get("file") or f"{e.get('type')}@{e.get('t')}" for e in t.get("evidence") or [])
        cells = [f"{_ft(t.get('t_start'))}–{_ft(t.get('t_end'))}"] + [str(t.get(k) or "—").replace("|", "/").replace("\n", " ")
                                                                      for k in ("speech", "visual", "audio", "teaches")] + [ev]
        L.append("| " + " | ".join(cells) + " |")
    L += ["", "## Карточки знаний", ""] + [f"- `{k}`" for k in kids]
    cv = d.get("coverage") or {}
    L += ["", "## Покрытие этого разбора", f"- Просмотрено кадров: {len(cv.get('frames_viewed', []))}",
          f"- Транскрипт прочитан: {cv.get('transcript_read')}", f"- Звук прослушан: {cv.get('audio_listened')}",
          f"- Не покрыто: {'; '.join(cv.get('not_covered', [])) or '—'}"]
    if d.get("open_questions"):
        L += ["", "## Открытые вопросы"] + [f"- {q}" for q in d["open_questions"]]
    (out / "analysis.md").write_text("\n".join(L) + "\n", encoding="utf-8")
