import { useState } from 'react'
import { Any, api, fileUrl } from '../api'
import { Err, useLoad } from '../ui'

const blank = () => ({title:'',platform:'Content Rewards',url:'',rules:'',rights:'',confirmed:false,state:'draft',deadline:'',cpm_cents:100,min_views:5000,cap_cents:20000})
const dollars = (v:number) => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format((v||0)/100)
const next: Record<string,string[]> = {rendered:['published'],published:['submitted'],submitted:['approved','rejected'],approved:['paid','rejected']}
const labels: Record<string,string> = {planned:'Запланирован',rendering:'Рендер',failed:'Ошибка',rendered:'Готов',published:'Опубликован',submitted:'Отправлен',approved:'Одобрен',paid:'Выплачен',rejected:'Отклонён'}
type Range = {t0:number;t1:number;hook:string;excerpt?:string;heuristic_score?:number}

function ClipRow({item,busy,onSave,onRender}:{item:Any;busy:boolean;onSave:(id:string,body:Any)=>Promise<boolean>;onRender:(id:string)=>void}) {
  const [edit,setEdit]=useState(false)
  const [state,setState]=useState(item.status)
  const [post,setPost]=useState(item.post_url||'')
  const [views,setViews]=useState(item.eligible_views)
  const [cost,setCost]=useState(item.cost_cents/100)
  const [paid,setPaid]=useState(item.paid_cents/100)
  return <div className="card" style={{marginBottom:10}}>
    <div className="row"><b>{item.t0.toFixed(2)} – {item.t1.toFixed(2)} с</b><span className="badge">{labels[item.status]||item.status}</span><span className="muted small">{item.hook}</span></div>
    <div className="row" style={{marginTop:8}}>
      {['planned','failed','rendered','rendering'].includes(item.status)&&<button disabled={busy} onClick={()=>onRender(item.id)}>{item.status==='rendering'?'Повторить зависший рендер':'Рендер 9:16'}</button>}
      {item.output_rel&&<><a href={fileUrl(item.output_rel)} target="_blank" rel="noreferrer">Смотреть ↗</a><a href={fileUrl(item.output_rel,true)}>MP4</a></>}
      {item.captions_rel&&<a href={fileUrl(item.captions_rel,true)}>SRT</a>}
      <span className="small">Оценка {dollars(item.estimated_cents)} · Получено {dollars(item.paid_cents)} · Расход {dollars(item.cost_cents)}</span>
      <button disabled={busy||item.status==='rendering'} onClick={()=>{setState(item.status);setPost(item.post_url||'');setViews(item.eligible_views);setCost(item.cost_cents/100);setPaid(item.paid_cents/100);setEdit(!edit)}}>{edit?'Закрыть':'Обновить учёт'}</button>
    </div>
    {item.error&&<p className="small" style={{color:'var(--bad)'}}>{item.error}</p>}
    {edit&&<form style={{marginTop:12}} onSubmit={async e=>{e.preventDefault();if(await onSave(item.id,{status:state,post_url:post,eligible_views:Number(views),cost_cents:Math.round(Number(cost)*100),paid_cents:Math.round(Number(paid)*100)}))setEdit(false)}}>
      <div className="row"><label>Статус <select value={state} onChange={e=>setState(e.target.value)}>{[item.status,...(next[item.status]||[])].map(s=><option key={s} value={s}>{labels[s]}</option>)}</select></label>
      <input type="url" placeholder="https://… ссылка на пост" value={post} onChange={e=>setPost(e.target.value)} disabled={['submitted','approved','paid','rejected'].includes(item.status)}/></div>
      <div className="row" style={{marginTop:8}}>
        <label>Засчитанные просмотры <input type="number" min="0" value={views} onChange={e=>setViews(Number(e.target.value))}/></label>
        <label>Расход $ <input type="number" min="0" step=".01" value={cost} onChange={e=>setCost(Number(e.target.value))}/></label>
        {state==='paid'&&<label>Получено $ <input required type="number" min=".01" step=".01" value={paid} onChange={e=>setPaid(Number(e.target.value))}/></label>}
        <button className="primary" disabled={busy}>Сохранить</button>
      </div><p className="small muted">Только просмотры, подтверждённые кампанией, и фактически полученные выплаты. Статус — ручная отметка, не проверка площадки.</p>
    </form>}
  </div>
}

export default function Clipping(){
  const {data,error,reload}=useLoad<Any>('/api/clipping')
  const [message,setMessage]=useState('')
  const [busy,setBusy]=useState('')
  const [draft,setDraft]=useState(blank)
  const [editing,setEditing]=useState('')
  const [campaign,setCampaign]=useState('')
  const [source,setSource]=useState('')
  const [ranges,setRanges]=useState<Range[]>([])
  const [burn,setBurn]=useState(false)
  const [min,setMin]=useState(15)
  const [max,setMax]=useState(45)
  const field=(k:keyof ReturnType<typeof blank>,v:Any)=>setDraft(d=>({...d,[k]:v}))
  const perform=async(name:string,fn:()=>Promise<void>)=>{
    setBusy(name);setMessage('')
    try{await fn();reload();return true}catch(e){setMessage(e instanceof Error?e.message:String(e));reload();return false}finally{setBusy('')}
  }
  const chosen=data?.campaigns?.find((c:Any)=>c.id===campaign)
  const clips=(data?.clips||[]).filter((x:Any)=>!campaign||x.campaign_id===campaign)
  const pending=clips.filter((x:Any)=>['planned','failed'].includes(x.status))
  const locked=!!editing&&(data?.clips||[]).some((x:Any)=>x.campaign_id===editing)
  const render=(id:string)=>perform('Рендер '+id,async()=>{await api.send('POST','/api/clipping/clips/'+id+'/render',{burn_captions:burn})})
  const renderBatch=()=>perform('Пакетный рендер',async()=>{
    const errors:string[]=[]
    for(const x of pending.slice(0,10)){
      try{await api.send('POST','/api/clipping/clips/'+x.id+'/render',{burn_captions:burn})}catch(e){errors.push(x.id+': '+String(e))}
      reload()
    }
    if(errors.length)throw Error(errors.join('\n'))
  })
  return <><h1>Clipping Factory</h1><div className="sub">Органический клипинг · разрешённые исходники · ручная публикация · учёт реальных выплат</div>
    <Err e={error}/><Err e={message||null}/>{busy&&<p className="muted">{busy}…</p>}
    <div className="grid3">
      <div className="card"><div className="small muted">Оценка невыплаченного</div><h1>{data?dollars(data.totals.estimated_unpaid_cents):'—'}</h1></div>
      <div className="card"><div className="small muted">Получено (ручной учёт)</div><h1>{data?dollars(data.totals.paid_cents):'—'}</h1></div>
      <div className="card"><div className="small muted">Получено минус расходы</div><h1>{data?dollars(data.totals.paid_cents-data.totals.cost_cents):'—'}</h1></div>
    </div>
    <h2>1. Opportunity Center</h2>
    <div className="grid3">{(data?.discovery||[]).map((d:Any)=><div key={d.name} className="card"><b>{d.name}</b><p className="small muted">{d.note}</p><a href={d.url} target="_blank" rel="noreferrer">Кампании ↗</a></div>)}</div>
    <p className="small muted">Каталоги открываются вручную. Перенесите реальный бриф и проверьте доступность, язык, географию, бюджет и лимиты. Платное продвижение, боты и искусственная активность не используются.</p>
    <div className="card"><h2 style={{marginTop:0}}>{editing?'Редактировать кампанию':'Добавить кампанию'}</h2>
      <form onSubmit={e=>{e.preventDefault();void perform('Сохранение',async()=>{const r=await api.send(editing?'PUT':'POST','/api/clipping/campaigns'+(editing?'/'+editing:''),draft);setCampaign(r.id);setEditing('');setDraft(blank())})}}>
        <fieldset disabled={locked||!!busy} style={{border:0,padding:0,margin:0,minWidth:0}}>
          <div className="row"><input required placeholder="Название" value={draft.title} onChange={e=>field('title',e.target.value)}/>
          <select value={draft.platform} onChange={e=>field('platform',e.target.value)}>{['Content Rewards','Vyro','Clipster','Other'].map(x=><option key={x}>{x}</option>)}</select>
          <input required type="url" placeholder="https://… URL брифа" value={draft.url} onChange={e=>field('url',e.target.value)}/></div>
          <div className="row" style={{margin:'10px 0'}}>
            <label>CPM, $ <input type="number" min="0" step=".01" required value={draft.cpm_cents/100} onChange={e=>field('cpm_cents',Math.round(Number(e.target.value)*100))}/></label>
            <label>Мин. просмотров <input type="number" min="0" required value={draft.min_views} onChange={e=>field('min_views',Number(e.target.value))}/></label>
            <label>Лимит/ролик, $ <input type="number" min="0" step=".01" required value={draft.cap_cents/100} onChange={e=>field('cap_cents',Math.round(Number(e.target.value)*100))}/></label>
            <label>Дедлайн (UTC) <input type="date" value={draft.deadline} onChange={e=>field('deadline',e.target.value)}/></label>
          </div>
          <label>Правила: язык, страны, формат, бюджет, срок подачи, ограничения</label>
          <textarea value={draft.rules} onChange={e=>field('rules',e.target.value)} placeholder="Скопируйте условия конкретной кампании. Ставки выше — только пример."/>
          <label>Права: лицензия на видео и звук, разрешение владельца</label>
          <textarea value={draft.rights} onChange={e=>field('rights',e.target.value)} placeholder="Ссылка или описание разрешения владельца"/>
          <label><input type="checkbox" checked={draft.confirmed} onChange={e=>field('confirmed',e.target.checked)}/> Я проверил условия, географию аккаунта, права и разрешённую органическую публикацию.</label>
        </fieldset>
        {locked&&<p className="small muted">Есть клипы: условия зафиксированы. Для новых условий создайте новую кампанию.</p>}
        <div className="row" style={{marginTop:12}}><label>Статус <select value={draft.state} onChange={e=>field('state',e.target.value)}><option value="draft">Черновик</option><option value="active">Активна</option><option value="closed">Закрыта</option></select></label><button disabled={!!busy} className="primary">Сохранить</button>{editing&&<button type="button" onClick={()=>{setEditing('');setDraft(blank())}}>Отмена</button>}</div>
      </form>
    </div>
    <h2>2. Производство — до 10 клипов за пакет</h2>
    <div className="card">
      <div className="row"><select disabled={!!busy} value={campaign} onChange={e=>setCampaign(e.target.value)}><option value="">Выбрать кампанию</option>{(data?.campaigns||[]).map((c:Any)=><option key={c.id} value={c.id}>{c.title} · {c.state}</option>)}</select>
        {chosen&&<button disabled={!!busy} onClick={()=>{setEditing(chosen.id);setDraft({...blank(),title:chosen.title,platform:chosen.platform,url:chosen.url,rules:chosen.rules,rights:chosen.rights,confirmed:!!chosen.confirmed,state:chosen.state,deadline:chosen.deadline,cpm_cents:chosen.cpm_cents,min_views:chosen.min_views,cap_cents:chosen.cap_cents})}}>Правила / изменить</button>}
        <select disabled={!!busy} value={source} onChange={e=>{setSource(e.target.value);setRanges([])}}><option value="">Видео из библиотеки</option>{(data?.sources||[]).map((s:Any)=><option key={s.id} value={s.id}>{s.title||s.id} · {s.usage_status} · {s.duration||'?'} c</option>)}</select><a href="#/library">Библиотека ↗</a>
      </div>
      <div className="row" style={{marginTop:12}}>
        <label>Мин. секунд <input type="number" min="5" max="120" value={min} onChange={e=>setMin(Number(e.target.value))}/></label>
        <label>Макс. секунд <input type="number" min="5" max="120" value={max} onChange={e=>setMax(Number(e.target.value))}/></label>
        <button disabled={!source||!!busy} onClick={()=>void perform('Подбор',async()=>{const r=await api.get('/api/clipping/suggest/'+source+'?minimum='+min+'&maximum='+max);setRanges(r.candidates);if(!r.candidates.length)setMessage('Нет подходящих фрагментов. Проверьте транскрипцию или задайте интервалы вручную.')})}>Найти фрагменты по транскрипту</button>
        <button disabled={!source||ranges.length>=10||!!busy} onClick={()=>setRanges(r=>[...r,{t0:0,t1:30,hook:''}])}>+ Интервал</button>
      </div>
      <p className="small muted">Подбор использует плотность речи и завершённость фраз, а не прогноз просмотров. Проверяйте каждый таймкод и хук. Хук — заметка для редактора, не новая озвучка.</p>
      {ranges.map((r,i)=><div key={i} className="card" style={{marginTop:8}}><div className="row">
        <label>Начало <input type="number" min="0" step=".01" value={r.t0} onChange={e=>setRanges(a=>a.map((x,j)=>j===i?{...x,t0:Number(e.target.value)}:x))}/></label>
        <label>Конец <input type="number" min="0" step=".01" value={r.t1} onChange={e=>setRanges(a=>a.map((x,j)=>j===i?{...x,t1:Number(e.target.value)}:x))}/></label>
        <input placeholder="Хук / заметка" value={r.hook} onChange={e=>setRanges(a=>a.map((x,j)=>j===i?{...x,hook:e.target.value}:x))}/><button disabled={!!busy} onClick={()=>setRanges(a=>a.filter((_,j)=>j!==i))}>Удалить</button></div>
        {r.excerpt&&<p className="small muted">{r.excerpt}</p>}
      </div>)}
      <div className="row" style={{marginTop:12}}><button className="primary" disabled={chosen?.state!=='active'||!source||!ranges.length||!!busy} onClick={()=>void perform('Создание пакета',async()=>{await api.send('POST','/api/clipping/batch',{campaign_id:campaign,source_id:source,items:ranges});setRanges([])})}>Создать пакет ({ranges.length})</button></div>
    </div>
    <h2>3. Рендер, публикация и выплаты</h2>
    <div className="row" style={{marginBottom:10}}><label><input type="checkbox" checked={burn} onChange={e=>setBurn(e.target.checked)}/> Вшить авто-субтитры (FFmpeg с libass)</label><button disabled={!pending.length||!!busy} onClick={()=>void renderBatch()}>Рендер пакета ({Math.min(10,pending.length)})</button><button onClick={reload}>Обновить</button></div>
    <p className="small muted">MP4 / SRT остаются на Mac. Рендер сохраняет весь кадр с полями. Проверьте видео и субтитры. Оценка выплат не учитывает исчерпание бюджета кампании и комиссии.</p>
    {clips.map((item:Any)=><ClipRow key={item.id} item={item} busy={!!busy} onRender={id=>void render(id)} onSave={(id,body)=>perform('Сохранение',async()=>{await api.send('PATCH','/api/clipping/clips/'+id,body)})}/>)}
    {!clips.length&&<p className="muted">Пока нет клипов.</p>}
  </>
}
