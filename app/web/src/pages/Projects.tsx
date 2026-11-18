import { useEffect, useState } from 'react'
import { api, Any, fileUrl, go } from '../api'
import { Err, Evidence, Toast, useLoad } from '../ui'

const BRIEF: [string, string][] = [['topic', 'Тема'], ['goal', 'Цель'], ['audience', 'Аудитория'], ['platform', 'Площадка'],
  ['language', 'Язык'], ['duration', 'Длительность'], ['format', 'Формат'], ['materials', 'Доступные материалы'],
  ['references', 'Референсы'], ['constraints', 'Ограничения'], ['cta', 'Ожидаемое действие зрителя']]
const DOC_LABEL: Record<string, string> = { brief: 'Бриф', concept: 'Концепция', hooks: 'Hooks', script: 'Сценарий', voiceover: 'Озвучка',
  storyboard: 'Раскадровка', assets: 'Ассеты', 'generation-prompts': 'Промпты генерации', 'edit-notes': 'Монтаж', sources: 'Источники', review: 'Мои правки' }

export default function Projects({ id }: { id?: string }) { return id ? <Project id={id} /> : <List /> }

function BriefForm({ init, onSubmit, submitLabel }: { init: Any; onSubmit: (title: string, brief: Any) => void; submitLabel: string }) {
  const brands = useLoad<Any[]>('/api/brands')
  const meta = useLoad<Any>('/api/meta')
  const [title, setTitle] = useState(init?.title || '')
  const [b, setB] = useState<Any>(init?.brief || { mode: 'standard' })
  return (
    <div>
      <div className="kv">
        <div>Название</div><div><input style={{ width: '100%' }} value={title} onChange={(e) => setTitle(e.target.value)} /></div>
        <div>Бренд</div><div><select value={b.brand_id || ''} onChange={(e) => setB({ ...b, brand_id: e.target.value })}>
          <option value="">— без бренда —</option>{(brands.data || []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></div>
        <div>Режим производства</div><div><select value={b.mode || 'standard'} onChange={(e) => setB({ ...b, mode: e.target.value })}>
          {Object.entries(meta.data?.project_modes || {}).map(([k, v]: Any) => <option key={k} value={k}>{v}</option>)}</select>
          {meta.data?.mode_rules?.[b.mode] && <div className="small muted">{meta.data.mode_rules[b.mode]}</div>}</div>
        <div>Источник голоса</div><div><select value={b.voice_source || ''} onChange={(e) => setB({ ...b, voice_source: e.target.value })}>
          <option value="">— по режиму —</option>
          {Object.entries(meta.data?.voice_sources || {}).map(([k, v]: Any) => <option key={k} value={k}>{v}</option>)}</select></div>
        {BRIEF.map(([k, l]) => <><div key={k}>{l}</div><div>{['materials', 'references', 'constraints', 'goal', 'topic'].includes(k) ?
          <textarea style={{ minHeight: 50 }} value={b[k] || ''} onChange={(e) => setB({ ...b, [k]: e.target.value })} /> :
          <input style={{ width: '100%' }} value={b[k] || ''} onChange={(e) => setB({ ...b, [k]: e.target.value })} />}</div></>)}
      </div>
      <button className="primary" style={{ marginTop: 10 }} onClick={() => onSubmit(title, b)}>{submitLabel}</button>
    </div>)
}

function List() {
  const { data } = useLoad<Any[]>('/api/projects')
  const [creating, setCreating] = useState(false)
  const create = async (title: string, brief: Any) => { const p = await api.send('POST', '/api/projects', { title, brief }); go(`/projects/${p.id}`) }
  return (
    <div>
      <h1>Проекты</h1>
      <div className="sub">Бриф → подбор знаний → концепция, hooks, сценарий, озвучка, раскадровка, ассеты → экспорт для монтажа.</div>
      {!creating ? <button className="primary" onClick={() => setCreating(true)}>+ Новый проект</button> :
        <div className="card"><BriefForm init={null} onSubmit={create} submitLabel="Создать проект" /></div>}
      <div className="grid3" style={{ marginTop: 14 }}>
        {(data || []).map((p) => <div key={p.id} className="card click" onClick={() => go(`/projects/${p.id}`)}>
          <b>{p.title}</b><div className="small muted">{p.id} · {p.brand_id || 'без бренда'} · {p.mode} · {p.status}</div>
          <div className="small muted">изменён {p.updated_at?.slice(0, 16)}</div></div>)}
      </div>
    </div>)
}

function Project({ id }: { id: string }) {
  const { data: p, reload, error } = useLoad<Any>(`/api/projects/${id}`, [id])
  const kn = useLoad<Any>(`/api/projects/${id}/knowledge`, [id])
  const [tab, setTab] = useState('brief')
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [exp, setExp] = useState<Any>(null)
  const [imp, setImp] = useState('')
  if (error) return <Err e={error} />
  if (!p) return <div className="muted">Загрузка…</div>

  async function pack() { try { const r = await api.send('POST', `/api/projects/${id}/pack`); setMsg(`Пакет: ${r.knowledge} карточек, ${r.fragments} фрагментов`); setExp({ pack: r.file }) } catch (e: Any) { setErr(e.message) } }
  async function doExport(mode: string) { try { const r = await api.send('POST', `/api/projects/${id}/export?mode=${mode}`); setExp(r); setMsg('Экспорт готов') } catch (e: Any) { setErr(e.message) } }
  async function doImport() {
    try { const r = await api.send('POST', `/api/projects/${id}/import`, JSON.parse(imp))
      if (!r.ok) setErr(r.errors.join('\n')); else { setMsg('Документы импортированы'); setImp(''); reload() } } catch (e: Any) { setErr(e.message) }
  }

  return (
    <div>
      <a onClick={() => go('/projects')}>← Проекты</a>
      <h1>{p.title}</h1>
      <div className="sub">{p.id} · бренд: {p.brand_id || '—'} · режим: {p.mode} · статус: {p.status}</div>
      <Err e={err} />
      <div className="row">
        <button onClick={pack}>Пакет project-generation</button>
        <button onClick={() => doExport('links')}>Экспорт (ссылки)</button>
        <button className="primary" onClick={() => doExport('portable')}>Экспорт ZIP с файлами</button>
      </div>
      {exp && <div className="note">{exp.pack && <>Пакет для Claude: <a href={fileUrl(exp.pack, true)}>{exp.pack}</a></>}
        {exp.zip && <>Архив: <a href={fileUrl(exp.zip, true)}>{exp.zip}</a> · файлов {exp.files}
          {exp.assets?.map((a: Any) => <div key={a.asset_id} className="small">{a.asset_id}: {a.included ? 'файл включён' : a.reason_not_included}</div>)}</>}</div>}
      <div className="tabs">{[...(p.docs ? Object.keys(DOC_LABEL) : []), 'knowledge', 'import', 'history'].map((d) =>
        <button key={d} className={tab === d ? 'on' : ''} onClick={() => setTab(d)}>{DOC_LABEL[d] || { knowledge: 'Подбор знаний', import: 'Импорт ответа', history: 'Версии' }[d]}</button>)}</div>
      {tab === 'brief' && <BriefForm init={p} submitLabel="Сохранить бриф (новая версия)" onSubmit={async (t, b) => { await api.send('PUT', `/api/projects/${id}/brief`, { title: t, brief: b }); setMsg('Бриф сохранён'); reload(); kn.reload() }} />}
      {['concept', 'hooks', 'script', 'voiceover', 'generation-prompts', 'edit-notes', 'review'].includes(tab) &&
        <MdDoc key={tab + (p.docs[tab]?.version || 0)} pid={id} doc={tab} cur={p.docs[tab]} onSaved={() => { setMsg('Сохранено'); reload() }} />}
      {tab === 'sources' && <div className="card"><div className="note">Генерируется автоматически из ID знаний и источников, сохранённых с документами.</div>
        <a href={fileUrl(`projects/${id}/sources.md`)} target="_blank" rel="noreferrer">sources.md</a></div>}
      {tab === 'storyboard' && <TableDoc key={'sb' + (p.docs.storyboard?.version || 0)} pid={id} doc="storyboard" cur={p.docs.storyboard} onSaved={() => { setMsg('Раскадровка сохранена'); reload() }}
        cols={['scene', 'timing', 'visual', 'voice', 'on_screen_text', 'sound', 'transition', 'asset_id', 'asset_task', 'basis']} />}
      {tab === 'assets' && <TableDoc key={'as' + (p.docs.assets?.version || 0)} pid={id} doc="assets" cur={p.docs.assets} onSaved={() => { setMsg('Ассеты сохранены'); reload() }}
        cols={['asset_id', 'description', 'status', 'source_id', 'usage_status', 'how_to_get', 'prompt_ref']} />}
      {tab === 'knowledge' && <div>
        <div className="note">Подбор по брифу (полнотекстовый поиск) с учётом бренда. Карточки других брендов исключены.</div>
        <h3>Предпочтения, действующие в проекте</h3>
        {(kn.data?.preferences || []).map((x: Any) => <div key={x.id} className="small">• [{x.scope}] {x.text}</div>)}
        <h3>Карточки ({kn.data?.cards.length || 0})</h3>
        <table><tbody>{(kn.data?.cards || []).map((k: Any) => <tr key={k.id}><td><a onClick={() => go(`/knowledge/${k.id}`)}>{k.title}</a>
          <div className="small muted">{k.statement}</div></td><td><Evidence e={k.evidence} /></td><td className="small"><code>{k.id}</code></td></tr>)}</tbody></table>
      </div>}
      {tab === 'import' && <div>
        <div className="note">Вставьте JSON project-docs/1 (ответ Claude на пакет project-generation). ID знаний и источников проверяются.</div>
        <textarea style={{ minHeight: 200 }} value={imp} onChange={(e) => setImp(e.target.value)} />
        <button className="primary" onClick={doImport} style={{ marginTop: 6 }}>Импортировать</button></div>}
      {tab === 'history' && <table><thead><tr><th>Документ</th><th>Версия</th><th>Кто</th><th>Когда</th><th>Заметка</th></tr></thead>
        <tbody>{p.versions.map((v: Any, i: number) => <tr key={i}><td>{DOC_LABEL[v.doc] || v.doc}</td><td>v{v.version}</td><td className="small">{v.origin}</td><td className="small">{v.created_at.slice(0, 16)}</td><td className="small">{v.note}</td></tr>)}</tbody></table>}
      <Toast msg={msg} onDone={() => setMsg(null)} />
    </div>)
}

function MdDoc({ pid, doc, cur, onSaved }: { pid: string; doc: string; cur: Any; onSaved: () => void }) {
  const [txt, setTxt] = useState(cur?.content || '')
  const [note, setNote] = useState('')
  useEffect(() => setTxt(cur?.content || ''), [cur?.version]) // eslint-disable-line
  return (
    <div>
      {cur ? <div className="small muted">v{cur.version} · {cur.origin} · {cur.created_at.slice(0, 16)}{cur.knowledge_ids?.length ? ` · знания: ${cur.knowledge_ids.join(', ')}` : ''}</div> :
        <div className="note">Документа ещё нет. Напишите вручную или импортируйте ответ на пакет project-generation.</div>}
      <textarea style={{ minHeight: 380, fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 13 }} value={txt} onChange={(e) => setTxt(e.target.value)} />
      <div className="row" style={{ marginTop: 6 }}>
        <input style={{ flex: 1 }} placeholder="Комментарий к версии" value={note} onChange={(e) => setNote(e.target.value)} />
        <button className="primary" onClick={async () => { await api.send('PUT', `/api/projects/${pid}/docs/${doc}`, { content: txt, note, knowledge_ids: cur?.knowledge_ids }); onSaved() }}>Сохранить новую версию</button>
      </div>
    </div>)
}

function TableDoc({ pid, doc, cur, cols, onSaved }: { pid: string; doc: string; cur: Any; cols: string[]; onSaved: () => void }) {
  const [rows, setRows] = useState<Any[]>(Array.isArray(cur?.content) ? cur.content : [])
  const set = (i: number, c: string, v: string) => setRows(rows.map((r, j) => j === i ? { ...r, [c]: v } : r))
  return (
    <div>
      {cur && <div className="small muted">v{cur.version} · {cur.origin}. Тайминги плановые (оценка), точные — после записи голоса.</div>}
      <div style={{ overflowX: 'auto' }}><table className="sbtable"><thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}<th></th></tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} style={{ minWidth: c === 'scene' ? 50 : 140 }}>
          <textarea value={r[c] ?? ''} onChange={(e) => set(i, c, e.target.value)} /></td>)}
          <td><button onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</button></td></tr>)}</tbody></table></div>
      <div className="row" style={{ marginTop: 6 }}>
        <button onClick={() => setRows([...rows, { [cols[0]]: doc === 'storyboard' ? String(rows.length + 1) : `A${rows.length + 1}` }])}>+ строка</button>
        <button className="primary" onClick={async () => { await api.send('PUT', `/api/projects/${pid}/docs/${doc}`, { content: rows, knowledge_ids: cur?.knowledge_ids }); onSaved() }}>Сохранить новую версию</button>
      </div>
    </div>)
}
