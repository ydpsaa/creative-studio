"""sound_events stage: what is audible, from the audio signal itself.
- AST (MIT/ast-finetuned-audioset-10-10-0.4593, AudioSet 527 classes) on 2 s windows, hop 0.5 s → event labels with probabilities.
- librosa per window: loudness, spectral centroid (brightness), flatness (noisiness), HPSS percussive share, onset density.
All results are model/DSP ESTIMATES. Generic AudioSet categories only — never a track or product name."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

AST_REPO = "MIT/ast-finetuned-audioset-10-10-0.4593"
WIN, HOP, SR = 2.0, 0.5, 16000
SPEECH = {"Speech", "Male speech, man speaking", "Female speech, woman speaking", "Child speech, kid speaking",
          "Conversation", "Narration, monologue", "Speech synthesizer", "Whispering"}
MUSICISH_HINT = ("Music", "music", "Song", "Singing", "Guitar", "Piano", "Drum", "Bass", "Synthesizer", "Beat",
                 "Orchestra", "Hip hop", "Electronic", "Ambient", "Pop", "Rock", "Jingle", "Theme", "Choir", "Vocal")
GENERIC = {"Silence", "Inside, small room", "Inside, large room or hall", "Outside, urban or manmade",
           "Outside, rural or natural", "Static", "Hum", "Noise", "Speech", "Music",
           "Television", "Sound effect", "Musical instrument"}

_model = None


def _load():
    global _model
    if _model is None:
        import torch
        from transformers import ASTFeatureExtractor, ASTForAudioClassification
        fe = ASTFeatureExtractor.from_pretrained(AST_REPO)
        m = ASTForAudioClassification.from_pretrained(AST_REPO).eval()
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        _model = (fe, m.to(dev), dev)
    return _model


def _is_music(label: str) -> bool:
    return any(h in label for h in MUSICISH_HINT)


def analyze(wav: Path, duration: float) -> dict:
    import librosa
    import torch
    y, _ = librosa.load(str(wav), sr=SR, mono=True)
    fe, m, dev = _load()
    labels = m.config.id2label
    # non-overlapping 0.5 s cells; each is described by a 2 s analysis window centred on it
    wins = []
    k = 0.0
    while k < duration - 0.05:
        c0, c1 = k, min(k + HOP, duration)
        mid = (c0 + c1) / 2
        a, b = max(0.0, mid - WIN / 2), min(duration, mid + WIN / 2)
        seg = y[int(a * SR):int(b * SR)]
        if len(seg) >= SR * 0.25:
            wins.append((float(c0), float(c1), seg))
        k += HOP
    probs = []
    for i in range(0, len(wins), 16):
        batch = [w[2] for w in wins[i:i + 16]]
        x = fe(batch, sampling_rate=SR, return_tensors="pt")
        with torch.no_grad():
            p = torch.sigmoid(m(**{k: v.to(dev) for k, v in x.items()}).logits).cpu().numpy()
        probs.append(p)
    P = np.concatenate(probs) if probs else np.zeros((0, len(labels)))
    speech_idx = [i for i, l in labels.items() if l in SPEECH]
    music_idx = [i for i, l in labels.items() if _is_music(l)]
    out = []
    for (s, e, seg), p in zip(wins, P):
        seg_len = len(seg) / SR
        rms = float(20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9))
        cen = float(np.mean(librosa.feature.spectral_centroid(y=seg, sr=SR)))
        flat = float(np.mean(librosa.feature.spectral_flatness(y=seg)))
        h, pc = librosa.effects.hpss(seg)
        perc = float(np.sum(pc ** 2) / (np.sum(h ** 2) + np.sum(pc ** 2) + 1e-12))
        onsets = librosa.onset.onset_detect(y=seg, sr=SR, units="time", delta=0.3)  # strong attacks only
        top = np.argsort(-p)[:6]
        events = [{"label": labels[int(i)], "p": round(float(p[i]), 3)} for i in top if p[i] >= 0.08]
        out.append({"t_start": round(s, 2), "t_end": round(e, 2),
                    "speech_p": round(float(p[speech_idx].max()) if speech_idx else 0, 3),
                    "music_p": round(float(p[music_idx].max()) if music_idx else 0, 3),
                    "events": events, "rms_db": round(rms, 1), "centroid_hz": round(cen),
                    "flatness": round(flat, 3), "percussive_share": round(perc, 2),
                    "onsets_per_s": round(len(onsets) / max(seg_len, 0.1), 1)})
    tempo = None
    try:
        t, _ = librosa.beat.beat_track(y=y, sr=SR)
        tempo = round(float(np.atleast_1d(t)[0]), 1)
    except Exception:  # noqa: BLE001
        pass
    return {"method": f"AST {AST_REPO} (AudioSet, окна {WIN} с, шаг {HOP} с) + librosa (centroid, flatness, HPSS, onsets)",
            "is_estimate": True, "duration": duration, "tempo_estimate_bpm": tempo,
            "warning": "Оценки моделей. Категории AudioSet общие; названия треков/конкретных эффектов не определяются. "
                       "Короткие (<0,5 с) события и наложения могут быть пропущены или смешаны.",
            "cells": out, "segments": segment(out),
            "note": "Ячейки 0,5 с без перекрытия; признаки ячейки считаются по окну 2 с вокруг неё (контекст ±0,75 с)"}


def _character(w: list[dict]) -> dict:
    cen = np.mean([x["centroid_hz"] for x in w])
    perc = np.mean([x["percussive_share"] for x in w])
    flat = np.mean([x["flatness"] for x in w])
    ons = np.mean([x["onsets_per_s"] for x in w])
    return {"brightness": "яркий/высокий" if cen > 2500 else "средний" if cen > 1200 else "тёмный/низкий",
            "texture": "ударный/щелчки" if perc > 0.55 else "шумовой" if flat > 0.2 else "тональный/протяжный",
            "activity": "частые резкие атаки" if ons > 2.5 else "отдельные резкие атаки" if ons >= 0.5 else "без резких атак",
            "loudness_db": round(float(np.mean([x["rms_db"] for x in w])), 1),
            "centroid_hz": int(cen), "percussive_share": round(float(perc), 2), "onsets_per_s": round(float(ons), 1)}


def _key(w: dict) -> tuple:
    layer = []
    if w["speech_p"] >= 0.35:
        layer.append("речь")
    if w["music_p"] >= 0.3:
        layer.append("музыка")
    fx = [e["label"] for e in w["events"] if e["label"] not in SPEECH and not _is_music(e["label"])
          and e["label"] not in GENERIC and e["p"] >= 0.15]
    if fx:
        layer.append("эффект")
    if not layer:
        layer.append("тишина/фон" if w["rms_db"] < -45 else "неопределённый звук")
    return tuple(layer), tuple(fx[:2])


def segment(wins: list[dict]) -> list[dict]:
    segs: list[dict] = []
    for w in wins:
        k = _key(w)
        if segs and segs[-1]["_k"] == k:
            segs[-1]["t_end"] = w["t_end"]
            segs[-1]["_w"].append(w)
            continue
        segs.append({"_k": k, "t_start": w["t_start"], "t_end": w["t_end"], "_w": [w]})
    out = []
    for s in segs:
        ws = s["_w"]
        agg: dict[str, float] = {}
        for w in ws:
            for e in w["events"]:
                agg[e["label"]] = max(agg.get(e["label"], 0), e["p"])
        top = sorted(agg.items(), key=lambda x: -x[1])[:5]
        # windows overlap: a 2-s window starting at t covers t..t+2, so the event lies inside, not across, the span
        out.append({"t_start": s["t_start"], "t_end": s["t_end"], "layers": list(s["_k"][0]),
                    "effect_labels": list(s["_k"][1]),
                    "top_labels": [{"label": l, "p": round(p, 3)} for l, p in top],
                    "speech_p": max(w["speech_p"] for w in ws), "music_p": max(w["music_p"] for w in ws),
                    "character": _character(ws)})
    return out


def spectrogram_png(wav: Path, out: Path, segs: list[dict]) -> None:
    import librosa
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    y, _ = librosa.load(str(wav), sr=SR)
    S = librosa.power_to_db(librosa.feature.melspectrogram(y=y, sr=SR, n_mels=96), ref=np.max)
    dur = len(y) / SR
    fig, ax = plt.subplots(figsize=(max(8, dur / 2.5), 3.2), dpi=110)
    ax.imshow(S, origin="lower", aspect="auto", extent=[0, dur, 0, 96], cmap="magma")
    col = {"речь": "#6aa8ff", "музыка": "#3ddc84", "эффект": "#f2c46d"}
    for s in segs:
        for i, l in enumerate(s["layers"]):
            ax.add_patch(plt.Rectangle((s["t_start"], 96 + i * 5), s["t_end"] - s["t_start"], 4, color=col.get(l, "#888"), clip_on=False))
    ax.set_ylim(0, 112)
    ax.set_xlabel("секунды"); ax.set_yticks([])
    ax.set_title("Mel-спектрограмма; полосы сверху: синий — речь, зелёный — музыка, жёлтый — эффект (оценки AST)", fontsize=8)
    fig.tight_layout(); fig.savefig(out); plt.close(fig)
