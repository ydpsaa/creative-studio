import { useState } from 'react'
import { Any, fileUrl, fmtT, go } from '../api'
import { Badge, Err, useLoad } from '../ui'

export default function Library() {
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  const [collection, setCollection] = useState('')
  const [brand, setBrand] = useState('')
  const [test, setTest] = useState(false)
  const params = new URLSearchParams({ q, kind, status, collection, brand, include_test: test ? '1' : '0' })
  const { data, error } = useLoad<Any[]>(`/api/sources?${params}`, [])
  const cols = useLoad<Any[]>('/api/collections')
  const brands = useLoad<Any[]>('/api/brands')
  const hits = useLoad<Any[]>(q.length > 1 ? `/api/search?q=${encodeURIComponent(q)}` : null, [])

  return (
    <div>
      <h1>Библиотека</h1>
      <div className="sub">Каждый источник хранится один раз; коллекции, аккаунты, бренды и проекты ссылаются на него по ID.</div>
      <div className="row" style={{ marginBottom: 14 }}>
        <input style={{ width: 320 }} placeholder="Поиск по транскриптам, OCR, заметкам, разборам…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">Все типы</option>
          {['video', 'audio', 'image', 'document', 'url'].map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Любая обработка</option><option value="analyzed">Разобрано</option>
          <option value="awaiting_ai">Ждёт анализа</option><option value="blocked">Недоступно / требует действия</option>
        </select>
        <select value={collection} onChange={(e) => setCollection(e.target.value)}>
          <option value="">Все коллекции</option>
          {(cols.data || []).map((c) => <option key={c.id} value={c.id}>{c.name} ({c.n})</option>)}
        </select>
        <select value={brand} onChange={(e) => setBrand(e.target.value)}>
          <option value="">Любой бренд</option>
          {(brands.data || []).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <label className="small muted"><input type="checkbox" checked={test} onChange={(e) => setTest(e.target.checked)} /> тестовые</label>
      </div>
      <Err e={error} />
      {q.length > 1 && (hits.data || []).length > 0 && (
        <div className="card" style={{ marginBottom: 14 }}>
          <h3>Найдено во фрагментах</h3>
          {(hits.data || []).slice(0, 12).map((h, i) => (
            <div key={i} className="seg" onClick={() => h.etype === 'knowledge' ? go(`/knowledge/${h.eid}`) :
              h.etype === 'project_doc' ? go(`/projects/${h.eid.split(':')[0]}`) : h.sid && go(`/source/${h.sid}?t=${h.t || 0}`)}>
              <span className="tc">{h.t != null ? fmtT(h.t) : h.etype}</span>
              <span><span className="small muted">{h.title} · </span>{h.snip}</span>
            </div>))}
        </div>)}
      <div className="lib">
        {(data || []).map((s) => (
          <div className="card click" key={s.id} onClick={() => go(`/source/${s.id}`)}>
            {s.poster ? <img className="thumb" src={fileUrl(s.poster)} /> :
              <div className="thumb empty">{s.kind === 'url' ? `${s.platform}\n${s.blocked_reason ? 'медиа не получено' : ''}` : s.kind}</div>}
            <div style={{ marginTop: 8, fontWeight: 600 }}>{s.title}</div>
            <div className="small muted">{s.kind} · {s.duration ? `${s.duration.toFixed(1)} с · ` : ''}{s.language !== 'неизвестно' ? s.language : ''} {s.is_test ? '· ТЕСТ' : ''}</div>
            <div className="statusgrid">
              <span className="muted">Получение</span><span><Badge s={s.st_acquire} /></span>
              {s.kind !== 'url' && <><span className="muted">Речь</span><span><Badge s={s.st_speech} /></span>
                <span className="muted">Визуал</span><span><Badge s={s.st_visual} /></span>
                <span className="muted">Звук</span><span><Badge s={s.st_audio} /></span></>}
              <span className="muted">Разбор</span><span><Badge s={s.st_analysis} /></span>
              <span className="muted">Знания</span><span><Badge s={s.st_knowledge} /></span>
            </div>
          </div>))}
      </div>
      {data && data.length === 0 && <div className="muted">Ничего не найдено.</div>}
    </div>
  )
}
