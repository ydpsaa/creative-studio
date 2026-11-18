"""Stage processors. Each takes (ctx) and writes ONLY into ctx.out (a temp run dir).
Returns StageResult; the job runner moves the dir into place and commits DB state atomically."""
from __future__ import annotations

import json
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path

from . import config, media

WHISPER_REPO = "mlx-community/whisper-large-v3-turbo"
# Phrases Whisper is known to produce on silence/music/tones; flagged, never deleted.
HALLUCINATIONS = {"thank you", "thanks for watching", "thank you for watching", "you", "bye",
                  "продолжение следует", "спасибо за просмотр", "субтитры сделал dimatorzok",
                  "редактор субтитров а.семкин корректор а.егорова", "дякую за перегляд"}


@dataclass
class Ctx:
    source: dict
    out: Path                        # temp run dir (absolute)
    run_id: str
    inputs: dict = field(default_factory=dict)  # stage -> final run dir (absolute) of completed deps

    @property
    def original(self) -> Path:
        return config.safe_path(self.source["rel_path"])


@dataclass
class StageResult:
    state: str = "completed"         # completed|partial|failed|blocked
    note: str = ""
    artifacts: list[dict] = field(default_factory=list)   # {kind, file(rel to run dir), t?, meta?}
    source_updates: dict = field(default_factory=dict)
    search: list[dict] = field(default_factory=list)      # {etype, eid, title, body, t}


def wjson(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fmt_t(t: float) -> str:
    m, s = divmod(max(t, 0), 60)
    return f"{int(m):02d}:{s:06.3f}"


# ---------------------------------------------------------------- video/audio
def probe(ctx: Ctx) -> StageResult:
    info = media.probe(ctx.original)
    wjson(ctx.out / "technical.json", info)
    v = [s for s in info["streams"] if s["type"] == "video"]
    a = [s for s in info["streams"] if s["type"] == "audio"]
    res = StageResult(artifacts=[{"kind": "technical", "file": "technical.json"}])
    upd = {"duration": info.get("duration")}
    if v:
        t = min(1.0, (info.get("duration") or 0) / 2)
        if media.extract_frame(ctx.original, t, ctx.out / "poster.jpg", width=360):
            res.artifacts.append({"kind": "poster", "file": "poster.jpg", "t": t})
    dec = info.get("decodable", {})
    bad = [k for k, val in dec.items() if val is not True]
    if bad:
        res.state = "partial"
        res.note = f"Не декодируется: {', '.join(bad)}"
    res.note = res.note or (
        f"{v[0]['width']}x{v[0]['height']} {v[0].get('fps') or '?'} fps, " if v else "") + \
        f"{info.get('duration')} с, аудио: {'есть' if a else 'нет'}"
    if not a:
        upd.update(st_speech="no_audio", st_audio="no_audio")
    res.source_updates = upd
    return res


def audio(ctx: Ctx) -> StageResult:
    out = ctx.out / "audio16k.wav"
    r = media.extract_audio(ctx.original, out)
    if r.returncode != 0 or not out.exists():
        return StageResult(state="failed", note="Не удалось извлечь звук: " + r.stderr[-300:])
    return StageResult(note="Общая аудиодорожка 16 кГц моно (это не отделение голоса от музыки)",
                       artifacts=[{"kind": "audio_track", "file": "audio16k.wav"}])


def transcribe(ctx: Ctx) -> StageResult:
    wav = ctx.inputs["audio"] / "audio16k.wav"
    import mlx_whisper
    import numpy as np
    with wave.open(str(wav)) as w:     # pass samples directly: mlx-whisper would otherwise shell out to `ffmpeg` on PATH
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    r = mlx_whisper.transcribe(pcm, path_or_hf_repo=WHISPER_REPO, word_timestamps=True,
                               condition_on_previous_text=False, verbose=None)
    audio_dur = len(pcm) / 16000
    segs = []
    for s in r.get("segments", []):
        reasons = []
        if s.get("avg_logprob", 0) < -1.0:
            reasons.append("низкая уверенность")
        if s.get("no_speech_prob", 0) > 0.6:
            reasons.append("вероятно не речь")
        if s.get("compression_ratio", 0) > 2.4:
            reasons.append("повторы")
        if s["end"] > audio_dur + 0.5:
            reasons.append("сегмент выходит за длительность аудио")
        n_words = len(s.get("words") or s["text"].split())
        if s["end"] - s["start"] > 3 and n_words / (s["end"] - s["start"]) < 0.3:
            reasons.append("слишком мало слов для длительности")
        if s["text"].strip().lower().strip(".!… ") in HALLUCINATIONS:
            reasons.append("типовая галлюцинация ASR")
        segs.append({
            "id": s["id"], "start": round(s["start"], 2), "end": round(min(s["end"], audio_dur), 2),
            "text": s["text"].strip(), "avg_logprob": round(s.get("avg_logprob", 0), 3),
            "no_speech_prob": round(s.get("no_speech_prob", 0), 3), "unclear": bool(reasons), "unclear_reasons": reasons,
            "words": [{"w": w["word"], "s": round(w["start"], 2), "e": round(w["end"], 2),
                       "p": round(w.get("probability", 0), 3)} for w in s.get("words", [])],
        })
    lang = r.get("language") or "неизвестно"
    data = {"kind": "original", "language": lang, "method": f"mlx-whisper:{WHISPER_REPO}",
            "note": "Автоматическое распознавание; не исправлено человеком", "segments": segs}
    wjson(ctx.out / "transcript.original.json", data)
    md = [f"# Транскрипт (исходный, {lang}, {data['method']})", ""]
    srt = []
    for i, s in enumerate(segs, 1):
        flag = f" ⚠ сомнительно: {', '.join(s['unclear_reasons'])}" if s["unclear"] else ""
        md.append(f"[{fmt_t(s['start'])}–{fmt_t(s['end'])}] {s['text']}{flag}")
        srt += [str(i), f"{_srt(s['start'])} --> {_srt(s['end'])}", s["text"], ""]
    (ctx.out / "transcript.original.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (ctx.out / "transcript.original.srt").write_text("\n".join(srt), encoding="utf-8")
    words = sum(len(s["words"]) for s in segs)
    speech_t = sum(s["end"] - s["start"] for s in segs)
    res = StageResult(artifacts=[{"kind": "transcript", "file": "transcript.original.json",
                                  "meta": {"language": lang, "segments": len(segs), "words": words}},
                                 {"kind": "transcript_md", "file": "transcript.original.md"},
                                 {"kind": "transcript_srt", "file": "transcript.original.srt"}])
    unclear = sum(1 for s in segs if s["unclear"])
    res.note = f"{lang}: {len(segs)} сегм., {words} слов, речь ~{speech_t:.1f} с, сомнительных {unclear}"
    if not segs:
        res.note = "Речь не обнаружена распознаванием"
        res.source_updates = {"st_speech": "no_speech"}
    else:
        res.source_updates = {"language": lang, "st_speech": "partial" if unclear else "completed"}
        if unclear:
            res.state = "partial"
    sid = ctx.source["id"]
    for s in segs:
        res.search.append({"etype": "segment", "eid": f"{sid}@{s['start']}", "title": ctx.source.get("title") or sid,
                           "body": s["text"], "t": s["start"]})
    return res


def _srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def frames(ctx: Ctx) -> StageResult:
    dur = float(ctx.source.get("duration") or 0)
    if dur <= 0:
        return StageResult(state="failed", note="Неизвестна длительность")
    step = 1.0 if dur <= 40 else 2.0 if dur <= 150 else 4.0
    scenes = media.scene_changes(ctx.original, 0.3)
    cand: list[tuple[float, str, float | None]] = [(0.0, "start", None)]
    cand += [(t, "scene_change", sc) for t, sc in scenes]
    # just after each cut: the settled frame of the new shot
    cand += [(min(t + 0.25, dur - 0.05), "after_cut", None) for t, _ in scenes]
    k = step
    while k < dur - 0.2:
        cand.append((round(k, 2), f"grid_{step:g}s", None))
        k += step
    cand.append((max(dur - 0.1, 0), "end", None))
    cand.sort()
    picked: list[tuple[float, str, float | None]] = []
    for t, why, sc in cand:
        if picked and t - picked[-1][0] < 0.35:
            if why == "scene_change":   # keep the reason that is more informative
                picked[-1] = (picked[-1][0], picked[-1][1] + "+scene_change", sc)
            continue
        picked.append((t, why, sc))
    fdir = ctx.out / "frames"
    fdir.mkdir()
    rec, arts = [], []
    for i, (t, why, sc) in enumerate(picked):
        name = f"f{i:03d}_{t:07.2f}.jpg"
        if media.extract_frame(ctx.original, t, fdir / name):
            rec.append({"i": i, "t": round(t, 3), "reason": why, "scene_score": sc, "file": f"frames/{name}"})
            arts.append({"kind": "frame", "file": f"frames/{name}", "t": round(t, 3), "meta": {"reason": why}})
    sheets = _contact_sheets(ctx.out, rec)
    wjson(ctx.out / "frames.json", {"method": f"scene>0.3 + after_cut + grid {step:g}s + start/end",
                                     "duration": dur, "scene_changes": scenes, "frames": rec,
                                     "note": "Выборка кадров, не просмотр каждого кадра"})
    arts.append({"kind": "frames_index", "file": "frames.json"})
    arts += [{"kind": "contact_sheet", "file": s} for s in sheets]
    st = "completed" if len(rec) == len(picked) else "partial"
    return StageResult(state=st, artifacts=arts,
                       note=f"{len(rec)} кадров ({len(scenes)} смен сцены, сетка {step:g} с)",
                       source_updates={"st_visual": "frames_extracted"})


def _contact_sheets(out: Path, rec: list[dict], per: int = 12, cols: int = 4) -> list[str]:
    from PIL import Image, ImageDraw
    res = []
    for n in range(0, len(rec), per):
        chunk = rec[n:n + per]
        ims = [Image.open(out / r["file"]) for r in chunk]
        w = 240
        h = int(ims[0].height * w / ims[0].width)
        rows_n = (len(ims) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * w, rows_n * (h + 22)), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        for i, (im, r) in enumerate(zip(ims, chunk)):
            x, y = (i % cols) * w, (i // cols) * (h + 22)
            sheet.paste(im.resize((w, h)), (x, y + 22))
            d.text((x + 4, y + 4), f"{fmt_t(r['t'])} {r['reason'][:18]}", fill=(120, 255, 140))
        name = f"contact_{n // per:02d}.jpg"
        sheet.save(out / name, quality=85)
        res.append(name)
    return res


def ocr(ctx: Ctx) -> StageResult:
    from ocrmac import ocrmac
    if ctx.source["kind"] == "image":
        items = [{"t": None, "file": None, "abs": ctx.original}]
    else:
        fr = json.loads((ctx.inputs["frames"] / "frames.json").read_text())
        items = [{"t": f["t"], "file": f["file"], "abs": ctx.inputs["frames"] / f["file"]} for f in fr["frames"]]
    out, failed = [], 0
    for it in items:
        try:
            ann = ocrmac.OCR(str(it["abs"]), recognition_level="accurate",
                             language_preference=["ru-RU", "uk-UA", "en-US"]).recognize()
            lines = [{"text": a[0], "conf": round(float(a[1]), 3), "bbox": [round(x, 3) for x in a[2]]} for a in ann]
            out.append({"t": it["t"], "frame": it["file"], "lines": lines})
        except Exception as e:  # noqa: BLE001
            failed += 1
            out.append({"t": it["t"], "frame": it["file"], "error": str(e)})
    # collapse repeated on-screen text into intervals
    spans, prev = [], None
    for o in out:
        txt = " | ".join(l["text"] for l in o.get("lines", []) if l["conf"] >= 0.3)
        if prev and txt == prev["text"]:
            prev["t_end"] = o["t"]
            continue
        prev = {"t_start": o["t"], "t_end": o["t"], "text": txt}
        if txt:
            spans.append(prev)
    wjson(ctx.out / "ocr.json", {"method": "Apple Vision (ocrmac, accurate, ru/uk/en)", "frames": out,
                                  "text_spans": spans,
                                  "note": "OCR по выбранным кадрам, не по каждому кадру видео"})
    md = ["# Текст на экране (OCR по выбранным кадрам)", ""]
    md += [f"[{fmt_t(s['t_start'] or 0)}–{fmt_t(s['t_end'] or 0)}] {s['text']}" for s in spans] or ["(текст не найден)"]
    (ctx.out / "ocr.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    sid = ctx.source["id"]
    res = StageResult(state="partial" if failed else "completed",
                      artifacts=[{"kind": "ocr", "file": "ocr.json", "meta": {"spans": len(spans)}},
                                 {"kind": "ocr_md", "file": "ocr.md"}],
                      note=f"{len(items)} кадров, {len(spans)} текстовых фрагментов" + (f", ошибок {failed}" if failed else ""))
    for s in spans:
        res.search.append({"etype": "ocr", "eid": f"{sid}@ocr{s['t_start']}", "title": ctx.source.get("title") or sid,
                           "body": s["text"], "t": s["t_start"]})
    if ctx.source["kind"] == "video":
        res.source_updates = {"st_visual": "frames+ocr"}
    return res


def audio_features(ctx: Ctx) -> StageResult:
    import numpy as np
    wav = ctx.inputs["audio"] / "audio16k.wav"
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    hop = int(sr * 0.1)
    n = len(x) // hop
    rms = np.array([np.sqrt(np.mean(x[i * hop:(i + 1) * hop] ** 2) + 1e-12) for i in range(n)])
    db = 20 * np.log10(rms + 1e-9)
    flux = np.maximum(np.diff(db, prepend=db[:1]), 0)
    thr = float(np.mean(flux) + 2.5 * np.std(flux))
    accents = [round(i * 0.1, 1) for i in range(1, n - 1)
               if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1] and db[i] > -40]
    data = {
        "method": "FFmpeg ebur128 + silencedetect(-35 dB, 0.3 s) + RMS 100 ms (numpy)",
        "is_estimate": True,
        "warning": "Сигнальные оценки. Не определяют наличие музыки/эффектов и не заменяют прослушивание.",
        "loudness": media.loudness(ctx.original),
        "silences": media.silences(ctx.original),
        "rms_db_100ms": [round(float(v), 1) for v in db],
        "candidate_accents_s": accents,
    }
    wjson(ctx.out / "audio_features.json", data)
    lu = data["loudness"].get("integrated_lufs")
    return StageResult(artifacts=[{"kind": "audio_features", "file": "audio_features.json"}],
                       note=f"Громкость {lu} LUFS, пауз {len(data['silences'])}, кандидатов акцентов {len(accents)} (оценки)",
                       source_updates={"st_audio": "signal_only"})


def sound_events(ctx: Ctx) -> StageResult:
    from . import sound
    wav = ctx.inputs["audio"] / "audio16k.wav"
    dur = float(ctx.source.get("duration") or 0)
    r = sound.analyze(wav, dur)
    wjson(ctx.out / "sound_map.json", r)
    md = ["# Звуковая карта (оценки AST + librosa)", "", f"Метод: {r['method']}", f"Темп (оценка): {r['tempo_estimate_bpm']} BPM", "",
          f"> {r['warning']}", "", "| Интервал | Слои | Метки AudioSet (вероятность) | Характер |", "|---|---|---|---|"]
    for g in r["segments"]:
        ch = g["character"]
        md.append(f"| {fmt_t(g['t_start'])}–{fmt_t(g['t_end'])} | {', '.join(g['layers'])} | "
                  + ", ".join(f"{x['label']} {x['p']:.2f}" for x in g["top_labels"]) + f" | {ch['texture']}, {ch['brightness']}, {ch['activity']} |")
    (ctx.out / "sound_map.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    arts = [{"kind": "sound_map", "file": "sound_map.json", "meta": {"segments": len(r["segments"])}},
            {"kind": "sound_map_md", "file": "sound_map.md"}]
    try:
        sound.spectrogram_png(wav, ctx.out / "spectrogram.png", r["segments"])
        arts.append({"kind": "spectrogram", "file": "spectrogram.png"})
    except Exception:  # noqa: BLE001
        pass
    layers = {}
    for g in r["segments"]:
        for l in g["layers"]:
            layers[l] = layers.get(l, 0) + g["t_end"] - g["t_start"]
    res = StageResult(artifacts=arts, source_updates={"st_audio": "events_estimated"},
                      note="Слои (с): " + ", ".join(f"{k} {v:.1f}" for k, v in layers.items()) + "; оценки")
    sid = ctx.source["id"]
    for g in r["segments"]:
        res.search.append({"etype": "sound", "eid": f"{sid}@snd{g['t_start']}", "title": ctx.source.get("title") or sid,
                           "body": " ".join(g["layers"] + [x["label"] for x in g["top_labels"]]), "t": g["t_start"]})
    return res


# ---------------------------------------------------------------- other kinds
def image_probe(ctx: Ctx) -> StageResult:
    from PIL import Image
    im = Image.open(ctx.original)
    small = im.convert("RGB").resize((64, int(64 * im.height / max(im.width, 1)) or 1))
    pal = small.quantize(colors=6).convert("RGB").getcolors(64 * 64) or []
    pal = sorted(pal, reverse=True)[:6]
    data = {"width": im.width, "height": im.height, "mode": im.mode, "format": im.format,
            "palette_estimate": [{"share": round(c / (small.width * small.height), 3),
                                  "hex": "#%02x%02x%02x" % rgb} for c, rgb in pal]}
    wjson(ctx.out / "image.json", data)
    im.convert("RGB").thumbnail((540, 540))
    th = im.convert("RGB")
    th.thumbnail((540, 540))
    th.save(ctx.out / "poster.jpg", quality=85)
    return StageResult(artifacts=[{"kind": "technical", "file": "image.json"}, {"kind": "poster", "file": "poster.jpg"}],
                       note=f"{im.width}x{im.height}, палитра (оценка) {', '.join(p['hex'] for p in data['palette_estimate'])}",
                       source_updates={"st_visual": "technical_only"})


TEXT_EXT = {".txt", ".md", ".srt", ".vtt", ".json", ".csv"}


def document_text(ctx: Ctx) -> StageResult:
    p = ctx.original
    if p.suffix.lower() not in TEXT_EXT:
        return StageResult(state="blocked",
                           note=f"Формат {p.suffix} пока не поддержан извлечением текста. Экспортируйте в .txt/.md")
    txt = p.read_text(encoding="utf-8", errors="replace")
    (ctx.out / "text.txt").write_text(txt, encoding="utf-8")
    res = StageResult(artifacts=[{"kind": "text", "file": "text.txt", "meta": {"chars": len(txt)}}],
                      note=f"{len(txt)} символов")
    # index by paragraph so search can point at a location
    for i, para in enumerate([q for q in re.split(r"\n\s*\n", txt) if q.strip()][:2000]):
        res.search.append({"etype": "doc_para", "eid": f"{ctx.source['id']}#p{i + 1}",
                           "title": ctx.source.get("title") or "", "body": para[:4000], "t": None})
    return res


class _Meta(__import__("html.parser").parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta, self.title, self._in_title, self.text, self._skip = {}, "", False, [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("property") or a.get("name")) and a.get("content"):
            self.meta[(a.get("property") or a.get("name")).lower()] = a["content"]
        if tag == "title":
            self._in_title = True
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.text.append(data.strip())


def page_fetch(ctx: Ctx) -> StageResult:
    import httpx
    url = ctx.source["url"]
    try:
        r = httpx.get(url, follow_redirects=True, timeout=httpx.Timeout(10, connect=6),
                      headers={"User-Agent": "CreativeStudio/0.1 (local single-user research tool)",
                               "Accept-Language": "ru,en;q=0.8"})
    except Exception as e:  # noqa: BLE001
        if ctx.source.get("rel_path"):
            return StageResult(state="partial", note=f"Метаданные страницы не получены ({type(e).__name__}); файл карточки уже есть")
        return StageResult(state="failed", note=f"Сеть: {type(e).__name__} {e}",
                           source_updates={"st_acquire": "unavailable",
                                           "blocked_reason": f"Платформа не ответила на запрос студии ({type(e).__name__}: {e}). "
                                                             "Обход ограничений платформы не выполняется.",
                                           "next_action": "Загрузите видеофайл (или субтитры/экспорт) в эту карточку: «Дополнить файлом»"})
    body = r.text[:2_000_000]
    (ctx.out / "page.html").write_text(body, encoding="utf-8")
    p = _Meta()
    try:
        p.feed(body)
    except Exception:  # noqa: BLE001
        pass
    final = str(r.url)
    login_wall = bool(re.search(r"/accounts/login|/login\b", final)) or (
        "instagram.com" in url and not p.meta.get("og:title"))
    info = {"requested": url, "final_url": final, "http_status": r.status_code, "title": p.title.strip(),
            "meta": {k: v for k, v in p.meta.items() if k.startswith(("og:", "twitter:", "description", "author"))},
            "login_wall_suspected": login_wall, "fetched_at": __import__("studio.db", fromlist=["now"]).now(),
            "media_downloaded": False,
            "note": "Получена только HTML-страница. Медиа с платформ без явного разрешения не скачивается."}
    wjson(ctx.out / "page.json", info)
    arts = [{"kind": "page_meta", "file": "page.json"}, {"kind": "page_html", "file": "page.html"}]
    social = re.search(r"(instagram|tiktok|youtube|youtu\.be|facebook|x\.com|twitter)", url)
    upd: dict = {}
    if p.meta.get("og:title") or p.title:
        upd["title"] = (p.meta.get("og:title") or p.title.strip())[:300]
    if p.meta.get("og:description"):
        upd["notes"] = "og:description (с платформы): " + p.meta["og:description"][:1000]
    if r.status_code >= 400:
        st = "failed"
        upd.update(st_acquire="unavailable", blocked_reason=f"HTTP {r.status_code}",
                   next_action="Проверьте ссылку или загрузите файл вручную в эту карточку")
    elif social:
        st = "blocked"
        upd.update(st_acquire="metadata_only" if not login_wall else "login_wall",
                   blocked_reason=("Платформа требует вход/не отдаёт медиа без авторизации" if login_wall else
                                   "Сохранены только метаданные страницы; медиа платформы не скачивается автоматически"),
                   next_action="Загрузите видеофайл (или субтитры/экспорт) в эту карточку: «Дополнить файлом»")
    else:
        st = "completed"
        text = "\n".join(p.text)[:200_000]
        (ctx.out / "page.txt").write_text(text, encoding="utf-8")
        arts.append({"kind": "text", "file": "page.txt", "meta": {"chars": len(text)}})
        upd.update(st_acquire="page_text", author=p.meta.get("author") or "неизвестно")
    note = f"HTTP {r.status_code}; {'похоже на стену входа; ' if login_wall else ''}og:title={'да' if p.meta.get('og:title') else 'нет'}"
    if ctx.source.get("rel_path"):
        # the card was already continued with a file: page metadata is supplementary only
        for k in ("st_acquire", "blocked_reason", "next_action"):
            upd.pop(k, None)
        st = "completed" if st != "failed" else "partial"
    if ctx.source.get("title") and ctx.source.get("title") != url:
        upd.pop("title", None)
    res = StageResult(state=st, note=note, artifacts=arts, source_updates=upd)
    res.search.append({"etype": "page", "eid": ctx.source["id"], "title": upd.get("title", url),
                       "body": " ".join([p.meta.get("og:description", ""), " ".join(p.text)[:20000] if st == "completed" else ""]),
                       "t": None})
    return res


# ---------------------------------------------------------------- context pack
ANALYSIS_SCHEMA_DOC = config.PROJECT_ROOT / "config" / "schemas" / "analysis.schema.md"


def context_pack(ctx: Ctx) -> StageResult:
    """Builds the `source-analysis` package: everything extracted so far + the expected answer schema."""
    import shutil
    pack = ctx.out / "source-analysis"
    pack.mkdir()
    manifest = {"pack": "source-analysis", "source": {k: ctx.source.get(k) for k in (
        "id", "kind", "title", "original_name", "author", "platform", "url", "duration", "language",
        "purpose", "usage_status", "provenance")}, "included": {}, "missing": []}
    for stage, files in (("probe", ["technical.json", "poster.jpg"]),
                         ("transcribe", ["transcript.original.json", "transcript.original.md"]),
                         ("frames", ["frames.json"]), ("ocr", ["ocr.json", "ocr.md"]),
                         ("audio_features", ["audio_features.json"]),
                         ("sound_events", ["sound_map.json", "sound_map.md", "spectrogram.png"]), ("image_probe", ["image.json", "poster.jpg"]),
                         ("document_text", ["text.txt"])):
        d = ctx.inputs.get(stage)
        if not d:
            continue
        for f in files:
            if (d / f).exists():
                shutil.copy2(d / f, pack / f)
                manifest["included"].setdefault(stage, []).append(f)
        if stage == "frames" and (d / "frames").exists():
            shutil.copytree(d / "frames", pack / "frames")
            for s in d.glob("contact_*.jpg"):
                shutil.copy2(s, pack / s.name)
            manifest["included"]["frames"].append("frames/*.jpg + contact_*.jpg")
    for need in ("transcribe", "frames", "ocr", "audio_features"):
        if ctx.source["kind"] == "video" and need not in ctx.inputs:
            manifest["missing"].append(need)
    if ANALYSIS_SCHEMA_DOC.exists():
        shutil.copy2(ANALYSIS_SCHEMA_DOC, pack / "EXPECTED_ANSWER.md")
    (pack / "README.md").write_text(
        "# Пакет source-analysis\n\nПередайте папку Claude (или другому ассистенту) и попросите ответить JSON "
        "строго по EXPECTED_ANSWER.md. Ответ импортируйте:\n\n"
        f"    ./studio import-analysis {ctx.source['id']} answer.json\n\n"
        "Таймкоды — в секундах по шкале оригинала. Ссылки на кадры — по полю file из frames.json.\n"
        "Звук: sound_map.md/json (AST + librosa, оценки) и spectrogram.png. Модель-классификатор слушала сигнал, Claude — нет.\n",
        encoding="utf-8")
    wjson(pack / "manifest.json", manifest)
    shutil.make_archive(str(ctx.out / "source-analysis"), "zip", pack)
    return StageResult(state="completed", note="Пакет для анализа собран; ожидается анализ AI (провайдер не подключён)",
                       artifacts=[{"kind": "context_pack", "file": "source-analysis.zip"},
                                  {"kind": "context_pack_dir", "file": "source-analysis"}],
                       source_updates={"st_analysis": "awaiting_ai"})


STAGES = {
    "probe": probe, "audio": audio, "transcribe": transcribe, "frames": frames, "ocr": ocr,
    "audio_features": audio_features, "image_probe": image_probe, "document_text": document_text,
    "page_fetch": page_fetch, "context_pack": context_pack, "sound_events": sound_events,
}

# stage -> dependencies, per source kind
PLANS = {
    "video": {"probe": [], "audio": ["probe"], "transcribe": ["audio"], "frames": ["probe"], "ocr": ["frames"],
              "audio_features": ["audio"], "sound_events": ["audio"],
              "context_pack": ["transcribe", "ocr", "audio_features", "sound_events"]},
    "audio": {"probe": [], "audio": ["probe"], "transcribe": ["audio"], "audio_features": ["audio"],
              "sound_events": ["audio"], "context_pack": ["transcribe", "audio_features", "sound_events"]},
    "image": {"image_probe": [], "ocr": ["image_probe"], "context_pack": ["ocr"]},
    "document": {"document_text": [], "context_pack": ["document_text"]},
    "url": {"page_fetch": []},
}


def dense_pass(con, source_id: str, t0: float, t1: float, step: float = 0.1) -> dict:
    """Dense frame + OCR pass over [t0, t1] for short inserts and ambiguous cuts.
    Published atomically like a stage; frames become citable artifacts (kind=frame, meta.dense=true)."""
    import os
    import time
    from ocrmac import ocrmac
    from . import config, db
    src = db.row(con.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())
    dur = float(src.get("duration") or 0)
    t0, t1 = max(0.0, t0), min(t1, dur - 0.05)
    if t1 <= t0 or step < 0.04:
        raise ValueError("Неверный интервал или шаг")
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-dense-{os.urandom(2).hex()}"
    base = config.DATA_DIR / "derived" / source_id
    tmp, final = base / f".tmp-{run_id}", base / run_id
    (tmp / "frames").mkdir(parents=True)
    orig = config.safe_path(src["rel_path"])
    rec, t = [], t0
    while t <= t1 + 1e-6:
        name = f"d{int(round(t * 1000)):06d}.jpg"
        if media.extract_frame(orig, t, tmp / "frames" / name, width=360):
            ann = ocrmac.OCR(str(tmp / "frames" / name), recognition_level="accurate",
                             language_preference=["ru-RU", "uk-UA", "en-US"]).recognize()
            rec.append({"t": round(t, 3), "file": f"frames/{name}",
                        "ocr": " | ".join(a[0] for a in ann if a[1] >= 0.3)})
        t += step
    # where does the on-screen text change?
    changes = [rec[0]] + [r for a, r in zip(rec, rec[1:]) if r["ocr"] != a["ocr"]] if rec else []
    sheets = _contact_sheets(tmp, [{"t": r["t"], "file": r["file"], "reason": "dense"} for r in rec], per=24, cols=6)
    wjson(tmp / "dense.json", {"interval": [t0, t1], "step": step, "frames": rec,
                               "text_changes": [{"t": c["t"], "ocr": c["ocr"]} for c in changes],
                               "method": f"кадр каждые {step} с + OCR Apple Vision"})
    os.replace(tmp, final)
    with db.tx(con):
        for r in rec:
            con.execute("INSERT INTO artifacts(id,source_id,run_id,stage,kind,rel_path,t,meta,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (db.new_id("art"), source_id, run_id, "dense", "frame", config.rel(final / r["file"]), r["t"],
                         db.j({"reason": "dense", "dense": True}), db.now()))
        con.execute("INSERT INTO artifacts(id,source_id,run_id,stage,kind,rel_path,t,meta,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (db.new_id("art"), source_id, run_id, "dense", "dense_index", config.rel(final / "dense.json"), t0,
                     db.j({"interval": [t0, t1], "step": step}), db.now()))
        for s in sheets:
            con.execute("INSERT INTO artifacts(id,source_id,run_id,stage,kind,rel_path,t,meta,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (db.new_id("art"), source_id, run_id, "dense", "contact_sheet", config.rel(final / s), t0, "{}", db.now()))
    return {"run_dir": config.rel(final), "frames": len(rec), "text_changes": [{"t": c["t"], "ocr": c["ocr"]} for c in changes],
            "contact_sheets": [config.rel(final / s) for s in sheets]}
