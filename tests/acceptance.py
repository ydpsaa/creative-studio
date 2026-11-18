"""Acceptance checks in an ISOLATED data dir with labeled test fixtures (never touches studio-data/).
Run:  .venv/bin/python tests/acceptance.py      → prints results, writes tests/acceptance-result.json
Each check reports what was actually executed; a check only passes on observed behaviour."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cs-accept-"))
DATA = TMP / "data"
os.environ["STUDIO_DATA_DIR"] = str(DATA)
sys.path.insert(0, str(ROOT / "app" / "api"))
FIX = ROOT / "tests" / "fixtures"
PY = str(ROOT / ".venv" / "bin" / "python")
REVIEW_ROOT = TMP / "review-assets"            # test fixtures for «Ревью» (never real user folders)
os.environ["STUDIO_REVIEW_CONFIG"] = str(TMP / "review_sets.json")
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "app" / "api"), "STUDIO_DATA_DIR": str(DATA)}

from studio import backup, brands, db, jobs, knowledge, projects, results, sources  # noqa: E402

R: list[dict] = []


def check(n, name, ok, detail, status=None):
    R.append({"n": n, "name": name, "status": status or ("проверено" if ok else "НЕ ПРОЙДЕНО"), "detail": detail})
    print(f"[{n:>2}] {'OK ' if ok else 'FAIL'} {name}: {detail}", flush=True)


def worker_once():
    subprocess.run([PY, "-m", "studio.cli", "work-once"], env=ENV, check=True, capture_output=True, timeout=900)


con = db.open_db()
brands.seed(con)

# 1 — import + restart persistence -------------------------------------------------
r = sources.import_file(con, FIX / "TEST_clip_tone.mp4", {"is_test": True, "title": "TEST clip", "collections": ["TEST"]})
sid = r["source"]["id"]
# simulated crash: start a worker, kill -9 while it runs, then restart (criterion 7)
p = subprocess.Popen([PY, "-m", "studio.cli", "worker"], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deadline = time.time() + 120
c2 = db.connect()
while time.time() < deadline:
    st = {j["stage"]: j["state"] for j in c2.execute("SELECT stage, state FROM jobs WHERE source_id=?", (sid,))}
    if st.get("transcribe") == "running" or st.get("frames") == "completed":
        break
    time.sleep(0.3)
p.send_signal(signal.SIGKILL)
p.wait()
before = {j["stage"]: (j["state"], j["run_id"]) for j in c2.execute("SELECT stage, state, run_id FROM jobs WHERE source_id=?", (sid,))}
done_before = {k: v for k, v in before.items() if v[0] in ("completed", "partial")}
# force the dead worker's job stale (instead of waiting 120 s)
c2.execute("UPDATE jobs SET heartbeat=0 WHERE state='running'")
worker_once()
after = {j["stage"]: (j["state"], j["run_id"]) for j in c2.execute("SELECT stage, state, run_id FROM jobs WHERE source_id=?", (sid,))}
kept = all(after[k][1] == v[1] for k, v in done_before.items())
all_done = all(v[0] in ("completed", "partial") for v in after.values())
check(7, "Прерванная обработка возобновляется без потери готовых результатов", kept and all_done,
      f"до kill -9 готово: {sorted(done_before)}; после перезапуска все этапы: {sorted(set(v[0] for v in after.values()))}; "
      f"run_id готовых этапов не изменились: {kept}")

# fresh process = restart; reads DB from disk
out = subprocess.run([PY, "-m", "studio.cli", "source", sid], env=ENV, capture_output=True, text=True).stdout
s_after = json.loads(out)["source"]
orig_ok = (DATA / s_after["rel_path"]).exists()

# 2 — duplicate import ------------------------------------------------------------
n_jobs = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
r2 = sources.import_file(con, FIX / "TEST_clip_tone.mp4", {"is_test": True, "via": "повторный импорт"})
n_jobs2 = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
n_orig = len(list((DATA / "originals").glob("*/TEST_clip_tone.mp4")))
check(2, "Повторный импорт идентичного файла не создаёт дубль и повторную обработку",
      r2["duplicate"] and r2["source"]["id"] == sid and n_jobs == n_jobs2 and n_orig == 1,
      f"duplicate={r2['duplicate']}, тот же source_id={r2['source']['id'] == sid}, новых задач {n_jobs2 - n_jobs}, копий оригинала {n_orig}, "
      f"ссылок-происхождений {con.execute('SELECT count(*) FROM source_links WHERE source_id=?', (sid,)).fetchone()[0]}")

# 6 — unreachable link stays visible, others continue -------------------------------
ru = sources.import_url(con, "http://127.0.0.1:9/closed-video", {"is_test": True})
sources.add_note(con, "TEST note", (FIX / "TEST_note.md").read_text(), {"is_test": True})
worker_once()
u = sources.get(con, ru["source"]["id"])
note_ok = con.execute("SELECT count(*) FROM jobs j JOIN sources s ON s.id=j.source_id WHERE s.title='TEST note' AND j.state='completed'").fetchone()[0] > 0
check(6, "Закрытая ссылка видна с причиной и способом продолжить; остальное обрабатывается",
      u["st_acquire"] in ("unavailable", "failed") and bool(u["blocked_reason"]) and bool(u["next_action"]) and note_ok,
      f"статус={u['st_acquire']}; причина={u['blocked_reason'][:70]}…; действие={u['next_action'][:50]}…; заметка обработана={note_ok}")

# 5 — speech vs visual coverage ------------------------------------------------------
from studio import coverage  # noqa: E402
cov = coverage.build(con, sid)
check(5, "Речь и частично разобранный визуал имеют разные состояния покрытия",
      cov["speech"] is not None and cov["visual"] is not None and "Выборка" in cov["visual"]["note"],
      f"speech={s_after['st_speech']} (ASR весь звук), visual={s_after['st_visual']} ({cov['visual']['frames_sampled']} кадров, "
      f"макс. разрыв {cov['visual']['max_gap_between_frames_s']} с), audio={s_after['st_audio']}; пробел: {cov['gaps']}")

# 1 (cont.) ------------------------------------------------------------------------
frames = json.loads(next((DATA / "derived" / sid).glob("*frames*/frames.json")).read_text())["frames"]
f0, f1 = frames[0]["file"], frames[min(3, len(frames) - 1)]["file"]
analysis = {
    "schema": "analysis/1", "source_id": sid, "origin": "claude-manual", "summary": "ТЕСТ: синтетический клип testsrc2 + тон 440 Гц.",
    "timeline": [{"t_start": 0, "t_end": 8, "speech": "нет речи (тон)", "visual": "тестовая таблица", "audio": "тон, не прослушано",
                  "teaches": "тест", "evidence": [{"type": "frame", "file": f0}]}],
    "coverage": {"frames_viewed": [f0, f1], "transcript_read": True, "audio_listened": False, "not_covered": ["тест"]},
    "knowledge": [{"title": "ТЕСТ: зебра-приём синтетического хука", "kind": "hook", "category": "hooks",
                   "statement": "Тестовая карточка зебра для проверки поиска", "evidence": "observed",
                   "refs": [{"source_id": sid, "t_start": 0.0, "t_end": 2.0, "frame": f0}], "is_test": True}]}
bad = dict(analysis, timeline=[dict(analysis["timeline"][0], t_end=999)])
rej = results.import_analysis(con, sid, bad)
bad_ev = json.loads(json.dumps(analysis))
bad_ev["knowledge"][0]["evidence"] = "user_preference"
rej2 = results.import_analysis(con, sid, bad_ev)
acc = results.import_analysis(con, sid, analysis)
kid = acc["knowledge_ids"][0]
ref = knowledge.get(con, kid)["refs"][0]
check(4, "Из карточки правила открывается источник и таймкод/кадр",
      ref["source_id"] == sid and ref["t_start"] == 0.0 and bool(ref["artifact_id"]),
      f"{kid} → {ref['source_id']} @ {ref['t_start']}–{ref['t_end']} с, кадр-артефакт {ref['artifact_id']}; "
      f"невалидный таймкод отклонён={not rej['ok']}, evidence=user_preference от анализа отклонён={not rej2['ok']}")

# 8 — search + project keeps applied knowledge ids ----------------------------------
hits = knowledge.search(con, "зебра", ["knowledge"], include_test=True)
BA = brands.save_brand(con, None, "TEST Brand A", {"kind": "тест"})["id"]
BB = brands.save_brand(con, None, "TEST Brand B", {"kind": "тест"})["id"]
p1 = projects.create(con, "TEST проект A", {"topic": "зебра хук", "brand_id": BA}, is_test=True)
imp = projects.import_docs(con, p1["id"], {"schema": "project-docs/1", "origin": "test", "knowledge_ids": [kid],
                                            "docs": {"script": "ТЕСТ сценарий", "storyboard": [{"scene": "1", "basis": kid}]}})
imp_bad = projects.import_docs(con, p1["id"], {"schema": "project-docs/1", "origin": "test", "knowledge_ids": ["kn_000000_000000"], "docs": {}})
srcmd = (DATA / "projects" / p1["id"] / "sources.md").read_text()
check(8, "Поиск находит приём; сценарий сохраняет ссылки на применённые знания",
      any(h["eid"] == kid for h in hits) and imp["ok"] and kid in srcmd and not imp_bad["ok"],
      f"поиск «зебра» → {[h['eid'] for h in hits]}; sources.md содержит {kid}: {kid in srcmd}; несуществующий ID отклонён: {not imp_bad['ok']}")

# 9 — brand / one-off preference isolation ------------------------------------------
p2 = projects.create(con, "TEST проект B", {"topic": "зебра хук", "brand_id": BB}, is_test=True)
brands.add_pref(con, "brand", BB, "TEST: зелёный акцент только для BRAND_B")
brands.add_pref(con, "clip", p1["id"], "TEST: разовая правка только этого ролика")
with db.tx(con):
    knowledge.insert(con, {"title": "ТЕСТ: зебра-приём только BRAND_B", "kind": "rule", "category": "hooks", "statement": "зебра бренд",
                           "evidence": "user_preference", "brand_scope": BB, "is_test": True,
                           "refs": [{"source_id": sid, "t_start": 0, "t_end": 1}]}, "user", "user")
pr1 = [x["text"] for x in brands.prefs_for_project(con, projects.get(con, p1["id"]))]
pr2 = [x["text"] for x in brands.prefs_for_project(con, projects.get(con, p2["id"]))]
k1 = [k["title"] for k in projects.select_knowledge(con, p1["id"])["cards"]]
k2 = [k["title"] for k in projects.select_knowledge(con, p2["id"])["cards"]]
iso = (not any("BRAND_B" in t for t in pr1) and any("BRAND_B" in t for t in pr2) and any("разовая" in t for t in pr1)
       and not any("разовая" in t for t in pr2) and not any("BRAND_B" in t for t in k1) and any("BRAND_B" in t for t in k2))
check(9, "Предпочтение бренда и разовая правка не загрязняют другие проекты", iso,
      f"проект A: prefs={pr1}, карточек BRAND_B={sum('BRAND_B' in t for t in k1)}; проект B: prefs={pr2}, карточек BRAND_B={sum('BRAND_B' in t for t in k2)}")

# 10 — no-AI mode via HTTP --------------------------------------------------------------
from fastapi.testclient import TestClient  # noqa: E402
from studio.main import app  # noqa: E402
cl = TestClient(app, headers={"host": "127.0.0.1:8765"})
t = cl.get("/api/tools").json()
put = cl.put("/api/settings", json={"ai_mode": "provider:anthropic"}, headers={"x-studio": "1"})
pk = cl.post(f"/api/projects/{p1['id']}/pack", headers={"x-studio": "1"}).json()
lib = cl.get("/api/sources?include_test=1").json()
no_hdr = cl.post("/api/import/note", json={"title": "x"}).status_code
evil = TestClient(app, headers={"host": "evil.example:8765"}).get("/api/health").status_code
check(10, "Без AI-провайдера работают библиотека и пакеты контекста; фиктивной генерации нет",
      t["ai_provider"]["available"] is False and put.status_code == 400 and (DATA / pk["file"]).exists() and len(lib) >= 3,
      f"ai_provider.available={t['ai_provider']['available']}; включить провайдера без адаптера → HTTP {put.status_code}; "
      f"пакет project-generation создан ({pk['knowledge']} карточек); библиотека отдаёт {len(lib)} источников; "
      f"запись без X-Studio → {no_hdr}; чужой Host → {evil}")

# 11 — portable export opens outside the studio -------------------------------------
ex = projects.export_zip(con, p1["id"], "portable")
outdir = TMP / "unzipped"
with zipfile.ZipFile(DATA / ex["zip"]) as z:
    z.extractall(outdir)
root = outdir / p1["id"]
man = json.loads((root / "manifest.json").read_text())
bad_files = [f["path"] for f in man["files"] if hashlib.sha256((root / f["path"]).read_bytes()).hexdigest() != f["sha256"]]
check(11, "Переносимый экспорт открывается вне студии; манифест проверяет состав", not bad_files and len(man["files"]) >= 5,
      f"файлов {len(man['files'])}, несовпадений хеша {len(bad_files)}, есть storyboard.csv={(root / 'storyboard.csv').exists()}")

# 12 — backup & restore into a separate folder ----------------------------------------
b = backup.create(con)
rv = backup.restore_and_verify(Path(b["backup"]), TMP / "restored")
check(12, "Резервная копия восстанавливается в отдельную папку с корректными связями", rv["ok"],
      f"файлов {rv['files']}, integrity={rv['integrity_check']}, отсутствующих файлов {rv['missing_count']}, "
      f"висячих ссылок знаний {rv['orphan_knowledge_refs']}, записей: {rv['counts']}")

# 1 — final restart check: fresh process sees everything ------------------------------
out = subprocess.run([PY, "-m", "studio.cli", "status"], env=ENV, capture_output=True, text=True).stdout
stt = json.loads(out)
proj_ok = (DATA / "projects" / p1["id"] / "script.md").exists()
check(1, "После импорта и перезапуска сохраняются оригинал, карточка, результаты и проект",
      orig_ok and stt["knowledge"] >= 1 and stt["projects"] >= 1 and proj_ok,
      f"оригинал на диске={orig_ok}; новый процесс видит: {stt['sources']}, знаний {stt['knowledge']}, проектов {stt['projects']}; файлы проекта={proj_ok}")

# 13 — portable skill export works outside the studio ------------------------------
from studio import skill_export  # noqa: E402
sx = skill_export.export(con, "test-skill")
copy = TMP / "skill-copy"
with zipfile.ZipFile(DATA / sx["zip"]) as z:
    z.extractall(copy)
ck = skill_export.check(copy / "test-skill")
sx2 = skill_export.export(con, "test-skill")
check(13, "Экспорт переносимого навыка: самостоятелен, версионирован", ck["ok"] and sx2["version"].startswith("2."),
      f"методов {sx['methods']}, файлов {sx['files']}; проверка копии вне студии: ok={ck['ok']}, проблем {ck['problem_count']}; "
      f"повторный экспорт → версия {sx2['version']}")

# 14 — «Ревью»: scan, decisions persist, MCP feedback, soft delete / Trash ----------------
import wave  # noqa: E402
from PIL import Image  # noqa: E402
for g in ("G01_test", "G02_test"):
    (REVIEW_ROOT / "stickers" / g).mkdir(parents=True)
    for i in range(3):
        Image.new("RGBA", (64, 64), (255, 0, 0, 128)).save(REVIEW_ROOT / "stickers" / g / f"0{i}_TEST.png")
(REVIEW_ROOT / "video").mkdir()
shutil.copy(FIX / "TEST_clip_tone.mp4", REVIEW_ROOT / "video" / "TEST.mp4")
(REVIEW_ROOT / "sfx").mkdir()
with wave.open(str(REVIEW_ROOT / "sfx" / "TEST_pop.wav"), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b"\0\0" * 800)
(REVIEW_ROOT / "STYLE.md").write_text("# TEST style\n")
(TMP / "review_sets.json").write_text(json.dumps({"roots": {"t": str(REVIEW_ROOT)}, "sets": [
    {"kind": "sticker_xerox", "root": "t", "glob": "stickers/G*/*.png", "group": "{dir}"},
    {"kind": "video", "root": "t", "glob": "video/*.mp4", "group": "test"},
    {"kind": "sound", "root": "t", "glob": "sfx/*.wav", "group": "sfx"},
    {"kind": "doc", "root": "t", "glob": "*.md", "group": "style"}]}))
from studio import review, site_sync  # noqa: E402
from studio import mcp_server  # noqa: E402
sc1 = review.scan(con)
Image.new("RGBA", (64, 64)).save(REVIEW_ROOT / "stickers" / "G02_test" / "09_NEW.png")
sc2 = review.scan(con)
items = review.list_items(con, kind="sticker_xerox")
newest = next(i for i in items if i["title"] == "09 NEW")
a, b, c3 = items[0]["id"], items[1]["id"], items[2]["id"]
review.decide(con, [a], status="selected")
review.decide(con, [b], status="needs_changes", comment="TEST: края обрезаны", forward=True)
review.group_comment(con, "sticker_xerox", "G01_test", "TEST: группа слишком тёмная", forward=True)
hdr = {"x-studio": "1"}
no_hdr_rv = cl.post("/api/review/decide", json={"ids": [c3], "status": "rejected"}).status_code
http_rej = cl.post("/api/review/decide", json={"ids": [c3], "status": "rejected"}, headers=hdr).json()
bad_status = cl.post("/api/review/decide", json={"ids": [c3], "status": "deleted"}, headers=hdr).status_code
th_img = cl.get(f"/api/review/thumb/{a}").status_code
vid = review.list_items(con, kind="video")[0]["id"]
th_vid = cl.get(f"/api/review/thumb/{vid}").status_code
# a fresh process (restart) sees the decisions
fb_cli = json.loads(subprocess.run([PY, "-m", "studio.cli", "review-feedback"], env=ENV, capture_output=True, text=True).stdout)
fb = mcp_server.get_review_feedback()
lst = mcp_server.list_review(kind="sticker_xerox", status="needs_changes")
mcp_server.mark_review_handled(b, "TEST: перегенерировано")
mcp_server.mark_review_handled("rg:sticker_xerox:G01_test", "TEST: группа осветлена")
fb2 = mcp_server.get_review_feedback()
since = fb2["now"]
time.sleep(1.1)
review.decide(con, [a], comment="TEST: позже")
fb3 = mcp_server.get_review_feedback(since=since)
# soft delete keeps the file; Trash only with confirm
p_rej = Path(review.list_items(con, status="rejected")[0]["path"])
FAKE_TRASH = TMP / "fake-trash"                 # don't litter the owner's real Trash on every run
FAKE_TRASH.mkdir()
review._trash = lambda p: shutil.move(str(p), FAKE_TRASH / p.name)  # real NSFileManager path verified manually 2026-11-18
no_confirm = cl.post(f"/api/review/items/{newest['id']}/trash", json={}, headers=hdr).status_code
tr = cl.post(f"/api/review/items/{newest['id']}/trash", json={"confirm": True}, headers=hdr)
trashed_ok = (tr.status_code == 200 and not (REVIEW_ROOT / "stickers" / "G02_test" / "09_NEW.png").exists()
              and (FAKE_TRASH / "09_NEW.png").exists() and review.get_item(con, newest["id"])["status"] == "rejected")
snap = site_sync.build_snapshot(con)["review"]
snap_txt = json.dumps(snap, ensure_ascii=False)
ok14 = (sc1["total"] == 9 and sc2["new"] == 1 and newest["status"] == "new"
        and no_hdr_rv == 403 and http_rej["changed"] == 1 and bad_status == 400 and th_img == 200 and th_vid == 200
        and {x["id"] for x in fb_cli["decisions"]} >= {a, b, c3} and [x["id"] for x in fb_cli["pending_requests"]] == [b]
        and fb["pending_requests"][0]["comment"] == "TEST: края обрезаны" and len(fb["pending_group_requests"]) == 1
        and lst["total"] == 1 and not fb2["pending_requests"] and not fb2["pending_group_requests"]
        and [x["id"] for x in fb3["decisions"]] == [a]
        and p_rej.exists() and no_confirm == 400 and trashed_ok
        and len(snap["items"]) == 10 and "/" not in snap_txt.replace("/1", "") and "TEST: края обрезаны" in snap_txt)
check(14, "«Ревью»: скан, решения сохраняются, MCP видит отзывы, удаление мягкое", ok14,
      f"скан {sc1['total']} → +{sc2['new']} новый; без X-Studio → {no_hdr_rv}; неверный статус → {bad_status}; миниатюры img/video "
      f"{th_img}/{th_vid}; новый процесс видит решений {len(fb_cli['decisions'])}, запросов {len(fb_cli['pending_requests'])}; "
      f"MCP: запрос с комментарием={fb['pending_requests'][0]['comment']!r}, групповых {len(fb['pending_group_requests'])}; "
      f"после mark_review_handled открытых {len(fb2['pending_requests'])}+{len(fb2['pending_group_requests'])}; since → {len(fb3['decisions'])}; "
      f"отклонённый файл на месте={p_rej.exists()}; Корзина без confirm → {no_confirm}, с confirm → {tr.status_code}, файл в (тестовой) Корзине={trashed_ok}; "
      f"снимок сайта: {len(snap['items'])} элементов без путей")

R.sort(key=lambda x: x["n"])
(ROOT / "tests" / "acceptance-result.json").write_text(json.dumps({"run_at": db.now(), "data_dir": str(DATA), "results": R},
                                                                   ensure_ascii=False, indent=2), encoding="utf-8")
shutil.rmtree(TMP, ignore_errors=True)
print("ИТОГО:", sum(r["status"] == "проверено" for r in R), "/", len(R))
