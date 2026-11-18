import { useState } from 'react'
import { api, Any, fileUrl, fmtT, go } from '../api'
import { Err, Evidence, Toast, useLoad } from '../ui'

export default function Knowledge({ id }: { id?: string }) {
  return id ? <Card id={id} /> : <List />
}

function List() {
  const meta = useLoad<Any>('/api/meta')
  const [q, setQ] = useState('')
  const [category, setCategory] = useState('')
  const [evidence, setEvidence] = useState('')
  const [kind, setKind] = useState('')
  const { data, error } = useLoad<Any[]>(`/api/knowledge?${new URLSearchParams({ q, category, evidence, kind })}`, [])
  const m = meta.data
  return (
    <div>
      <h1>Методы и стили</h1>
      <div className="sub">Карточки знаний с источниками и таймкодами. Гипотезы анализа отделены от слов авторов и ваших предпочтений.</div>
      <div className="row" style={{ marginBottom: 12 }}>
        <input style={{ width: 300 }} placeholder="Поиск приёма…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={category} onChange={(e) => setCategory(e.target.value)}><option value="">Все категории</option>
          {(m?.categories || []).map((c: string) => <option key={c}>{c}</option>)}</select>
        <select value={kind} onChange={(e) => setKind(e.target.value)}><option value="">Все типы</option>
          {Object.entries(m?.kinds || {}).map(([k, v]: Any) => <option key={k} value={k}>{v}</option>)}</select>
        <select value={evidence} onChange={(e) => setEvidence(e.target.value)}><option value="">Любая обоснованность</option>
          {Object.entries(m?.evidence || {}).map(([k, v]: Any) => <option key={k} value={k}>{v}</option>)}</select>
      </div>
      <Err e={error} />
      <div className="card">
        <table><thead><tr><th>Карточка</th><th>Тип</th><th>Категория</th><th>Обоснованность</th><th>Источники</th></tr></thead>
          <tbody>{(data || []).map((k) => (
            <tr key={k.id}><td><a onClick={() => go(`/knowledge/${k.id}`)}>{k.title}</a><div className="small muted">{k.statement?.slice(0, 140)}</div></td>
              <td className="small">{m?.kinds[k.kind]}</td><td className="small">{k.category}</td><td><Evidence e={k.evidence} /></td>
              <td className="small">{k.sources.map((s: string) => <a key={s} className="pill" onClick={() => go(`/source/${s}`)}>{s}</a>)}</td></tr>))}</tbody></table>
        {data && data.length === 0 && <div className="muted">Нет карточек. Они появляются после импорта разбора источника.</div>}
      </div>
    </div>)
}

const EDIT_FIELDS: [string, string][] = [['title', 'Название'], ['statement', 'Формулировка'], ['task', 'Задача'],
  ['when_use', 'Когда применять'], ['when_not', 'Когда не применять'], ['conditions', 'Условия'], ['example', 'Пример'],
  ['mistake', 'Типичная ошибка'], ['limitations', 'Ограничения'], ['brand_scope', 'Область бренда (all или brand_id)'], ['format_scope', 'Формат']]

function Card({ id }: { id: string }) {
  const { data: k, error, reload } = useLoad<Any>(`/api/knowledge/${id}`, [id])
  const meta = useLoad<Any>('/api/meta')
  const [edit, setEdit] = useState<Any>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  if (error) return <Err e={error} />
  if (!k) return <div className="muted">Загрузка…</div>
  const start = () => setEdit({ ...Object.fromEntries(EDIT_FIELDS.map(([f]) => [f, k[f] || ''])), steps: (k.steps || []).join('\n'),
    evidence: k.evidence, note: '' })
  async function save() {
    const { note, ...changes } = edit
    changes.steps = changes.steps.split('\n').map((x: string) => x.trim()).filter(Boolean)
    try { await api.send('PATCH', `/api/knowledge/${id}`, { changes, note, base_version: k.version }); setEdit(null); setMsg('Новая версия сохранена'); reload() }
    catch (e: Any) { setErr(e.message) }
  }
  return (
    <div>
      <a onClick={() => go('/knowledge')}>← Все карточки</a>
      <h1>{k.title}</h1>
      <div className="row sub"><Evidence e={k.evidence} /><span>{meta.data?.kinds[k.kind]} · {k.category} · версия {k.version} · <code>{k.id}</code></span></div>
      <Err e={err} />
      {!edit ? <div className="grid2">
        <div className="card">
          {[['Формулировка', 'statement'], ['Задача', 'task'], ['Когда применять', 'when_use'], ['Когда не применять', 'when_not'], ['Условия', 'conditions']].map(([l, f]) =>
            k[f] ? <div key={f}><h3>{l}</h3><div>{k[f]}</div></div> : null)}
          {k.steps?.length > 0 && <><h3>Шаги</h3><ol>{k.steps.map((s: string, i: number) => <li key={i}>{s}</li>)}</ol></>}
          {[['Пример', 'example'], ['Типичная ошибка', 'mistake'], ['Ограничения', 'limitations']].map(([l, f]) =>
            k[f] ? <div key={f}><h3>{l}</h3><div>{k[f]}</div></div> : null)}
          <h3>Область</h3><div className="small">Бренд: {k.brand_scope} · формат: {k.format_scope || '—'} · язык: {k.language || '—'}</div>
          <div style={{ marginTop: 12 }}><button onClick={start}>Исправить карточку</button></div>
        </div>
        <div>
          <div className="card">
            <h3>Доказательства в источниках</h3>
            {k.refs.map((r: Any) => (
              <div key={r.id} style={{ marginBottom: 12 }}>
                <a onClick={() => go(`/source/${r.source_id}?t=${r.t_start ?? 0}`)}>{r.source_title || r.source_id}</a>
                <span className="tc"> {r.t_start != null ? `${fmtT(r.t_start)}–${fmtT(r.t_end)}` : r.page}</span>
                {r.quote && <div className="small">«{r.quote}»</div>}
                {r.frame_path && <img src={fileUrl(r.frame_path)} style={{ width: 120, borderRadius: 6, marginTop: 4, cursor: 'pointer' }}
                  onClick={() => go(`/source/${r.source_id}?t=${r.t_start ?? 0}`)} />}
              </div>))}
            <div className="small muted">Происхождение: {k.origin}</div>
          </div>
          <div className="card" style={{ marginTop: 12 }}>
            <h3>История версий</h3>
            {k.history.map((h: Any) => <div key={h.version} className="small">v{h.version} · {h.changed_at.slice(0, 16)} · {h.author} · {h.note}</div>)}
          </div>
        </div>
      </div> : <div className="card">
        {EDIT_FIELDS.map(([f, l]) => <div key={f} style={{ marginBottom: 8 }}><div className="small muted">{l}</div>
          {f === 'title' || f.endsWith('scope') ? <input style={{ width: '100%' }} value={edit[f]} onChange={(e) => setEdit({ ...edit, [f]: e.target.value })} /> :
            <textarea style={{ minHeight: 60 }} value={edit[f]} onChange={(e) => setEdit({ ...edit, [f]: e.target.value })} />}</div>)}
        <div className="small muted">Шаги (по одному в строке)</div>
        <textarea value={edit.steps} onChange={(e) => setEdit({ ...edit, steps: e.target.value })} />
        <div className="row" style={{ margin: '8px 0' }}><span className="small muted">Обоснованность</span>
          <select value={edit.evidence} onChange={(e) => setEdit({ ...edit, evidence: e.target.value })}>
            {Object.entries(meta.data?.evidence || {}).map(([a, b]: Any) => <option key={a} value={a}>{b}</option>)}</select></div>
        <input style={{ width: '100%' }} placeholder="Что и почему изменено (сохраняется в истории)" value={edit.note} onChange={(e) => setEdit({ ...edit, note: e.target.value })} />
        <div className="row" style={{ marginTop: 8 }}><button className="primary" onClick={save}>Сохранить версию {k.version + 1}</button><button onClick={() => setEdit(null)}>Отмена</button></div>
      </div>}
      <Toast msg={msg} onDone={() => setMsg(null)} />
    </div>)
}
