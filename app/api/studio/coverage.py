"""coverage.json — what was actually processed, by which method, and what was not."""
from __future__ import annotations

import json

from . import config, db


def _load(con, sid: str, kind: str):
    a = con.execute("SELECT rel_path FROM artifacts WHERE source_id=? AND kind=? ORDER BY created_at DESC LIMIT 1",
                    (sid, kind)).fetchone()
    if not a:
        return None
    p = config.safe_path(a["rel_path"])
    return json.loads(p.read_text()) if p.exists() else None


def build(con, sid: str) -> dict:
    s = db.row(con.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone())
    dur = s.get("duration")
    cov: dict = {"source_id": sid, "generated_at": db.now(), "duration": dur, "kind": s["kind"],
                 "statuses": {k: s[k] for k in s if k.startswith("st_")}, "speech": None, "visual": None,
                 "ocr": None, "audio": None, "analysis": None, "gaps": []}
    tr = _load(con, sid, "transcript")
    if tr:
        segs = tr["segments"]
        cov["speech"] = {"method": tr["method"], "language": tr["language"],
                         "intervals": [[x["start"], x["end"]] for x in segs],
                         "unclear_intervals": [[x["start"], x["end"]] for x in segs if x["unclear"]],
                         "processed_interval": [0, dur], "note": "Весь звук пропущен через ASR; интервалы — где найдена речь"}
    elif s["kind"] in ("video", "audio"):
        cov["gaps"].append("Речь: транскрипт отсутствует")
    fr = _load(con, sid, "frames_index")
    if fr:
        ts = [f["t"] for f in fr["frames"]]
        maxgap = max((b - a for a, b in zip(ts, ts[1:])), default=None)
        cov["visual"] = {"method": fr["method"], "frames_sampled": len(ts), "frame_times": ts,
                         "max_gap_between_frames_s": round(maxgap, 2) if maxgap else None,
                         "scene_changes": len(fr.get("scene_changes", [])),
                         "note": "Выборка кадров. Это не просмотр каждого кадра."}
    elif s["kind"] == "video":
        cov["gaps"].append("Изображение: кадры не извлечены")
    oc = _load(con, sid, "ocr")
    if oc:
        cov["ocr"] = {"method": oc["method"], "frames": len(oc["frames"]), "text_spans": len(oc["text_spans"]),
                      "errors": sum(1 for f in oc["frames"] if "error" in f)}
    af = _load(con, sid, "audio_features")
    if af:
        cov["audio"] = {"method": af["method"], "is_estimate": True, "interval": [0, dur],
                        "listening": "не выполнялось (AI-модель не прослушивала звук; музыка/эффекты не идентифицированы автоматически)"}
    sm = _load(con, sid, "sound_map")
    if sm:
        cov["audio"] = cov.get("audio") or {}
        cov["audio"]["sound_map"] = {"method": sm["method"], "cells": len(sm["cells"]), "segments": len(sm["segments"]),
                                     "is_estimate": True, "listening": "модель AST слушала сигнал; человек/Claude — нет"}
    dense = [dict(r) for r in con.execute("SELECT meta FROM artifacts WHERE source_id=? AND kind='dense_index'", (sid,))]
    if dense:
        cov["visual"] = cov.get("visual") or {}
        cov["visual"]["dense_passes"] = [json.loads(r["meta"]) for r in dense]
    an = con.execute("SELECT * FROM analyses WHERE source_id=? ORDER BY version DESC LIMIT 1", (sid,)).fetchone()
    if an:
        d = json.loads(an["data"])
        cov["analysis"] = {"version": an["version"], "origin": an["origin"], "model": an["model"],
                           "frames_viewed": d.get("coverage", {}).get("frames_viewed", []),
                           "timeline_intervals": [[t["t_start"], t["t_end"]] for t in d.get("timeline", [])],
                           "not_covered": d.get("coverage", {}).get("not_covered", [])}
    else:
        cov["gaps"].append("Смысловой разбор (AI/ручной) ещё не импортирован")
    return cov


def analysis_status(con, sid: str, cov: dict) -> str | None:
    """Honest analysis status: 'completed' only if the analysis integrated every available extraction."""
    an = cov.get("analysis")
    if not an:
        return None
    d = json.loads(con.execute("SELECT data FROM analyses WHERE source_id=? ORDER BY version DESC LIMIT 1", (sid,)).fetchone()["data"])
    c = d.get("coverage") or {}
    missing = []
    has_sound = con.execute("SELECT 1 FROM artifacts WHERE source_id=? AND kind='sound_map'", (sid,)).fetchone()
    if has_sound and not c.get("audio_analyzed_by_model"):
        missing.append("звуковая карта не учтена")
    if cov.get("speech") and cov["speech"]["unclear_intervals"] and not c.get("unclear_speech_checked"):
        missing.append("сомнительная речь не проверена")
    tag = f"{an['origin']} v{an['version']}"
    if missing:
        return f"partial ({tag}: {'; '.join(missing)})"
    return f"completed ({tag}; визуал — выборка кадров, звук — оценки модели)"


def write(con, sid: str) -> dict:
    cov = build(con, sid)
    st = analysis_status(con, sid, cov)
    if st:
        con.execute("UPDATE sources SET st_analysis=? WHERE id=?", (st, sid))
        cov["statuses"]["st_analysis"] = st
    d = config.DATA_DIR / "analyses" / sid
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "coverage.json.tmp"
    tmp.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / "coverage.json")
    return cov
