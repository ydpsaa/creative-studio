import { useState } from 'react'
import { api, Any } from '../api'
import { Badge, Err, Toast, useLoad } from '../ui'

const FIELDS: [string, string, boolean][] = [['kind', 'Тип', false], ['product', 'Продукт / о чём бренд', false], ['default_mode', 'Режим по умолчанию', false], ['modes', 'Доступные режимы', true], ['audience', 'Аудитория', false], ['language', 'Язык', false],
  ['tone_of_voice', 'Tone of voice', false], ['accent_color', 'Акцентный цвет', false], ['palette', 'Палитра', true], ['typography', 'Шрифты', false], ['principle', 'Принцип подачи', false], ['visual_rules', 'Визуальные правила', true],
  ['allowed_assets', 'Разрешённые ассеты', true], ['prohibitions', 'Запреты', true], ['examples', 'Утверждённые примеры', true]]

export default function Brands() {
  const brands = useLoad<Any[]>('/api/brands')
  const prefs = useLoad<Any[]>('/api/preferences')
  const meta = useLoad<Any>('/api/meta')
  const [sel, setSel] = useState<Any>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [np, setNp] = useState({ scope: 'studio', scope_id: '', text: '' })

  const open = (b: Any) => setSel({ id: b?.id, name: b?.name || '', data: Object.fromEntries(FIELDS.map(([f, , list]) =>
    [f, list ? (b?.data?.[f] || []).join('\n') : b?.data?.[f] || ''])), origin: b?.data?.origin })
  async function save() {
    const data: Any = { origin: sel.origin || 'пользователь' }
    FIELDS.forEach(([f, , list]) => { data[f] = list ? sel.data[f].split('\n').map((x: string) => x.trim()).filter(Boolean) : sel.data[f] })
    try { await api.send('POST', '/api/brands', { id: sel.id, name: sel.name, data }); setMsg('Бренд сохранён'); brands.reload() } catch (e: Any) { setErr(e.message) }
  }
  async function addPref() {
    try { await api.send('POST', '/api/preferences', { ...np, scope_id: np.scope === 'studio' ? null : np.scope_id }); setNp({ ...np, text: '' }); prefs.reload(); setMsg('Предпочтение сохранено') }
    catch (e: Any) { setErr(e.message) }
  }
  const toggle = async (p: Any) => { await api.send('POST', `/api/preferences/${p.id}/active`, { active: !p.active }); prefs.reload() }

  return (
    <div>
      <h1>Бренды</h1>
      <div className="sub">Независимые профили. Стиль одного бренда не переносится на другой без явного основания.</div>
      <Err e={err} />
      <div className="grid3">
        {(brands.data || []).map((b) => (
          <div key={b.id} className="card click" onClick={() => open(b)}>
            <b>{b.name}</b><div className="small muted">{b.data.kind} · v{b.version}</div>
            {(b.data.visual_rules || []).map((r: string, i: number) => <div key={i} className="small">• {r}</div>)}
            <div className="small muted" style={{ marginTop: 6 }}>{b.data.origin}</div>
          </div>))}
        <div className="card click" onClick={() => open(null)}><b>+ Новый бренд / клиент</b></div>
      </div>
      {sel && <div className="card" style={{ marginTop: 14 }}>
        <div className="kv">
          <div>Название</div><div><input style={{ width: '100%' }} value={sel.name} onChange={(e) => setSel({ ...sel, name: e.target.value })} /></div>
          {FIELDS.map(([f, l, list]) => <><div key={f}>{l}{list ? <div className="small">по строке</div> : null}</div><div>{list ?
            <textarea style={{ minHeight: 60 }} value={sel.data[f]} onChange={(e) => setSel({ ...sel, data: { ...sel.data, [f]: e.target.value } })} /> :
            <input style={{ width: '100%' }} value={sel.data[f]} onChange={(e) => setSel({ ...sel, data: { ...sel.data, [f]: e.target.value } })} />}</div></>)}
        </div>
        <div className="row" style={{ marginTop: 8 }}><button className="primary" onClick={save}>Сохранить</button><button onClick={() => setSel(null)}>Закрыть</button></div>
      </div>}

      <h2>Предпочтения и правки</h2>
      <div className="note">Правка действует только в выбранной области. Разовая правка ролика не становится правилом бренда или студии.</div>
      <div className="card">
        <div className="row">
          <select value={np.scope} onChange={(e) => setNp({ ...np, scope: e.target.value })}>
            {Object.entries(meta.data?.pref_scopes || {}).map(([k, v]: Any) => <option key={k} value={k}>{v}</option>)}</select>
          {np.scope === 'brand' && <select value={np.scope_id} onChange={(e) => setNp({ ...np, scope_id: e.target.value })}>
            <option value="">— бренд —</option>{(brands.data || []).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select>}
          {(np.scope === 'project' || np.scope === 'clip') && <input placeholder="ID проекта (prj_…)" value={np.scope_id} onChange={(e) => setNp({ ...np, scope_id: e.target.value })} />}
          <input style={{ flex: 1, minWidth: 260 }} placeholder="Например: «без музыкальной подложки под голос»" value={np.text} onChange={(e) => setNp({ ...np, text: e.target.value })} />
          <button className="primary" onClick={addPref}>Добавить</button>
        </div>
        <table style={{ marginTop: 10 }}><thead><tr><th>Область</th><th>Текст</th><th>Статус</th><th></th></tr></thead><tbody>
          {(prefs.data || []).map((p) => <tr key={p.id}><td className="small">{meta.data?.pref_scopes[p.scope]}{p.scope_id ? ` · ${p.scope_id}` : ''}</td>
            <td>{p.text}<div className="small muted">{p.id} · {p.origin}{p.replaced_by ? ` · заменено ${p.replaced_by}` : ''}</div></td>
            <td><Badge s={p.active ? 'completed' : 'cancelled'} title={p.active ? 'активно' : 'отключено'} /></td>
            <td><button onClick={() => toggle(p)}>{p.active ? 'Отключить' : 'Включить'}</button></td></tr>)}</tbody></table>
      </div>
      <Toast msg={msg} onDone={() => setMsg(null)} />
    </div>)
}
