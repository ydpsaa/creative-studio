"""Version-aware project exchange with the owner's website. No paid generation."""
from __future__ import annotations
import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from . import config, db, projects

ROOT = config.DATA_DIR / 'site' / 'workspace'

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def project_fingerprint(p):
    return digest({'title':p['title'],'brief':p['brief'],'docs':{k:v['content'] for k,v in p['docs'].items() if k!='sources'}})

def fields(p):
    b=p['brief']; docs=p['docs']
    content=lambda name, default='':docs.get(name,{}).get('content',default)
    raw=content('storyboard',[])
    shots=[]
    for i,row in enumerate(raw if isinstance(raw,list) else []):
        duration=max(.1,min(3600,float(row.get('t_end',0) or 0)-float(row.get('t_start',0) or 0)))
        shots.append({'id':'scene_'+str(i+1),'duration':duration,'visual':str(row.get('visual',''))[:3000],
                      'audio':str(row.get('voice') or row.get('sound') or '')[:3000],'source':'Графика','ready':False})
    voice={'synthetic':'elevenlabs','elevenlabs':'elevenlabs','own_recording':'own_recording','on_camera':'live','live':'live'}.get(b.get('voice_source'),'pending')
    out={'title':p['title'],'brief':str(b.get('topic') or ''),'language':str(b.get('language') or 'EN'),
         'duration':str(b.get('duration') or ''),'script':content('script'),'voiceText':content('voiceover'),
         'voice':voice,'editNotes':content('edit-notes'),'shots':shots[:80]}
    return out

def merge_fields(p,plan,previous):
    """Only local fields changed since the last successful exchange replace web fields."""
    fresh=fields(p)
    if not previous:return fresh
    baseline=previous.get('fields',{})
    changed={k:v for k,v in fresh.items() if v!=baseline.get(k)}
    return changed

def apply_remote(con,p,plan):
    data=plan['data']; local=fields(p); brief=dict(p['brief'])
    brief.update(topic=data['brief'],language=data['language'],duration=data['duration'])
    if data['voice']!=local['voice']:brief['voice_source']=data['voice']
    old_shots=p['docs'].get('storyboard',{}).get('content',[])
    docs={}
    for key,name in [('script','script'),('voiceText','voiceover'),('editNotes','edit-notes')]:
        if data[key]!=local[key]:docs[name]=data[key]
    if data['shots']!=local['shots']:
        rows=[];t=0
        for i,shot in enumerate(data['shots']):
            base=old_shots[i] if isinstance(old_shots,list) and i<len(old_shots) else {}
            rows.append({**base,'scene':i+1,'t_start':t,'t_end':t+shot['duration'],'timing':base.get('timing','оценка'),
                         'visual':shot['visual'],'voice':shot['audio']})
            t+=shot['duration']
        docs['storyboard']=rows
    with db.tx(con):
        if brief!=p['brief'] or data['title']!=p['title']:
            con.execute('UPDATE projects SET title=?,brief=?,updated_at=? WHERE id=?',(data['title'],db.j(brief),db.now(),p['id']))
            projects._save_doc(con,p['id'],'brief',projects._brief_md(data['title'],brief),'site-sync',note='Правка с сайта')
        for name,content in docs.items():
            prior=p['docs'].get(name,{})
            projects._save_doc(con,p['id'],name,content,'site-sync',kids=prior.get('knowledge_ids'),sids=prior.get('source_ids'),note='Версия сайта '+str(plan['revision']))
    projects.write_files(con,p['id'])
    folder=config.DATA_DIR/'projects'/p['id']
    atomic(folder/'site-plan.json',plan)
    return projects.get(con,p['id'])

def ensure_project(con,plan):
    pid=plan['source_project_id'] or 'site_'+plan['id']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',pid):raise ValueError('Неверный проект')
    p=projects.get(con,pid)
    if p:return p
    if plan['source_project_id']:raise ValueError('Исходный локальный проект не найден')
    data=plan['data'];b={'topic':data['brief'],'brand_id':data['brand_id'],'mode':data['mode'],'language':data['language'],
        'duration':data['duration'],'voice_source':data['voice'],'assumptions':[]}
    with db.tx(con):
        con.execute('INSERT INTO projects(id,title,brand_id,mode,status,brief,created_at,updated_at,is_test) VALUES(?,?,?,?,?,?,?,?,0)',
                    (pid,data['title'],data['brand_id'],data['mode'],'brief',db.j(b),db.now(),db.now()))
        projects._save_doc(con,pid,'brief',projects._brief_md(data['title'],b),'site-sync',note='Создан на сайте')
    return projects.get(con,pid)

def archive_plan(client,p,plan,records):
    folder=ROOT/plan['id']; assets=folder/'files';assets.mkdir(parents=True,exist_ok=True)
    source_files=[r for r in records if r['role']=='source']
    for file in source_files:
        dest=assets/file['id']
        if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=file['sha256']:
            response=client.h.get('/api/studio/production/files/'+file['id']);response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest()!=file['sha256']:raise ValueError('Контрольная сумма файла не совпадает')
            temp=dest.with_suffix('.part');temp.write_bytes(response.content);temp.replace(dest)
    signature=digest({'revision':plan['revision'],'files':[(f['id'],f['sha256']) for f in source_files]})
    state_path=folder/'package-state.json'
    prior=json.loads(state_path.read_text()) if state_path.exists() else {}
    if prior.get('signature')==signature:return
    output=folder/f"{plan['id']}-v{plan['revision']}.zip"
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('project.json',json.dumps(plan,ensure_ascii=False,indent=2))
        z.writestr('README.txt','Монтажный пакет Creative Studio. Это исходники и документы, не готовое видео.\nВерсия '+str(plan['revision']))
        for name,doc in p['docs'].items():
            content=doc['content'];ext='json' if isinstance(content,(dict,list)) else 'md'
            z.writestr('documents/'+name+'.'+ext,json.dumps(content,ensure_ascii=False,indent=2) if ext=='json' else str(content))
        csv_out=io.StringIO();writer=csv.DictWriter(csv_out,fieldnames=['id','duration','visual','audio','source','ready']);writer.writeheader();writer.writerows(plan['data']['shots']);z.writestr('storyboard.csv',csv_out.getvalue())
        for f in source_files:z.write(assets/f['id'],'media/'+f['id'][:18]+'-'+Path(f['name']).name)
        z.writestr('manifest.json',json.dumps({'plan_id':plan['id'],'revision':plan['revision'],'files':source_files},ensure_ascii=False,indent=2))
    if output.stat().st_size>32*1024*1024:raise ValueError('Пакет сохранён в студии, но больше лимита сайта 32 МБ')
    r=client.h.post('/api/studio/production/files',params={'plan':plan['id'],'name':output.name,'revision':plan['revision']},content=output.read_bytes(),headers={'Content-Type':'application/octet-stream'})
    client._ok(r);atomic(state_path,{'signature':signature})

def exchange(client):
    ROOT.mkdir(parents=True,exist_ok=True)
    con=db.open_db()
    try:
        cloud=client._ok(client.h.get('/api/studio/production'))
        plans={p['id']:p for p in cloud['plans']}
        statuses={s['id']:s for s in cloud.get('sync',[])}
        # Seed local projects once, without replacing a web plan.
        for row in projects.list_projects(con):
            if row['is_test'] or row['id'].startswith('site_') or row['id'] in plans:continue
            p=projects.get(con,row['id'])
            r=client._ok(client.h.post('/api/studio/production/import',json={'project_id':p['id'],'expected_revision':0,'data':fields(p)}))
            plans[p['id']]=r['plan']
            atomic(ROOT/p['id']/'state.json',{'revision':r['plan']['revision'],'fingerprint':project_fingerprint(p),'fields':fields(p)})
        for plan in plans.values():
            if plan['data']['archived']:continue
            try:
                folder=ROOT/plan['id'];state_path=folder/'state.json'
                previous=json.loads(state_path.read_text()) if state_path.exists() else {}
                p=ensure_project(con,plan);fingerprint=project_fingerprint(p)
                local_changed=bool(previous) and previous.get('fingerprint')!=fingerprint
                remote_changed=bool(previous) and previous.get('revision')!=plan['revision']
                choice=statuses.get(plan['id'],{}).get('state') if statuses.get(plan['id'],{}).get('revision')==plan['revision'] else None
                if choice=='prefer_site':local_changed=False;remote_changed=True
                if choice=='prefer_studio':local_changed=True;remote_changed=False
                if local_changed and remote_changed:
                    client._ok(client.h.post('/api/studio/production/ack',json={'id':plan['id'],'revision':plan['revision'],'state':'conflict','message':'Есть правки и на сайте, и в студии. Обе версии сохранены; требуется сравнение.'}))
                    continue
                if local_changed:
                    data={**plan['data'],**merge_fields(p,plan,previous)}
                    data['checks']={k:False for k in data['checks']}
                    if data['status']=='опубликован':data['status']='проверка'
                    payload={k:plan[k] for k in ['id','source_project_id','base_hash']}
                    payload.update(kind='plan',expected_revision=plan['revision'],data=data)
                    plan=client._ok(client.h.post('/api/studio/production',json=payload))['plan']
                elif remote_changed or not previous:
                    p=apply_remote(con,p,plan)
                atomic(folder/'plan.json',plan)
                atomic(state_path,{'revision':plan['revision'],'fingerprint':project_fingerprint(p),'fields':fields(p)})
                records=client._ok(client.h.get('/api/studio/production/files',params={'plan':plan['id']}))['files']
                archive_plan(client,p,plan,records)
                if statuses.get(plan['id'],{}).get('state')!='synced' or statuses.get(plan['id'],{}).get('revision')!=plan['revision']:
                    client._ok(client.h.post('/api/studio/production/ack',json={'id':plan['id'],'revision':plan['revision'],'state':'synced','message':'Синхронизировано со студией · монтажный пакет сохранён'}))
            except Exception as e:
                # Report a bounded diagnostic without persisting credentials or paths from requests.
                message='Не удалось синхронизировать проект: '+type(e).__name__
                client._ok(client.h.post('/api/studio/production/ack',json={'id':plan['id'],'revision':plan['revision'],'state':'error','message':message}))
                raise
    finally:con.close()
