"""Organic-only clipping workspace. Campaign terms, eligible views and receipts are entered by the owner."""
from __future__ import annotations

import json
import math
import sqlite3
import tempfile
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, HTTPException

from . import config, db, media

router = APIRouter(prefix="/api/clipping")
DISCOVERY = [
    {"name": "Content Rewards", "url": "https://contentrewards.com/", "note": "Check each brief, eligibility, budget and payout limits."},
    {"name": "Vyro", "url": "https://vyro.com/creators", "note": "Organic only; verify the current brief."},
    {"name": "Clipster", "url": "https://www.clipster.com/", "note": "Organic only; verify the current brief."},
]
TRANSITIONS = {"rendered": {"published"}, "published": {"submitted"},
               "submitted": {"approved", "rejected"}, "approved": {"paid", "rejected"}}


def fail(message, status=400):
    raise HTTPException(status, message)


def text(v, field, limit=4000, required=True):
    if not isinstance(v, str) or len(v) > limit or (required and not v.strip()):
        fail(f"Invalid {field}")
    return v.strip()


def url(v, field):
    v = text(v, field, 2000)
    try:
        p = urlsplit(v)
        if p.scheme != "https" or not p.hostname or p.username or p.password or any(c.isspace() for c in v):
            fail(f"{field}: HTTPS URL required")
    except ValueError:
        fail(f"Invalid {field}")
    return v


def integer(v, field, maximum=1_000_000_000):
    if type(v) is not int or not 0 <= v <= maximum:
        fail(f"Invalid {field}: nonnegative integer required")
    return v


def seconds(v):
    if type(v) not in (int, float) or not math.isfinite(v):
        fail("Invalid time")
    return round(float(v), 2)


def lookup(c, table, identifier):
    if table not in ("clip_campaigns", "clipping_items", "sources"):
        raise ValueError("invalid table")
    r = c.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
    if not r:
        fail("Not found", 404)
    return dict(r)


def campaign_fields(body):
    title = text(body.get("title"), "title", 160)
    platform = text(body.get("platform"), "platform", 80)
    state = text(body.get("state", "draft"), "state", 20)
    if state not in ("draft", "active", "closed"):
        fail("Invalid campaign state")
    confirmed = body.get("confirmed", False)
    if type(confirmed) is not bool:
        fail("confirmed must be boolean")
    deadline = text(body.get("deadline", ""), "deadline", 10, False)
    if deadline:
        try:
            if date.fromisoformat(deadline).isoformat() != deadline:
                raise ValueError()
        except ValueError:
            fail("Deadline must be YYYY-MM-DD (UTC)")
    rules = text(body.get("rules", ""), "rules", 12000, state == "active")
    rights = text(body.get("rights", ""), "rights", 4000, state == "active")
    cpm = integer(body.get("cpm_cents"), "cpm_cents", 100000)
    minimum = integer(body.get("min_views"), "min_views")
    cap = integer(body.get("cap_cents"), "cap_cents", 10000000)
    if state == "active" and (not confirmed or not cpm or not cap):
        fail("Active campaign requires confirmed terms, rights, positive CPM and per-clip cap")
    return (title, platform, url(body.get("url"), "campaign URL"), rules, rights,
            int(confirmed), state, deadline, cpm, minimum, cap)


def check_campaign(campaign):
    if campaign["state"] != "active" or not campaign["confirmed"]:
        fail("Campaign not active or terms not confirmed")
    if campaign["deadline"] and campaign["deadline"] < datetime.now(timezone.utc).date().isoformat():
        fail("Campaign deadline has passed (UTC)")


def check_source(c, sid):
    s = lookup(c, "sources", sid)
    if s["kind"] != "video" or not s["rel_path"]:
        fail("Import a local video in the Library first")
    if s["usage_status"] not in ("мой материал", "разрешено"):
        fail("Source must be owned or explicitly authorized for commercial clipping")
    if s["usage_status"] == "разрешено" and not (s["provenance"] or "").strip():
        fail("Record permission/provenance in the Library")
    if not s["duration"] or s["duration"] <= 0:
        fail("Wait for the technical probe")
    path = config.safe_path(s["rel_path"])
    if not path.is_file():
        fail("Source file not found")
    return s, path


def transcript(c, sid):
    r = c.execute("SELECT rel_path FROM artifacts WHERE source_id=? AND kind='transcript' ORDER BY created_at DESC,rowid DESC LIMIT 1", (sid,)).fetchone()
    if not r:
        return []
    p = config.safe_path(r["rel_path"])
    return json.loads(p.read_text(encoding="utf-8")).get("segments", []) if p.is_file() else []


def suggest(segments, duration, minimum=15, maximum=45, count=10):
    """Deterministic transcript shortlist; not a virality prediction."""
    if not 5 <= minimum <= maximum <= 120 or not 1 <= count <= 10:
        fail("Duration range 5–120 seconds; 1–10 candidates")
    segs = sorted((s for s in segments if not s.get("unclear") and s.get("text", "").strip()
                   and 0 <= s["start"] < s["end"] <= duration), key=lambda s:s["start"])
    options = []
    for i, first in enumerate(segs):
        start, last_end, parts = first["start"], first["start"], []
        for s in segs[i:]:
            end = s["end"]
            if end-start > maximum or (parts and s["start"]-last_end > 3):
                break
            parts.append(s["text"].strip())
            last_end = end
            if end-start < minimum:
                continue
            combined = " ".join(parts)
            score = min(len(combined.split()) / max(end-start, 1), 4) + int("?" in parts[0]) + .8*combined.endswith((".", "!", "?"))
            options.append({"t0":round(start,2), "t1":round(end,2), "hook":parts[0][:180],
                            "excerpt":combined[:700], "heuristic_score":round(score,2)})
    result = []
    for x in sorted(options, key=lambda x:(-x["heuristic_score"],x["t0"],x["t1"])):
        if not any(max(x["t0"],y["t0"]) < min(x["t1"],y["t1"]) for y in result):
            result.append(x)
        if len(result) == count:
            break
    return result


def estimate(item, campaign):
    views = item["eligible_views"]
    if item["status"] not in ("submitted","approved","paid") or views < campaign["min_views"]:
        return 0
    return min(campaign["cap_cents"], views * campaign["cpm_cents"] // 1000)


@router.get("")
def overview():
    with closing(db.open_db()) as c:
        campaigns = [dict(r) for r in c.execute("SELECT * FROM clip_campaigns ORDER BY created_at DESC")]
        by_id = {r["id"]:r for r in campaigns}
        clips = [dict(r) for r in c.execute("SELECT * FROM clipping_items ORDER BY created_at DESC,id")]
        for item in clips:
            item["estimated_cents"] = estimate(item, by_id[item["campaign_id"]])
        return {"campaigns":campaigns, "clips":clips, "discovery":DISCOVERY,
                "totals":{"estimated_unpaid_cents":sum(x["estimated_cents"] for x in clips if x["status"]!="paid"),
                          "paid_cents":sum(x["paid_cents"] for x in clips),
                          "cost_cents":sum(x["cost_cents"] for x in clips)},
                "sources":[dict(r) for r in c.execute("SELECT id,title,duration,usage_status FROM sources WHERE kind='video' AND rel_path IS NOT NULL AND is_test=0 ORDER BY imported_at DESC")]}


@router.post("/campaigns")
def create_campaign(body: dict = Body(...)):
    fields = campaign_fields(body)
    cid = db.new_id("campaign")
    with closing(db.open_db()) as c:
        c.execute("INSERT INTO clip_campaigns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (cid,*fields,db.now(),db.now()))
    return {"id":cid}


@router.put("/campaigns/{cid}")
def update_campaign(cid: str, body: dict = Body(...)):
    fields = campaign_fields(body)
    with closing(db.open_db()) as c, db.tx(c):
        old = lookup(c,"clip_campaigns",cid)
        # Preserve financial and rights terms after any clip exists; use a new campaign for new terms.
        if c.execute("SELECT 1 FROM clipping_items WHERE campaign_id=? LIMIT 1",(cid,)).fetchone():
            new = fields
            if tuple(old[k] for k in ("title","platform","url","rules","rights","confirmed")) != new[:6] or tuple(old[k] for k in ("deadline","cpm_cents","min_views","cap_cents")) != new[7:]:
                fail("Existing clips: only campaign state can change; create a new campaign for revised terms")
        c.execute("UPDATE clip_campaigns SET title=?,platform=?,url=?,rules=?,rights=?,confirmed=?,state=?,deadline=?,cpm_cents=?,min_views=?,cap_cents=?,updated_at=? WHERE id=?",
                  (*fields,db.now(),cid))
    return {"id":cid}


@router.get("/suggest/{sid}")
def candidates(sid: str, minimum: int=15, maximum: int=45, count: int=10):
    with closing(db.open_db()) as c:
        s,_ = check_source(c,sid)
        segs = transcript(c,sid)
        return {"candidates":suggest(segs,s["duration"],minimum,maximum,count),"has_transcript":bool(segs),
                "method":"transcript heuristic; review every cut"}


@router.post("/batch")
def create_batch(body: dict = Body(...)):
    items = body.get("items")
    if not isinstance(items,list) or not 1 <= len(items) <= 10 or not all(isinstance(x,dict) for x in items):
        fail("Provide 1–10 ranges")
    with closing(db.open_db()) as c, db.tx(c):
        campaign = lookup(c,"clip_campaigns",text(body.get("campaign_id"),"campaign_id",100))
        check_campaign(campaign)
        s,_ = check_source(c,text(body.get("source_id"),"source_id",100))
        ids = []
        for x in items:
            t0,t1 = seconds(x.get("t0")),seconds(x.get("t1"))
            if not 0 <= t0 < t1 <= s["duration"] or not 5 <= t1-t0 <= 120:
                fail("Range must be within source, 5–120 seconds")
            iid = db.new_id("clip")
            try:
                c.execute("INSERT INTO clipping_items(id,campaign_id,source_id,t0,t1,hook,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                          (iid,campaign["id"],s["id"],t0,t1,text(x.get("hook",""),"hook",500,False),db.now(),db.now()))
            except sqlite3.IntegrityError:
                fail("Duplicate range in this campaign",409)
            ids.append(iid)
        return {"ids":ids}


def srt_time(t):
    ms = max(0,round(t*1000))
    h,ms = divmod(ms,3600000)
    m,ms = divmod(ms,60000)
    s,ms = divmod(ms,1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def captions(segments,t0,t1):
    lines = []
    for seg in segments:
        if seg.get("unclear") or not seg.get("text","").strip():
            continue
        a,b = max(seg["start"],t0),min(seg["end"],t1)
        if a >= b:
            continue
        if a > seg["start"] or b < seg["end"]:
            content = "".join(w["w"] for w in seg.get("words",[]) if w["s"] >= a-.05 and w["e"] <= b+.05)
        else:
            content = seg["text"]
        content = " ".join(content.split())
        if content:
            lines.append(f"{len(lines)+1}\n{srt_time(a-t0)} --> {srt_time(b-t0)}\n{content}\n")
    return "\n".join(lines)


@router.post("/clips/{iid}/render")
def render(iid: str, body: dict=Body(default={})):
    burn = body.get("burn_captions",False)
    if type(burn) is not bool:
        fail("burn_captions must be boolean")
    with closing(db.open_db()) as c:
        with db.tx(c):
            item = lookup(c,"clipping_items",iid)
            if item["status"] == "rendering":
                if (datetime.now(timezone.utc)-datetime.fromisoformat(item["updated_at"])).total_seconds() < 600:
                    fail("Render already in progress",409)
            elif item["status"] not in ("planned","failed","rendered"):
                fail("Published clips cannot be overwritten")
            check_campaign(lookup(c,"clip_campaigns",item["campaign_id"]))
            _,src = check_source(c,item["source_id"])
            subs = captions(transcript(c,item["source_id"]),item["t0"],item["t1"])
            if burn and not subs:
                fail("No usable captions; transcribe first or render without them")
            c.execute("UPDATE clipping_items SET status='rendering',error=NULL,output_rel=NULL,captions_rel=NULL,updated_at=? WHERE id=?",(db.now(),iid))
        try:
            folder = config.safe_path(f"exports/clipping/{iid}")
            folder.mkdir(parents=True,exist_ok=True)
            mp4,srt,tmp = folder/"clip.mp4",folder/"captions.srt",folder/"render.tmp.mp4"
            vf = "scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
            with tempfile.TemporaryDirectory(prefix="studio-clip-",dir="/tmp") as td:
                sub = Path(td)/"captions.srt"
                if burn:
                    sub.write_text(subs,encoding="utf-8")
                    vf += f",subtitles={sub}:force_style='FontSize=18,Alignment=2,MarginV=36,Outline=2'"
                result = media.run_ffmpeg(["-y","-ss",str(item["t0"]),"-i",str(src),"-t",str(round(item["t1"]-item["t0"],2)),
                                           "-map","0:v:0","-map","0:a:0?","-vf",vf,"-r","30",
                                           "-c:v","libx264","-preset","veryfast","-crf","23","-pix_fmt","yuv420p",
                                           "-c:a","aac","-b:a","128k","-movflags","+faststart",str(tmp)],timeout=180)
            if result.returncode or not tmp.is_file() or not tmp.stat().st_size:
                raise RuntimeError("FFmpeg failed: "+result.stderr[-1000:])
            tmp.replace(mp4)
            if subs:
                srt.write_text(subs,encoding="utf-8")
            else:
                srt.unlink(missing_ok=True)
            c.execute("UPDATE clipping_items SET status='rendered',output_rel=?,captions_rel=?,updated_at=? WHERE id=?",
                      (config.rel(mp4),config.rel(srt) if subs else None,db.now(),iid))
        except Exception as exc:
            c.execute("UPDATE clipping_items SET status='failed',error=?,updated_at=? WHERE id=?",(str(exc)[:1500],db.now(),iid))
            fail("Render failed: "+str(exc)[:500],422)
        return lookup(c,"clipping_items",iid)


@router.patch("/clips/{iid}")
def update_clip(iid: str, body: dict=Body(...)):
    with closing(db.open_db()) as c, db.tx(c):
        item = lookup(c,"clipping_items",iid)
        status = body.get("status",item["status"])
        if not isinstance(status,str) or (status!=item["status"] and status not in TRANSITIONS.get(item["status"],set())):
            fail("Invalid status transition")
        if item["status"]=="rendering":
            fail("Clip is rendering",409)
        post = body.get("post_url",item["post_url"])
        if post:
            post = url(post,"post URL")
        elif status in ("published","submitted","approved","paid","rejected"):
            fail("Published clip requires post URL")
        else:
            post = None
        if status in ("published","submitted") and status!=item["status"]:
            check_campaign(lookup(c,"clip_campaigns",item["campaign_id"]))
            check_source(c,item["source_id"])
        if item["status"] in ("submitted","approved","paid","rejected") and post!=item["post_url"]:
            fail("Submitted post URL is immutable")
        views = integer(body.get("eligible_views",item["eligible_views"]),"eligible_views")
        cost = integer(body.get("cost_cents",item["cost_cents"]),"cost_cents")
        paid = integer(body.get("paid_cents",item["paid_cents"]),"paid_cents")
        if paid and status!="paid":
            fail("Record payout only after receipt")
        if status=="paid" and not paid:
            fail("Enter actual positive payout")
        if views and status not in ("submitted","approved","paid","rejected"):
            fail("Enter campaign-reported eligible views after submission")
        try:
            c.execute("UPDATE clipping_items SET status=?,post_url=?,eligible_views=?,cost_cents=?,paid_cents=?,updated_at=? WHERE id=?",
                      (status,post,views,cost,paid,db.now(),iid))
        except sqlite3.IntegrityError:
            fail("Post URL already recorded",409)
        return lookup(c,"clipping_items",iid)
