import { useCallback, useRef, useState } from 'react'
import { api, Any, go } from '../api'
import { Badge, Err, Toast, useLoad, usePoll } from '../ui'

const STAGE: Record<string, string> = {
  probe: 'Технический проход', audio: 'Аудиодорожка', transcribe: 'Речь (ASR)', frames: 'Кадры', ocr: 'OCR',
  audio_features: 'Звук (сигнал)', context_pack: 'Пакет анализа', page_fetch: 'Страница по ссылке',
  image_probe: 'Изображение', document_text: 'Текст документа',
}

export default function Inbox() {
  const jobs = useLoad<Any[]>('/api/jobs')
  const blocked = useLoad<Any[]>('/api/sources?status=blocked')
  const accounts = useLoad<Any[]>('/api/accounts')
  const reloadAll = useCallback(() => { jobs.reload(); blocked.reload() }, [jobs.reload, blocked.reload]) // eslint-disable-line
  usePoll(reloadAll, 4000)
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [urls, setUrls] = useState('')
  const [note, setNote] = useState({ title: '', text: '' })
  const [meta, setMeta] = useState({ purpose: 'обучение', usage_status: 'неизвестно', collection: '' })
  const [filter, setFilter] = useState('active')
  const fileRef = useRef<HTMLInputElement>(null)

  const metaJson = () => ({ purpose: meta.purpose, usage_status: meta.usage_status,
    collections: meta.collection ? [meta.collection] : [] })

  async function upload(files: FileList | File[]) {
    if (!files.length) return
    setBusy(true); setErr(null)
    const fd = new FormData()
    Array.from(files).forEach((f) => fd.append('files', f))
    fd.append('meta', JSON.stringify(metaJson()))
    try {
      const r: Any[] = await api.upload('/api/import/files', fd)
      const ok = r.filter((x) => x.ok)
      const dup = ok.filter((x) => x.duplicate).length
      const bad = r.filter((x) => !x.ok)
      setMsg(`Импортировано: ${ok.length - dup}, дублей: ${dup}${bad.length ? `, ошибок: ${bad.length}` : ''}`)
      if (bad.length) setErr(bad.map((b) => `${b.name}: ${b.error}`).join('\n'))
      reloadAll()
    } catch (e: Any) { setErr(e.message) } finally { setBusy(false) }
  }

  async function addUrls() {
    const list = urls.split(/\s+/).filter(Boolean)
    if (!list.length) return
    setBusy(true); setErr(null)
    try {
      const r: Any[] = await api.send('POST', '/api/import/urls', { urls: list, meta: { ...metaJson(), via: 'ввод ссылок в интерфейсе' } })
      const bad = r.filter((x) => !x.ok)
      setMsg(`Ссылок: ${r.length - bad.length}, из них дублей: ${r.filter((x) => x.duplicate).length}`)
      if (bad.length) setErr(bad.map((b) => `${b.input}: ${b.error}`).join('\n'))
      setUrls(''); reloadAll(); accounts.reload()
    } catch (e: Any) { setErr(e.message) } finally { setBusy(false) }
  }

  async function addNote() {
    if (!note.title.trim()) return
    await api.send('POST', '/api/import/note', note)
    setNote({ title: '', text: '' }); setMsg('Заметка сохранена'); reloadAll()
  }

  async function act(id: string, a: 'retry' | 'cancel') {
    try { await api.send('POST', `/api/jobs/${id}/${a}`); reloadAll() } catch (e: Any) { setErr(e.message) }
  }

  const list = (jobs.data || []).filter((j) => filter === 'all' ? true :
    filter === 'active' ? ['queued', 'running', 'failed', 'blocked'].includes(j.state) : j.state === filter)
  const counts = (jobs.data || []).reduce((a: Record<string, number>, j) => { a[j.state] = (a[j.state] || 0) + 1; return a }, {})

  return (
    <div>
      <h1>Входящие</h1>
      <div className="sub">Добавляйте файлы, ссылки, профили и заметки. Оригинал сохраняется один раз (SHA-256), обработка идёт в фоне.</div>
      <Err e={err} />
      <div className="grid2">
        <div className="card">
          <h3>Файлы</h3>
          <div className={`drop ${over ? 'over' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files) }}
            onClick={() => fileRef.current?.click()} style={{ cursor: 'pointer' }}>
            {busy ? 'Загрузка…' : 'Перетащите видео, аудио, изображения, тексты или ZIP — или нажмите для выбора'}
            <div className="small" style={{ marginTop: 6 }}>Обработчики: видео/аудио (mp4, mov, webm, mp3, wav, m4a…), изображения, .txt/.md/.srt/.vtt/.json/.csv, ZIP. PDF/DOCX — сохраняются, текст не извлекается.</div>
          </div>
          <input ref={fileRef} type="file" multiple hidden onChange={(e) => e.target.files && upload(e.target.files)} />
          <div className="row" style={{ marginTop: 10 }}>
            <label className="small muted">Назначение</label>
            <select value={meta.purpose} onChange={(e) => setMeta({ ...meta, purpose: e.target.value })}>
              {['обучение', 'референс', 'ассет', 'мой материал', 'сочетание'].map((x) => <option key={x}>{x}</option>)}
            </select>
            <label className="small muted">Использование</label>
            <select value={meta.usage_status} onChange={(e) => setMeta({ ...meta, usage_status: e.target.value })}>
              {['неизвестно', 'мой материал', 'разрешено', 'только референс'].map((x) => <option key={x}>{x}</option>)}
            </select>
            <input placeholder="Коллекция (необязательно)" value={meta.collection} onChange={(e) => setMeta({ ...meta, collection: e.target.value })} />
          </div>
        </div>
        <div className="card">
          <h3>Ссылки и профили</h3>
          <textarea placeholder="По одной ссылке в строке: ролики, статьи, профили аккаунтов" value={urls} onChange={(e) => setUrls(e.target.value)} />
          <div className="row" style={{ marginTop: 8 }}>
            <button className="primary" disabled={busy} onClick={addUrls}>Добавить ссылки</button>
            <span className="small muted">Медиа с соцсетей не скачивается в обход входа. Закрытые ссылки остаются видимыми — их можно дополнить файлом.</span>
          </div>
          <h3>Заметка</h3>
          <input placeholder="Заголовок" style={{ width: '100%', marginBottom: 6 }} value={note.title} onChange={(e) => setNote({ ...note, title: e.target.value })} />
          <textarea placeholder="Текст, объяснение, инструкция…" value={note.text} onChange={(e) => setNote({ ...note, text: e.target.value })} />
          <button onClick={addNote} style={{ marginTop: 6 }}>Сохранить заметку</button>
        </div>
      </div>

      {(blocked.data || []).length > 0 && <>
        <h2>Требуют действия ({blocked.data!.length})</h2>
        <div className="card">
          <table><thead><tr><th>Источник</th><th>Статус</th><th>Причина</th><th>Что сделать</th></tr></thead>
            <tbody>{blocked.data!.map((s) => (
              <tr key={s.id}><td><a onClick={() => go(`/source/${s.id}`)}>{s.title}</a><div className="small muted">{s.id}</div></td>
                <td><Badge s={s.st_acquire} /></td><td className="small">{s.blocked_reason}</td><td className="small">{s.next_action}</td></tr>))}
            </tbody></table>
        </div></>}

      {(accounts.data || []).length > 0 && <>
        <h2>Аккаунты</h2>
        {accounts.data!.map((a) => (
          <div className="card" key={a.id} style={{ marginBottom: 8 }}>
            <b>@{a.handle}</b> <span className="muted small">{a.platform} · {a.url}</span> <Badge s={a.status === 'needs_post_list' ? 'blocked' : a.status} />
            <div className="small" style={{ marginTop: 4 }}>{a.sample_note}. {a.notes}</div>
            <div className="small muted">Снимок: {a.snapshot_at || 'не выполнен'} · период: {a.period} · публикаций в карточке: {a.posts.length}</div>
          </div>))}</>}

      <h2>Очередь обработки</h2>
      <div className="row" style={{ marginBottom: 8 }}>
        {['active', 'all', 'queued', 'running', 'completed', 'partial', 'blocked', 'failed', 'cancelled'].map((f) => (
          <button key={f} className={filter === f ? 'primary' : ''} onClick={() => setFilter(f)}>
            {f === 'active' ? 'Требуют внимания/идут' : f === 'all' ? 'Все' : f} {f !== 'active' && f !== 'all' && counts[f] ? `(${counts[f]})` : ''}
          </button>))}
      </div>
      <div className="card">
        {list.length === 0 ? <div className="muted">Нет задач в этом фильтре.</div> :
          <table><thead><tr><th>Источник</th><th>Этап</th><th>Состояние</th><th>Попытки</th><th>Результат / причина</th><th></th></tr></thead>
            <tbody>{list.map((j) => (
              <tr key={j.id}>
                <td><a onClick={() => go(`/source/${j.source_id}`)}>{j.title || j.source_id}</a></td>
                <td>{STAGE[j.stage] || j.stage}</td>
                <td><Badge s={j.state} /></td>
                <td className="small">{j.attempts}/{j.max_attempts}</td>
                <td className="small">{j.error || j.note || ''}</td>
                <td className="row">
                  {['failed', 'blocked', 'cancelled', 'partial'].includes(j.state) && <button onClick={() => act(j.id, 'retry')}>Повторить</button>}
                  {['queued', 'running'].includes(j.state) && <button onClick={() => act(j.id, 'cancel')}>Отменить</button>}
                </td>
              </tr>))}</tbody></table>}
        <div className="small muted" style={{ marginTop: 6 }}>Процент выполнения не показывается: этапы не сообщают вычислимый прогресс.</div>
      </div>
      <Toast msg={msg} onDone={() => setMsg(null)} />
    </div>
  )
}
