"""Coverage report per source, generated from stored data (not hand-written)."""
from __future__ import annotations

import json

from . import config, coverage, db


def _fmt(t):
    m, s = divmod(float(t), 60)
    return f"{int(m):02d}:{s:04.1f}"


def source_report(con, sid: str) -> str:
    s = db.row(con.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone())
    cov = coverage.build(con, sid)
    an = con.execute("SELECT data, version, origin FROM analyses WHERE source_id=? ORDER BY version DESC LIMIT 1", (sid,)).fetchone()
    d = json.loads(an["data"]) if an else {}
    ac = d.get("coverage", {})
    rows = [("Длительность", f"{s['duration']:.1f} с" if s.get("duration") else "—")]
    sp = cov.get("speech")
    if sp:
        tot = sum(b - a for a, b in sp["intervals"])
        unc = "; ".join(f"{_fmt(a)}–{_fmt(b)}" for a, b in sp["unclear_intervals"]) or "нет"
        rows.append(("Речь", f"ASR всего звука ({sp['method'].split(':')[-1]}, {sp['language']}); речь найдена в {tot:.1f} с; сомнительные: {unc}"
                     + ("; проверены в разборе" if ac.get("unclear_speech_checked") else "")))
    v = cov.get("visual") or {}
    viewed = ac.get("frames_viewed", [])
    grid = [f for f in viewed if not f.startswith("frames/d") and f.startswith("frames/")]
    dense = sorted({f for f in viewed if f.startswith("frames/d")})
    dp = v.get("dense_passes") or []
    rows.append(("Кадры (извлечено)", f"{v.get('frames_sampled', 0)} по выборке ({v.get('method', '—')}), макс. разрыв {v.get('max_gap_between_frames_s')} с"
                 + (f"; плотные проходы: " + ", ".join(f"{_fmt(p['interval'][0])}–{_fmt(p['interval'][1])} шаг {p['step']} с" for p in dp) if dp else "")))
    rows.append(("Кадры (просмотрено в разборе)", f"{len(grid)} выборочных + {len(dense)} плотных"
                 + (f"; плотные: {_fmt(int(dense[0][8:14]) / 1000)}…{_fmt(int(dense[-1][8:14]) / 1000)}" if dense else "")))
    ed = d.get("editing", {})
    rows.append(("Движение/переходы/вставки", (ed.get("camera_movement") or "—")[:400]))
    au = cov.get("audio") or {}
    rows.append(("Аудио", "; ".join(filter(None, [
        "сигнал: громкость/паузы" if au.get("method") else None,
        f"звуковая карта AST+librosa ({au['sound_map']['segments']} сегм., оценки)" if au.get("sound_map") else "звуковой карты нет",
        "учтена в разборе" if ac.get("audio_analyzed_by_model") else "в разборе НЕ учтена",
        "прослушивание человеком/Claude: нет"]))))
    rows.append(("Осталось неизвестным", "; ".join(ac.get("not_covered", [])) or "—"))
    weak = []
    for k in con.execute("SELECT id, title, evidence, tags FROM knowledge WHERE status='active' AND id IN "
                         "(SELECT knowledge_id FROM knowledge_refs WHERE source_id=?)", (sid,)):
        tags = json.loads(k["tags"] or "[]")
        if k["evidence"] == "hypothesis" or "needs-review" in tags:
            weak.append(f"`{k['id']}` {k['title']} ({'гипотеза' if k['evidence'] == 'hypothesis' else 'требует проверки'})")
    rows.append(("Карточки на неполном анализе", "<br>".join(weak) or "нет помеченных"))
    rows.append(("Статус разбора", s["st_analysis"]))
    L = [f"### {s['title']} (`{sid}`)", "", "| Показатель | Факт |", "|---|---|"]
    L += [f"| {a} | {str(b).replace('|', '/')} |" for a, b in rows]
    return "\n".join(L) + "\n"


def all_reports(con) -> str:
    out = ["# Покрытие материалов", "", f"Сгенерировано {db.now()} командой `./studio coverage-report` из базы.", ""]
    for r in con.execute("SELECT id FROM sources WHERE rel_path IS NOT NULL AND is_test=0 ORDER BY title"):
        out.append(source_report(con, r["id"]))
    return "\n".join(out)
