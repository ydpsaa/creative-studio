import { useCallback, useEffect, useMemo, useState } from 'react'
import { Any, api } from '../api'
import { Err, Toast, useLoad } from '../ui'

const ST: Record<string, [string, string]> = {
  new: ['новое', 'muted'], selected: ['выбрано', 'ok'], rejected: ['отклонено', 'bad'], needs_changes: ['нужны правки', 'warn'],
}
const FILTERS: [string, string][] = [
  ['', 'Все'], ['new', 'Новые'], ['selected', 'Выбрано'], ['needs_changes', 'Нужны правки'], ['rejected', 'Отклонено'],
  ['forward', 'Отправлено Claude'], ['commented', 'С комментарием'],
]
const thumb = (id: string) => `/api/review/thumb/${id}`
const file = (id: string) => `/api/review/file/${id}`
const isImg = (it: Any) => ['.png', '.jpg', '.jpeg', '.webp', '.gif'].includes(it.ext)

function StatusBadge({ s }: { s: string }) {
  const [l, t] = ST[s] || [s, 'muted']
  return <span className={`badge ${t}`}>{l}</span>
}

function Doc({ id }: { id: string }) {
  const [text, setText] = useState<string | null>(null)
  useEffect(() => { fetch(file(id)).then((r) => r.text()).then(setText).catch(() => setText('не удалось открыть')) }, [id])
  return <pre className="md rvdoc">{text ?? '…'}</pre>
}

function Item({ it, sel, toggle, save, trash, zoom }: {
  it: Any; sel: boolean; toggle: (id: string, shift: boolean) => void
  save: (ids: string[], patch: Any) => Promise<void>; trash: (it: Any) => void; zoom: (it: Any) => void
}) {
  const [comment, setComment] = useState(it.comment || '')
  const [tags, setTags] = useState((it.tags || []).join(', '))
  const [open, setOpen] = useState(false)
  useEffect(() => { setComment(it.comment || ''); setTags((it.tags || []).join(', ')) }, [it.comment, it.tags])
  const set = (status: string) => save([it.id], { status: it.status === status ? 'new' : status })
  const media = it.kind === 'video'
    ? <video className="rvmedia" controls preload="none" poster={thumb(it.id)} src={file(it.id)} />
    : it.kind === 'sound'
      ? <audio controls preload="none" src={file(it.id)} style={{ width: '100%' }} />
      : isImg(it)
        ? <img className="rvimg" loading="lazy" src={thumb(it.id)} onClick={() => zoom(it)} title="Открыть крупно" />
        : <div>
          <button onClick={() => setOpen(!open)}>{open ? 'Свернуть' : 'Открыть документ'}</button>
          {open && <Doc id={it.id} />}
        </div>
  return (
    <div className={`rvcard st-${it.status}${sel ? ' sel' : ''}${it.kind === 'doc' ? ' wide' : ''}`}>
      <div className="rvtop">
        <label className="small" onClick={(e) => { e.preventDefault(); toggle(it.id, e.shiftKey) }}>
          <input type="checkbox" checked={sel} readOnly /> <span className="rvtitle" title={it.rel_path}>{it.title}</span>
        </label>
        <StatusBadge s={it.status} />
      </div>
      {media}
      <div className="row rvbtns">
        <button className={it.status === 'selected' ? 'on-ok' : ''} title="Выбрать" onClick={() => set('selected')}>✓</button>
        <button className={it.status === 'needs_changes' ? 'on-warn' : ''} title="Нужны правки" onClick={() => set('needs_changes')}>✎</button>
        <button className={it.status === 'rejected' ? 'on-bad' : ''} title="Отклонить (файл остаётся на месте)" onClick={() => set('rejected')}>✕</button>
        <button className={it.forward ? 'on-run' : ''} title={it.forward ? 'Отправлено Claude — нажмите, чтобы отозвать' : 'Отправить Claude с комментарием'}
          onClick={() => save([it.id], { forward: !it.forward })}>{it.forward ? '→ Claude ✓' : '→ Claude'}</button>
        <span style={{ flex: 1 }} />
        <button className="ghost" title="Переместить файл в Корзину macOS" onClick={() => trash(it)}>🗑</button>
      </div>
      <textarea className="rvcomment" placeholder="Что не так / что сделать…" value={comment}
        onChange={(e) => setComment(e.target.value)} onBlur={() => comment !== (it.comment || '') && save([it.id], { comment })} />
      <input className="rvtags" placeholder="теги через запятую" value={tags} onChange={(e) => setTags(e.target.value)}
        onBlur={() => tags !== (it.tags || []).join(', ') && save([it.id], { tags: tags.split(',').map((x: string) => x.trim()).filter(Boolean) })} />
      {(it.handled_note || it.file_changed_at) && <div className="small muted" style={{ marginTop: 4 }}>
        {it.handled_note && <>Claude: {it.handled_note}<br /></>}
        {it.file_changed_at && <>файл обновлён {it.file_changed_at.slice(0, 16).replace('T', ' ')}</>}
      </div>}
    </div>
  )
}

function GroupHead({ g, kind, items, selected, setSel, onSaved }: {
  g: Any; kind: string; items: Any[]; selected: Set<string>; setSel: (s: Set<string>) => void; onSaved: (m: string) => void
}) {
  const [comment, setComment] = useState(g?.comment || '')
  const [open, setOpen] = useState(!!g?.comment)
  useEffect(() => { setComment(g?.comment || '') }, [g?.comment])
  const ids = items.map((i) => i.id)
  const allSel = ids.length > 0 && ids.every((i) => selected.has(i))
  const saveGroup = (patch: Any) => api.send('POST', '/api/review/group', { kind, group: g.group, ...patch })
    .then(() => onSaved(patch.forward ? 'Группа отправлена Claude' : 'Комментарий группы сохранён')).catch((e) => onSaved(String(e.message || e)))
  return (
    <div className="rvgroup">
      <div className="row">
        <h2 style={{ margin: 0 }}>{g.group}</h2>
        <span className="small muted">{g.total} · выбрано {g.by_status.selected} · правки {g.by_status.needs_changes} · отклонено {g.by_status.rejected}</span>
        <button className="ghost small" onClick={() => {
          const n = new Set(selected); ids.forEach((i) => allSel ? n.delete(i) : n.add(i)); setSel(n)
        }}>{allSel ? 'снять выделение группы' : 'выделить группу'}</button>
        <button className="ghost small" onClick={() => setOpen(!open)}>{g.comment ? 'комментарий группы ✓' : 'комментарий группы'}</button>
        {g.group_forward && <span className="badge run">группа отправлена Claude</span>}
        {g.handled_note && <span className="small muted">Claude: {g.handled_note}</span>}
      </div>
      {open && <div className="row" style={{ marginTop: 6, alignItems: 'flex-start' }}>
        <textarea style={{ flex: 1, minHeight: 52 }} placeholder="Общее замечание ко всей группе…" value={comment} onChange={(e) => setComment(e.target.value)} />
        <div style={{ display: 'grid', gap: 6 }}>
          <button onClick={() => saveGroup({ comment })}>Сохранить</button>
          <button className="primary" onClick={() => saveGroup({ comment, forward: !g.group_forward })}>{g.group_forward ? 'Отозвать у Claude' : 'Отправить Claude'}</button>
        </div>
      </div>}
    </div>
  )
}

export default function Review() {
  const sum = useLoad<Any>('/api/review/summary')
  const [kind, setKind] = useState('sticker_xerox')
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<Set<string>>(new Set())
  const [last, setLast] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [zoomIt, setZoom] = useState<Any>(null)
  const [bulkComment, setBulkComment] = useState('')
  const params = new URLSearchParams({ kind, status, q })
  const { data: items, error, reload, setData } = useLoad<Any[]>(`/api/review/items?${params}`, [])
  const reloadSum = useCallback(() => api.get('/api/review/summary?scan=0').then(sum.setData).catch(() => {}), [sum.setData])

  useEffect(() => { setSel(new Set()); setLast(null) }, [kind])
  const kinds: Any[] = sum.data?.kinds || []
  const kindInfo = kinds.find((k) => k.kind === kind)
  const groups = useMemo(() => {
    const m = new Map<string, Any[]>()
    for (const it of items || []) { if (!m.has(it.group)) m.set(it.group, []); m.get(it.group)!.push(it) }
    return [...m.entries()]
  }, [items])
  const flat = (items || []).map((i) => i.id)

  const save = async (ids: string[], patch: Any) => {
    try {
      const r = await api.send('POST', '/api/review/decide', { ids, ...patch })
      setData((items || []).map((it) => ids.includes(it.id) ? {
        ...it, ...('status' in patch ? { status: patch.status } : {}), ...('comment' in patch ? { comment: patch.comment } : {}),
        ...('tags' in patch ? { tags: patch.tags } : {}), ...('forward' in patch ? { forward: patch.forward } : {}),
      } : it))
      reloadSum()
      if (ids.length > 1 || 'forward' in patch) setToast(`Сохранено: ${r.changed}${patch.forward ? ' — отправлено Claude' : ''}`)
    } catch (e: Any) { setToast(String(e.message || e)) }
  }
  const toggle = (id: string, shift: boolean) => {
    const n = new Set(sel)
    if (shift && last) {
      const a = flat.indexOf(last), b = flat.indexOf(id)
      flat.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((x) => n.add(x))
    } else if (n.has(id)) n.delete(id); else n.add(id)
    setSel(n); setLast(id)
  }
  const trash = async (it: Any) => {
    if (!window.confirm(`Переместить файл в Корзину macOS?\n\n${it.rel_path}\n\nФайл можно будет вернуть из Корзины. Если просто не нравится — лучше «Отклонить» (✕): файл останется на месте.`)) return
    try {
      await api.send('POST', `/api/review/items/${it.id}/trash`, { confirm: true })
      setToast('Файл в Корзине'); reload(); reloadSum()
    } catch (e: Any) { setToast(String(e.message || e)) }
  }
  const bulk = (patch: Any) => { if (sel.size) save([...sel], patch) }

  return (
    <div>
      <h1>Ревью</h1>
      <div className="sub">Всё, что AI сгенерировал для ваших стилей (папки — config/review_sets.json). Выбирайте, отклоняйте, пишите, что не так, и отправляйте Claude —
        он увидит решения через MCP (<code>get_review_feedback</code>). Файлы не копируются и не удаляются: «Отклонить» только помечает.</div>
      <Err e={error || sum.error} />
      <div className="row" style={{ marginBottom: 12 }}>
        {kinds.map((k) => <button key={k.kind} className={kind === k.kind ? 'primary' : ''} onClick={() => setKind(k.kind)}>
          {k.label} <span className="small">({k.total})</span>{k.forward ? <span className="small"> · →{k.forward}</span> : null}</button>)}
        <span style={{ flex: 1 }} />
        {sum.data && <span className="small muted">Ждут Claude: {sum.data.pending_for_claude}</span>}
        <button onClick={() => api.send('POST', '/api/review/scan').then((r) => { setToast(`Найдено ${r.total}, новых ${r.new}, изменено ${r.changed}, пропало ${r.missing_now}`); reload(); reloadSum() })}>Пересканировать</button>
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {FILTERS.map(([v, l]) => <option key={v} value={v}>{l}{v && kindInfo && kindInfo.by_status[v] !== undefined ? ` (${kindInfo.by_status[v]})` : ''}</option>)}
        </select>
        <input style={{ width: 260 }} placeholder="Поиск по названию, комментарию, тегу…" value={q} onChange={(e) => setQ(e.target.value)} />
        {kindInfo && <span className="small muted">новых {kindInfo.by_status.new} · выбрано {kindInfo.by_status.selected} · правки {kindInfo.by_status.needs_changes} · отклонено {kindInfo.by_status.rejected}</span>}
      </div>

      <div className="rvbulk card">
        <span className="small"><b>{sel.size}</b> выделено</span>
        <button onClick={() => setSel(new Set(flat))}>Выделить все видимые</button>
        <button disabled={!sel.size} onClick={() => setSel(new Set())}>Снять</button>
        <span className="sep" />
        <button disabled={!sel.size} onClick={() => bulk({ status: 'selected' })}>✓ Выбрать</button>
        <button disabled={!sel.size} onClick={() => bulk({ status: 'needs_changes' })}>✎ Нужны правки</button>
        <button disabled={!sel.size} onClick={() => bulk({ status: 'rejected' })}>✕ Отклонить</button>
        <button disabled={!sel.size} onClick={() => bulk({ status: 'new' })}>Вернуть в новые</button>
        <span className="sep" />
        <input style={{ width: 240 }} placeholder="Комментарий к выделенным…" value={bulkComment} onChange={(e) => setBulkComment(e.target.value)} />
        <button disabled={!sel.size || !bulkComment.trim()} onClick={() => { bulk({ comment: bulkComment }); setBulkComment('') }}>Записать</button>
        <button className="primary" disabled={!sel.size} onClick={() => bulk({ forward: true, ...(bulkComment.trim() ? { comment: bulkComment } : {}) })}>→ Отправить Claude</button>
      </div>

      {groups.map(([g, its]) => {
        const gi = kindInfo?.groups.find((x: Any) => x.group === g) || { group: g, total: its.length, by_status: { selected: 0, needs_changes: 0, rejected: 0 } }
        return (
          <div key={g}>
            <GroupHead g={gi} kind={kind} items={its} selected={sel} setSel={setSel} onSaved={(m) => { setToast(m); reloadSum() }} />
            <div className={`rvgrid k-${kind}`}>
              {its.map((it) => <Item key={it.id} it={it} sel={sel.has(it.id)} toggle={toggle} save={save} trash={trash} zoom={setZoom} />)}
            </div>
          </div>)
      })}
      {items && items.length === 0 && <div className="muted">Ничего не найдено.</div>}

      {zoomIt && <div className="rvzoom" onClick={() => setZoom(null)}>
        <img src={file(zoomIt.id)} />
        <div className="small">{zoomIt.rel_path} · клик — закрыть</div>
      </div>}
      <Toast msg={toast} onDone={() => setToast(null)} />
    </div>
  )
}
