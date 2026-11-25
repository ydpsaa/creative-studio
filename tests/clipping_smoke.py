"""Portable clipping integration test: rights, limits, real render, payout ledger."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "api"))

with tempfile.TemporaryDirectory(prefix="clipping-test-") as tmp:
    os.environ["STUDIO_DATA_DIR"] = tmp
    from fastapi.testclient import TestClient
    from studio import config, db, media, clipping
    from studio.main import app

    client = TestClient(app, headers={"host":"127.0.0.1:8765","x-studio":"1"})
    c = db.open_db()
    path = config.DATA_DIR / "originals" / "fixture.mp4"
    ff = media.run_ffmpeg(["-y","-f","lavfi","-i","color=c=blue:s=320x180:r=25","-t","7","-c:v","libx264","-pix_fmt","yuv420p",str(path)])
    assert ff.returncode == 0, ff.stderr
    c.execute("INSERT INTO sources(id,kind,title,rel_path,imported_at,duration,usage_status,provenance) VALUES (?,?,?,?,?,?,?,?)",
              ("source","video","Fixture",config.rel(path),db.now(),7,"только референс","test"))
    segs = [{"start":0.0,"end":6.0,"text":"A complete example sentence.","words":[]}]
    trans = config.DATA_DIR / "derived" / "transcript.json"
    trans.write_text(json.dumps({"segments":segs}), encoding="utf-8")
    c.execute("INSERT INTO artifacts(id,source_id,kind,rel_path,created_at) VALUES (?,?,?,?,?)",
              ("trans","source","transcript",config.rel(trans),db.now()))
    payload = {"title":"Test","platform":"Vyro","url":"https://vyro.com/creators","rules":"Test only","rights":"Synthetic fixture",
               "confirmed":False,"state":"active","cpm_cents":100,"min_views":5000,"cap_cents":1000}
    assert client.post("/api/clipping/campaigns",json=payload).status_code == 400
    payload["confirmed"] = True
    created = client.post("/api/clipping/campaigns",json=payload)
    assert created.status_code == 200, created.text
    cid=created.json()["id"]
    batch={"campaign_id":cid,"source_id":"source","items":[{"t0":0,"t1":6,"hook":"test"}]}
    assert client.post("/api/clipping/batch",json=batch).status_code == 400
    c.execute("UPDATE sources SET usage_status='мой материал' WHERE id='source'")
    assert client.post("/api/clipping/batch",json=dict(batch,items=[{"t0":0,"t1":8}])).status_code == 400
    suggested=client.get("/api/clipping/suggest/source?minimum=5&maximum=7").json()
    assert len(suggested["candidates"]) == 1
    created=client.post("/api/clipping/batch",json=batch)
    assert created.status_code == 200, created.text
    iid=created.json()["ids"][0]
    assert client.post("/api/clipping/batch",json=batch).status_code == 409
    assert client.put(f"/api/clipping/campaigns/{cid}",json=dict(payload,cpm_cents=999)).status_code == 400
    p=f"/api/clipping/clips/{iid}"
    assert client.patch(p,json={"status":"paid","paid_cents":100}).status_code == 400
    rendered=client.post(p+"/render",json={})
    assert rendered.status_code == 200, rendered.text
    clip=rendered.json()
    assert clip["status"] == "rendered" and clip["captions_rel"]
    video=media.probe(config.safe_path(clip["output_rel"]))
    assert any(s["type"]=="video" and s["width"]==1080 and s["height"]==1920 for s in video["streams"])
    assert client.patch(p,json={"status":"published","post_url":"https://example.com/post"}).status_code==200
    assert client.patch(p,json={"status":"submitted","eligible_views":4999}).status_code==200
    assert client.get("/api/clipping").json()["totals"]["estimated_unpaid_cents"]==0
    assert client.patch(p,json={"eligible_views":20000,"cost_cents":50}).status_code==200
    assert client.get("/api/clipping").json()["totals"]["estimated_unpaid_cents"]==1000
    assert client.patch(p,json={"status":"approved"}).status_code==200
    assert client.patch(p,json={"status":"paid","paid_cents":800}).status_code==200
    assert client.get("/api/clipping").json()["totals"]=={"estimated_unpaid_cents":0,"paid_cents":800,"cost_cents":50}
    assert client.post(p+"/render",json={}).status_code==400
    assert clipping.captions(segs,1,5)=="" # no word timings for partial segment
    c.close()
    print("Clipping smoke checks passed")
