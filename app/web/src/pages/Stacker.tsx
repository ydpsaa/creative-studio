import { useEffect, useMemo, useState } from 'react'
import { Any, api, fileUrl, go } from '../api'
import { Err, Toast, useLoad, usePoll } from '../ui'

type Page = { rows?: string[]; punch?: string; sticker?: string; tear?: string }

const stickerUrl = (f: string) => `/api/stacker/sticker/${f}`

function StickerPicker({ onPick, onClose }: { onPick: (f: string) => void; onClose: () => void }) {
  const { data } = useLoad<Any[]>('/api/stacker/stickers')
  const [q, setQ] = useState('')
  const groups = useMemo(() => {
    const m = new Map<string, Any[]>()
    for (const s of data || []) {
      if (q && !s.name.includes(q.toLowerCase()) && !s.group.toLowerCase().includes(q.toLowerCase())) continue
      if (!m.has(s.group)) m.set(s.group, [])
      m.get(s.group)!.push(s)
    }
    return [...m.entries()]
  }, [data, q])
  return (
    <div className="stkmodal" onClick={onClose}>
      <div className="card stkpicker" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ marginBottom: 10 }}>
          <b>Стикер — буквальная картинка ключевого слова</b>
          <input autoFocus placeholder="поиск: bulb, money, hand…" value={q} onChange={(e) => setQ(e.target.value)} style={{ marginLeft: 'auto', width: 240 }} />
          <button onClick={() => { onPick(''); onClose() }}>Без стикера</button>
          <button onClick={onClose}>Закрыть</button>
        </div>
        <div className="scroll" style={{ maxHeight: '70vh' }}>
          {groups.map(([g, items]) => (
            <div key={g}>
              <div className="small muted" style={{ margin: '8px 0 4px' }}>{g}</div>
              <div className="stkgrid">
                {items.map((s) => (
                  <div key={s.file} className="stkcell" title={s.name} onClick={() => { onPick(s.file); onClose() }}>
                    <img loading="lazy" src={stickerUrl(s.file)} /><div className="small">{s.name}</div>
                  </div>))}
              </div>
            </div>))}
        </div>
      </div>
    </div>)
}

function PageEditor({ p, i, n, set, move, del, pick }: {
  p: Page; i: number; n: number; set: (p: Page) => void; move: (d: number) => void; del: () => void; pick: () => void
}) {
  const kind = p.punch !== undefined ? 'punch' : 'rows'
  return (
    <div className="card stkpage">
      <div className="row">
        <b>Экран {i + 1}</b>
        <select value={kind} onChange={(e) => set(e.target.value === 'punch'
          ? { punch: (p.rows || []).join(' ') || 'but.', sticker: p.sticker }
          : { rows: p.punch ? [p.punch] : ['новая строка'], sticker: p.sticker })}>
          <option value="rows">Стопка строк</option><option value="punch">Слово-удар</option>
        </select>
        <span style={{ flex: 1 }} />
        <button disabled={i === 0} onClick={() => move(-1)} title="Выше">↑</button>
        <button disabled={i === n - 1} onClick={() => move(1)} title="Ниже">↓</button>
        <button onClick={del} title="Удалить экран">✕</button>
      </div>
      <div className="stkpagebody">
        <div style={{ flex: 1 }}>
          {kind === 'rows'
            ? <><div className="small muted">Строки (2–3 по 1–3 слова), каждая с новой строки — в порядке, как вы их говорите</div>
              <textarea value={(p.rows || []).join('\n')} onChange={(e) => set({ ...p, rows: e.target.value.split('\n') })} style={{ minHeight: 80 }} /></>
            : <><div className="small muted">Слово-удар (BUT, DONE, HOW) — заглавными на всю ширину</div>
              <input style={{ width: '100%' }} value={p.punch || ''} onChange={(e) => set({ ...p, punch: e.target.value })} />
              <div className="small muted" style={{ marginTop: 8 }}>Разорвать слово вокруг стикера (последний экран), например «done.»</div>
              <input style={{ width: '100%' }} value={p.tear || ''} placeholder="не рвать" onChange={(e) => set({ ...p, tear: e.target.value || undefined })} /></>}
        </div>
        <div className="stkthumb" onClick={pick} title="Выбрать стикер">
          {p.sticker ? <img src={stickerUrl(p.sticker)} /> : <span className="small muted">+ стикер</span>}
        </div>
      </div>
    </div>)
}

function Editor({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { data, error, reload, setData } = useLoad<Any>(`/api/stacker/projects/${id}`, [id])
  const [sc, setSc] = useState<Any>(null)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState('')
  const [picker, setPicker] = useState<number | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [info, setInfo] = useState<Any>(null)
  useEffect(() => { if (data && !dirty) setSc(data.scenario) }, [data]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setDirty(false); setInfo(null) }, [id])
  const rendering = data?.status?.state === 'rendering'
  usePoll(() => { if (rendering) reload() }, 2000)
  if (!sc) return <Err e={error} />
  const upd = (patch: Any) => { setSc({ ...sc, ...patch }); setDirty(true) }
  const setPage = (i: number, p: Page) => { const pages = [...sc.pages]; pages[i] = p; upd({ pages }) }
  const clean = () => ({ ...sc, pages: sc.pages.map((p: Page) => p.rows ? { ...p, rows: p.rows.map((r) => r.trim()).filter(Boolean) } : p) })
  const run = async (label: string, f: () => Promise<Any>) => {
    setBusy(label); setErr(null)
    try { const r = await f(); if (r) { setData(r); setSc(r.scenario); if (r.preview_info) setInfo(r.preview_info) } setDirty(false); onChanged() }
    catch (e: Any) { setErr(String(e.message || e)) } finally { setBusy('') }
  }
  const save = () => api.send('PUT', `/api/stacker/projects/${id}`, clean())
  const upload = (kind: string, f: File | undefined) => {
    if (!f) return
    const form = new FormData(); form.append('file', f)
    run(kind === 'voice' ? 'Загружаю голос…' : 'Загружаю видео…', () => api.upload(`/api/stacker/projects/${id}/${kind}`, form))
  }
  const st = data.status || {}
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <input style={{ fontSize: 18, fontWeight: 600, width: 360 }} value={sc.title || ''} onChange={(e) => upd({ title: e.target.value })} />
        <span style={{ flex: 1 }} />
        <button className={dirty ? 'primary' : ''} disabled={!!busy} onClick={() => run('Сохраняю…', save)}>Сохранить</button>
        <button disabled={!!busy} onClick={() => run('Собираю кадры… (первый раз с голосом — распознавание речи, до минуты)',
          async () => { await save(); return api.send('POST', `/api/stacker/projects/${id}/preview`, {}) })}>Кадры</button>
        <button className="primary" disabled={!!busy || rendering} onClick={() => run('Запускаю сборку…',
          async () => { await save(); return api.send('POST', `/api/stacker/projects/${id}/render`) })}>{rendering ? 'Собираю видео…' : 'Собрать видео'}</button>
      </div>
      {busy && <div className="note">{busy}</div>}
      <Err e={err || error} />

      <div className="grid2">
        <div>
          <div className="card">
            <h3 style={{ marginTop: 0 }}>Голос и кадр</h3>
            <div className="kv">
              <div>Голос</div>
              <div>{data.voice ? <><audio controls src={fileUrl(data.voice)} style={{ width: 260 }} /> <button onClick={() => run('…', () => api.send('DELETE', `/api/stacker/projects/${id}/voice`))}>убрать</button></>
                : <><input type="file" accept="audio/*,video/*" onChange={(e) => upload('voice', e.target.files?.[0])} />
                  <div className="small muted">Без голоса время считается по тексту ниже (черновик)</div></>}</div>
              <div>Ваш кадр</div>
              <div>{data.talk ? <><span className="small">{data.talk.split('/').pop()}</span> <button onClick={() => run('…', () => api.send('DELETE', `/api/stacker/projects/${id}/talk`))}>убрать</button></>
                : <><input type="file" accept="video/*" onChange={(e) => upload('talk', e.target.files?.[0])} />
                  <div className="small muted">Говорящая часть после склейки, обрежется в полосу 4:3</div></>}</div>
              {data.talk && <><div>Начало кадра</div><div>
                <select value={'talk_start' in sc ? 'start' : 'sync'} onChange={(e) => {
                  const n = { ...sc }; if (e.target.value === 'start') { delete n.talk_offset; n.talk_start = 0 } else { delete n.talk_start; n.talk_offset = 0 }
                  setSc(n); setDirty(true) }}>
                  <option value="sync">это моя запись — синхронно с голосом</option><option value="start">отдельное видео — с начала</option>
                </select></div></>}
            </div>
          </div>
          <div className="card" style={{ marginTop: 14 }}>
            <h3 style={{ marginTop: 0 }}>Оформление</h3>
            <div className="kv">
              <div>Формат</div>
              <div><select value={sc.format || '9:16'} onChange={(e) => upd({ format: e.target.value })}>
                <option value="9:16">9:16 — Reels / Shorts / TikTok</option><option value="4:5">4:5 — лента и карусель Instagram</option></select></div>
              <div>Цвет полосы</div>
              <div className="row"><input type="color" value={sc.band_color || '#0E1E5C'} onChange={(e) => upd({ band_color: e.target.value })} />
                {['#0E1E5C', '#870000', '#020002'].map((c) => <span key={c} className="stkswatch" style={{ background: c }} onClick={() => upd({ band_color: c })} />)}</div>
              <div>Цвет субтитров</div>
              <div><input type="color" value={sc.caption_color || '#FFB547'} onChange={(e) => upd({ caption_color: e.target.value })} /></div>
              <div>Счётчик серии</div>
              <div><input value={sc.counter || ''} placeholder="01/30" onChange={(e) => upd({ counter: e.target.value || undefined })} /></div>
              <div>Звуки</div>
              <div><select value={sc.sfx || ''} onChange={(e) => upd({ sfx: e.target.value || undefined })}>
                {(data.sfx_libraries || []).map((l: Any) => <option key={l.id} value={l.id}>{l.label}</option>)}</select>
                <div className="small muted">Текст = клавиатура; стикер = бумага; разрыв = рвущаяся бумага</div></div>
            </div>
          </div>
          {!data.voice && <div className="card" style={{ marginTop: 14 }}>
            <h3 style={{ marginTop: 0 }}>Текст (пока нет голоса)</h3>
            <textarea value={sc.script || ''} onChange={(e) => upd({ script: e.target.value })} style={{ minHeight: 110 }}
              placeholder="Весь текст ролика: сначала слова заставки, потом говорящая часть" />
          </div>}
          {data.words && <div className="card" style={{ marginTop: 14 }}>
            <h3 style={{ marginTop: 0 }}>Распознано в голосе</h3>
            <div className="small">{data.words.slice(0, 80).map((w: Any) => w.w).join(' ')}{data.words.length > 80 ? ' …' : ''}</div>
            <div className="small muted">Слова экранов должны идти в этом порядке. Если слово распознано иначе, на экране будет ваш текст.</div>
          </div>}
        </div>

        <div>
          <h3 style={{ marginTop: 0 }}>Заставка — экраны по порядку</h3>
          {sc.pages.map((p: Page, i: number) => (
            <PageEditor key={i} p={p} i={i} n={sc.pages.length} set={(np) => setPage(i, np)} pick={() => setPicker(i)}
              move={(d) => { const pages = [...sc.pages]; [pages[i], pages[i + d]] = [pages[i + d], pages[i]]; upd({ pages }) }}
              del={() => upd({ pages: sc.pages.filter((_: Page, k: number) => k !== i) })} />))}
          <div className="row">
            <button onClick={() => upd({ pages: [...sc.pages, { rows: ['новая строка'] }] })}>+ стопка строк</button>
            <button onClick={() => upd({ pages: [...sc.pages, { punch: 'but.' }] })}>+ слово-удар</button>
          </div>
        </div>
      </div>

      {(info || (data.previews || []).length > 0) && <div className="card" style={{ marginTop: 18 }}>
        <h3 style={{ marginTop: 0 }}>Кадры</h3>
        {info && <div className="small" style={{ marginBottom: 8 }}>
          {info.timeline.map((t: Any) => `экран ${t.page}: ${t.from}–${t.to} с`).join(' · ')} · заставка до {info.intro_end} с · всего {info.total} с
          {info.simulated && <span className="muted"> · время расчётное (нет голоса)</span>}
          {info.warnings.map((w: string, k: number) => <div key={k} style={{ color: 'var(--warn)' }}>⚠ {w}</div>)}
        </div>}
        <div className="stkframes">
          {(data.previews || []).map((p: Any) => <div key={p.path}><img src={fileUrl(p.path) + `&v=${data.status?.at || ''}`} /><div className="small muted">{p.t.toFixed(2)} с</div></div>)}
        </div>
      </div>}

      {(data.video || rendering || st.state === 'failed') && <div className="card" style={{ marginTop: 18 }}>
        <h3 style={{ marginTop: 0 }}>Видео</h3>
        {rendering && <div className="note">Собираю… страница обновится сама.</div>}
        {st.state === 'failed' && <div className="error">Не собралось: {st.error}</div>}
        {data.video && !rendering && <div className="row" style={{ alignItems: 'flex-start' }}>
          <video className="player" style={{ maxWidth: 360 }} controls src={fileUrl(data.video) + `&v=${st.at || ''}`} />
          <div className="small">
            {st.duration && <div>Длительность: {st.duration} с · сборка {st.seconds} с</div>}
            <div style={{ marginTop: 8 }}><a href={fileUrl(data.video, true)}>Скачать MP4</a></div>
          </div>
        </div>}
      </div>}

      {picker !== null && <StickerPicker onClose={() => setPicker(null)} onPick={(f) => setPage(picker, { ...sc.pages[picker], sticker: f || undefined })} />}
      <Toast msg={toast} onDone={() => setToast(null)} />
    </div>)
}

export default function Stacker({ id }: { id?: string }) {
  const { data: list, reload } = useLoad<Any[]>('/api/stacker/projects')
  const [title, setTitle] = useState('')
  const create = () => api.send('POST', '/api/stacker/projects', { title: title || 'Новый стакер' }).then((p) => { setTitle(''); reload(); go(`/stacker/${p.id}`) })
  return (
    <div>
      <h1>Стакер</h1>
      <div className="sub">Слова в такт голосу в полосе 4:3, стикеры на ключевых словах, звуки заставки — и видео одной кнопкой.
        Описание стиля — <code>stacker-kit/README.ru.md</code>.</div>
      <div className="split">
        <div>
          <div className="card">
            <div className="row"><input placeholder="Название ролика" value={title} onChange={(e) => setTitle(e.target.value)} style={{ flex: 1 }} />
              <button className="primary" onClick={create}>Новый</button></div>
            <div style={{ marginTop: 10 }}>
              {(list || []).map((p) => (
                <div key={p.id} className={`seg ${p.id === id ? 'on' : ''}`} style={{ gridTemplateColumns: '1fr auto' }} onClick={() => go(`/stacker/${p.id}`)}>
                  <span>{p.title}</span>
                  <span className="small muted">{p.status?.state === 'rendering' ? 'собирается' : p.has_video ? 'видео ✓' : 'черновик'}</span>
                </div>))}
              {list && list.length === 0 && <div className="small muted">Пока пусто. Новый проект начнётся с примера «I'M DONE».</div>}
            </div>
          </div>
        </div>
        <div>{id ? <Editor id={id} onChanged={reload} /> : <div className="note">Выберите ролик слева или создайте новый.</div>}</div>
      </div>
    </div>)
}
