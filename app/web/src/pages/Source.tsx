import { useEffect, useRef, useState } from 'react'
import { api, Any, fileUrl, fmtT, go } from '../api'
import { Badge, Err, Evidence, Toast, useLoad, usePoll } from '../ui'

const TABS = ['Транскрипт', 'Кадры', 'Текст на экране', 'Звук', 'Разбор', 'Знания', 'Покрытие', 'Карточка', 'Задачи']

export default function Source({ id, t }: { id: string; t: number }) {
  const { data, error, reload } = useLoad<Any>(`/api/sources/${id}`, [id])
  usePoll(reload, 6000)
  const [tab, setTab] = useState('Транскрипт')
  const [cur, setCur] = useState(t || 0)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const media = useRef<HTMLVideoElement & HTMLAudioElement>(null)

  useEffect(() => { if (t && media.current) { media.current.currentTime = t } }, [t, data?.source?.rel_path])
  if (error) return <Err e={error} />
  if (!data) return <div className="muted">Загрузка…</div>
  const s = data.source
  const seek = (x: number) => { setCur(x); if (media.current) { media.current.currentTime = x; media.current.play().catch(() => {}) } }

  async function attach(f: File) {
    const fd = new FormData(); fd.append('file', f)
    try { const r = await api.upload(`/api/sources/${id}/attach`, fd); setMsg(r.duplicate ? 'Файл уже был в медиатеке — карточки связаны' : 'Файл добавлен, обработка запущена'); reload() }
    catch (e: Any) { setErr(e.message) }
  }

  return (
    <div>
      <div className="row"><a onClick={() => go('/library')}>← Библиотека</a></div>
      <h1>{s.title}</h1>
      <div className="sub">{s.id} · {s.kind} · {s.platform} · импорт {s.imported_at?.slice(0, 10)} {s.is_test ? '· ТЕСТОВЫЙ МАТЕРИАЛ' : ''}</div>
      <Err e={err} />
      {s.blocked_reason && <div className="warnbox"><b>Недоступно:</b> {s.blocked_reason}<br /><b>Что сделать:</b> {s.next_action}
        {!s.rel_path && <div style={{ marginTop: 8 }}><label className="row"><span>Дополнить файлом:</span>
          <input type="file" onChange={(e) => e.target.files?.[0] && attach(e.target.files[0])} /></label></div>}</div>}
      <div className="split">
        <div>
          {s.rel_path && s.kind === 'video' && <video ref={media} className="player" src={fileUrl(s.rel_path)} controls
            onTimeUpdate={(e) => setCur((e.target as HTMLVideoElement).currentTime)} />}
          {s.rel_path && s.kind === 'audio' && <audio ref={media} src={fileUrl(s.rel_path)} controls style={{ width: '100%' }}
            onTimeUpdate={(e) => setCur((e.target as HTMLAudioElement).currentTime)} />}
          {s.rel_path && s.kind === 'image' && <img className="player" src={fileUrl(s.rel_path)} />}
          {!s.rel_path && <div className="thumb empty" style={{ aspectRatio: '9/10' }}>Файла нет. {s.url && <a href={s.url} target="_blank" rel="noreferrer">Открыть ссылку ↗</a>}</div>}
          <div className="card" style={{ marginTop: 12 }}>
            <div className="statusgrid">
              <span className="muted">Получение</span><Badge s={s.st_acquire} />
              <span className="muted">Речь</span><Badge s={s.st_speech} />
              <span className="muted">Изображение</span><Badge s={s.st_visual} />
              <span className="muted">Звук</span><Badge s={s.st_audio} />
              <span className="muted">Разбор</span><Badge s={s.st_analysis} />
              <span className="muted">Знания</span><Badge s={s.st_knowledge} />
            </div>
            <PackBox d={data} sid={id} onDone={(m) => { setMsg(m); reload() }} />
          </div>
        </div>
        <div>
          <div className="tabs">{TABS.map((x) => <button key={x} className={tab === x ? 'on' : ''} onClick={() => setTab(x)}>{x}</button>)}</div>
          {tab === 'Транскрипт' && <Transcript tr={data.transcript} cur={cur} seek={seek} />}
          {tab === 'Кадры' && <><Frames fr={data.frames} seek={seek} cur={cur} /><Dense d={data} sid={id} seek={seek} onDone={() => reload()} /></>}
          {tab === 'Текст на экране' && <Ocr ocr={data.ocr} seek={seek} />}
          {tab === 'Звук' && <><SoundMap sm={data.sound_map} spec={data.spectrogram} seek={seek} /><Sound af={data.audio_features} seek={seek} /></>}
          {tab === 'Разбор' && <Analysis an={data.analysis} sid={id} seek={seek} onDone={(m) => { setMsg(m); reload() }} />}
          {tab === 'Знания' && <Knowledge kn={data.knowledge} seek={seek} />}
          {tab === 'Покрытие' && <pre className="card md small">{JSON.stringify(data.coverage, null, 2)}</pre>}
          {tab === 'Карточка' && <Meta d={data} onSaved={() => { setMsg('Сохранено'); reload() }} />}
          {tab === 'Задачи' && <Jobs jobs={data.jobs} reload={reload} />}
        </div>
      </div>
      <Toast msg={msg} onDone={() => setMsg(null)} />
    </div>
  )
}

function PackBox({ d, sid, onDone }: { d: Any; sid: string; onDone: (m: string) => void }) {
  const pj = d.pack_job
  const prepare = async () => { await api.send('POST', `/api/sources/${sid}/pack`); onDone('Пакет готовится в очереди') }
  if (!pj) return null
  return (
    <div style={{ marginTop: 10 }}>
      <h3>Разбор с Claude (ручной этап)</h3>
      <ol className="small" style={{ paddingLeft: 18, margin: '4px 0' }}>
        <li><button onClick={prepare} disabled={['queued', 'running'].includes(pj.state)}>Подготовить пакет для Claude</button>
          {' '}<Badge s={pj.state} /> {d.pack && pj.state === 'completed' && <a href={fileUrl(d.pack, true)}>скачать ZIP</a>}</li>
        <li>Передать пакет Claude: «разбери по навыку creative-video-analysis» (или вложить ZIP в чат).</li>
        <li>Вкладка «Разбор» → «Импортировать разбор» (файл JSON или вставка).</li>
      </ol>
    </div>)
}

function SoundMap({ sm, spec, seek }: { sm: Any; spec: string | null; seek: (t: number) => void }) {
  if (!sm) return <div className="note">Звуковой карты нет (этап sound_events не выполнен).</div>
  return (
    <div style={{ marginBottom: 16 }}>
      <div className="warnbox small">{sm.warning}</div>
      <div className="small muted">{sm.method} · темп (оценка): {sm.tempo_estimate_bpm ?? '—'} BPM</div>
      {spec && <img src={fileUrl(spec)} style={{ width: '100%', borderRadius: 8, margin: '8px 0' }} />}
      <table className="sbtable"><thead><tr><th>Интервал</th><th>Слои</th><th>Метки AudioSet</th><th>Характер</th></tr></thead>
        <tbody>{sm.segments.map((g: Any, i: number) => <tr key={i}>
          <td><a onClick={() => seek(g.t_start)}>{fmtT(g.t_start)}–{fmtT(g.t_end)}</a></td><td>{g.layers.join(', ')}</td>
          <td className="small">{g.top_labels.map((x: Any) => `${x.label} ${x.p.toFixed(2)}`).join(', ')}</td>
          <td className="small">{g.character.texture}, {g.character.brightness}, {g.character.activity}</td></tr>)}</tbody></table>
    </div>)
}

function Dense({ d, sid, seek, onDone }: { d: Any; sid: string; seek: (t: number) => void; onDone: () => void }) {
  const [f, setF] = useState({ t0: '', t1: '', step: '0.1' })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const run = async () => {
    setBusy(true); setErr(null)
    try { await api.send('POST', `/api/sources/${sid}/dense`, { t0: +f.t0, t1: +f.t1, step: +f.step }); onDone() }
    catch (e: Any) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <div style={{ marginTop: 16 }}>
      <h3>Плотный проход (короткие вставки, переходы, движение)</h3>
      <div className="row"><input placeholder="от, с" style={{ width: 80 }} value={f.t0} onChange={(e) => setF({ ...f, t0: e.target.value })} />
        <input placeholder="до, с" style={{ width: 80 }} value={f.t1} onChange={(e) => setF({ ...f, t1: e.target.value })} />
        <input placeholder="шаг" style={{ width: 60 }} value={f.step} onChange={(e) => setF({ ...f, step: e.target.value })} />
        <button onClick={run} disabled={busy}>{busy ? 'Извлечение…' : 'Извлечь кадры + OCR'}</button></div>
      <Err e={err} />
      {(d.dense || []).map((x: Any, i: number) => <div key={i} className="card" style={{ marginTop: 8 }}>
        <div className="small">{fmtT(x.interval?.[0])}–{fmtT(x.interval?.[1])}, шаг {x.step} с · смены текста: {(x.text_changes || []).map((c: Any) =>
          <a key={c.t} className="pill" onClick={() => seek(c.t)}>{fmtT(c.t)} {c.ocr.slice(0, 24)}</a>)}</div>
        <div className="frames">{(x.frames || []).map((fr: Any) => <div key={fr.t}><img src={fileUrl(`${x.rel_dir}/${fr.file}`)} onClick={() => seek(fr.t)} /><div className="ft">{fmtT(fr.t)}</div></div>)}</div>
      </div>)}
    </div>)
}

function Transcript({ tr, cur, seek }: { tr: Any; cur: number; seek: (t: number) => void }) {
  if (!tr) return <div className="muted">Транскрипта нет (см. «Задачи»).</div>
  return (
    <div>
      <div className="note">{tr.method} · язык: {tr.language} · {tr.note}. Жёлтым — сегменты с низкой уверенностью.</div>
      <div className="scroll">{tr.segments.map((g: Any) => (
        <div key={g.id} className={`seg ${cur >= g.start && cur < g.end ? 'on' : ''} ${g.unclear ? 'unclear' : ''}`} onClick={() => seek(g.start)}>
          <span className="tc">{fmtT(g.start)}–{fmtT(g.end)}</span><span className="tx">{g.text}</span>
        </div>))}</div>
    </div>)
}

function Frames({ fr, seek, cur }: { fr: Any; seek: (t: number) => void; cur: number }) {
  const [big, setBig] = useState<Any>(null)
  if (!fr) return <div className="muted">Кадры не извлечены.</div>
  return (
    <div>
      <div className="note">{fr.note}. Метод: {fr.method}. Кадров: {fr.frames.length}, смен сцены: {fr.scene_changes.length}.</div>
      {big && <div className="card" style={{ marginBottom: 10 }}><img src={fileUrl(big.rel_path)} style={{ maxHeight: 480, borderRadius: 8 }} />
        <div className="small">{fmtT(big.t)} · {big.reason} {big.scene_score ? `· scene ${big.scene_score}` : ''} · <code>{big.file}</code></div></div>}
      <div className="frames">{fr.frames.map((f: Any) => (
        <div key={f.i}><img src={fileUrl(f.rel_path)} className={Math.abs(cur - f.t) < 0.5 ? 'on' : ''} onClick={() => { setBig(f); seek(f.t) }} />
          <div className="ft">{fmtT(f.t)}{f.reason.includes('scene') ? ' ✂' : ''}</div></div>))}</div>
    </div>)
}

function Ocr({ ocr, seek }: { ocr: Any; seek: (t: number) => void }) {
  if (!ocr) return <div className="muted">OCR не выполнялся.</div>
  return (
    <div>
      <div className="note">{ocr.method}. {ocr.note}. Повторяющийся текст склеен в интервалы.</div>
      <div className="scroll">{ocr.text_spans.map((s: Any, i: number) => (
        <div key={i} className="seg" onClick={() => s.t_start != null && seek(s.t_start)}>
          <span className="tc">{fmtT(s.t_start)}</span><span className="tx">{s.text}</span></div>))}
        {ocr.text_spans.length === 0 && <div className="muted">Текст на выбранных кадрах не найден.</div>}</div>
    </div>)
}

function Sound({ af, seek }: { af: Any; seek: (t: number) => void }) {
  if (!af) return <div className="muted">Звуковой проход не выполнялся.</div>
  const max = Math.max(...af.rms_db_100ms.map((v: number) => v + 60), 1)
  return (
    <div>
      <div className="warnbox">{af.warning}</div>
      <div className="kv"><div>Метод</div><div>{af.method}</div>
        <div>Громкость</div><div>{af.loudness.integrated_lufs ?? '—'} LUFS (LRA {af.loudness.lra_lu ?? '—'})</div>
        <div>Паузы</div><div>{af.silences.map((s: Any) => `${fmtT(s.start)}–${fmtT(s.end)}`).join(', ') || 'не найдены'}</div>
        <div>Кандидаты акцентов</div><div>{af.candidate_accents_s.map((t: number) => <a key={t} className="pill" onClick={() => seek(t)}>{fmtT(t)}</a>)}</div></div>
      <h3>Огибающая RMS (100 мс)</h3>
      <svg viewBox={`0 0 ${af.rms_db_100ms.length} 60`} style={{ width: '100%', height: 90, background: 'var(--panel)', borderRadius: 8 }} preserveAspectRatio="none">
        {af.rms_db_100ms.map((v: number, i: number) => <rect key={i} x={i} width={1} y={60 - Math.max(0, (v + 60)) / max * 60} height={Math.max(0, (v + 60)) / max * 60} fill="#3ddc84" opacity={0.7} onClick={() => seek(i / 10)} />)}
      </svg>
    </div>)
}

function Analysis({ an, sid, seek, onDone }: { an: Any; sid: string; seek: (t: number) => void; onDone: (m: string) => void }) {
  const [txt, setTxt] = useState('')
  const [res, setRes] = useState<Any>(null)
  async function submit(dry: boolean) {
    try {
      const r = await api.send('POST', `/api/sources/${sid}/analysis?dry_run=${dry ? 1 : 0}`, JSON.parse(txt))
      setRes(r)
      if (r.ok && !dry) onDone(`Разбор v${r.version} принят, карточек: ${r.knowledge_ids.length}`)
    } catch (e: Any) { setRes({ ok: false, errors: [e.message] }) }
  }
  const d = an?.data
  return (
    <div>
      {d ? <>
        <div className="note">Версия {an.version} · {an.origin}{an.model ? ` · ${an.model}` : ''} · {an.created_at}</div>
        <p>{d.summary}</p>
        {d.structure && <><h3>Конструкция</h3>{Object.entries(d.structure).map(([k, v]: Any) => (
          <div key={k} className="small">{k}: {v ? <><a onClick={() => seek(v.t_start)}>{fmtT(v.t_start)}</a> — {v.description}</> : <span className="muted">отсутствует</span>}</div>))}</>}
        {d.methodology && <><h3>Методика</h3><ol>{d.methodology.steps.map((x: Any, i: number) => (
          <li key={i}><a onClick={() => seek(x.t_start)}>{fmtT(x.t_start)}</a> {x.action}{x.settings ? ` — ${x.settings}` : ''}</li>))}</ol></>}
        <h3>Временная шкала</h3>
        <table className="sbtable"><thead><tr><th>Интервал</th><th>Говорится</th><th>Видно</th><th>Слышно</th><th>Чему учит</th></tr></thead>
          <tbody>{d.timeline.map((x: Any, i: number) => (
            <tr key={i}><td><a onClick={() => seek(x.t_start)}>{fmtT(x.t_start)}–{fmtT(x.t_end)}</a></td><td>{x.speech}</td><td>{x.visual}</td><td className="muted">{x.audio}</td><td>{x.teaches}</td></tr>))}</tbody></table>
        {d.editing && <><h3>Монтаж и постановка</h3><div className="kv">{Object.entries(d.editing).map(([k, v]: Any) => <><div key={k}>{k}</div><div>{v}</div></>)}</div></>}
        {d.sound && <><h3>Звук</h3><ul>{d.sound.observations.map((o: string, i: number) => <li key={i}>{o}</li>)}</ul><div className="warnbox small">{d.sound.limitations}</div></>}
      </> : <div className="note">Смысловой разбор ещё не импортирован. AI-провайдер не подключён: скачайте пакет source-analysis, получите ответ Claude по схеме и вставьте его ниже.</div>}
      <h3>Импортировать разбор (analysis/1)</h3>
      <div className="row" style={{ marginBottom: 6 }}><span className="small muted">Файл JSON:</span>
        <input type="file" accept=".json,application/json" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setTxt(await f.text()) }} /></div>
      <textarea style={{ minHeight: 120 }} placeholder='{"schema":"analysis/1", …}' value={txt} onChange={(e) => setTxt(e.target.value)} />
      <div className="row" style={{ marginTop: 6 }}><button onClick={() => submit(true)}>Проверить</button><button className="primary" onClick={() => submit(false)}>Импортировать</button></div>
      {res && <div className={res.ok ? 'note' : 'error'}>{res.ok ? 'Проверка пройдена' : 'Отклонено'}
        {(res.errors || []).map((e: string, i: number) => <div key={i} className="small">• {e}</div>)}
        {(res.warnings || []).map((e: string, i: number) => <div key={i} className="small">⚠ {e}</div>)}</div>}
    </div>)
}

function Knowledge({ kn, seek }: { kn: Any[]; seek: (t: number) => void }) {
  if (!kn.length) return <div className="muted">Карточек из этого источника пока нет.</div>
  return <table><tbody>{kn.map((k, i) => (
    <tr key={i}><td><a onClick={() => seek(k.t_start)} className="tc">{fmtT(k.t_start)}</a></td>
      <td><a onClick={() => go(`/knowledge/${k.id}`)}>{k.title}</a><div className="small muted">{k.category} · {k.kind}</div></td>
      <td><Evidence e={k.evidence} /></td></tr>))}</tbody></table>
}

function Meta({ d, onSaved }: { d: Any; onSaved: () => void }) {
  const s = d.source
  const [f, setF] = useState<Any>({ title: s.title, author: s.author, published_at: s.published_at, purpose: s.purpose,
    usage_status: s.usage_status, provenance: s.provenance || '', notes: s.notes || '', tags: (s.tags || []).join(', ') })
  const save = async () => { await api.send('PATCH', `/api/sources/${s.id}`, { ...f, tags: f.tags.split(',').map((x: string) => x.trim()).filter(Boolean) }); onSaved() }
  const field = (k: string, l: string, opts?: string[]) => (<><div>{l}</div><div>{opts ?
    <select value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })}>{opts.map((o) => <option key={o}>{o}</option>)}</select> :
    <input style={{ width: '100%' }} value={f[k] || ''} onChange={(e) => setF({ ...f, [k]: e.target.value })} />}</div></>)
  return (
    <div>
      <div className="kv">
        {field('title', 'Название')}{field('author', 'Автор / аккаунт')}{field('published_at', 'Дата публикации')}
        {field('purpose', 'Назначение', ['обучение', 'референс', 'ассет', 'мой материал', 'сочетание', 'неизвестно'])}
        {field('usage_status', 'Статус использования', ['неизвестно', 'мой материал', 'разрешено', 'только референс'])}
        {field('provenance', 'Происхождение')}{field('tags', 'Теги (через запятую)')}
        <div>Заметки</div><div><textarea value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></div>
      </div>
      <button className="primary" onClick={save} style={{ marginTop: 8 }}>Сохранить</button>
      <h3>Технические данные</h3>
      <div className="kv small"><div>Файл</div><div>{s.original_name || '—'} · {s.size ? `${(s.size / 1e6).toFixed(1)} МБ` : ''} · {s.mime || ''}</div>
        <div>SHA-256</div><div><code>{s.sha256 || '—'}</code></div><div>Путь</div><div><code>{s.rel_path || '—'}</code></div>
        <div>Длительность</div><div>{s.duration ? `${s.duration} с` : '—'}</div><div>Язык</div><div>{s.language}</div></div>
      <h3>Как попал в студию</h3>
      {d.links.map((l: Any) => <div key={l.id} className="small">• {l.via}{l.url ? <> — <a href={l.url} target="_blank" rel="noreferrer">{l.url}</a></> : ''} {l.note ? `(${l.note})` : ''} · {l.added_at.slice(0, 16)}</div>)}
      {d.collections.length > 0 && <div className="small" style={{ marginTop: 6 }}>Коллекции: {d.collections.map((c: string) => <span key={c} className="pill">{c}</span>)}</div>}
      {d.relations.length > 0 && <div className="small">Связи: {d.relations.map((r: Any, i: number) => <span key={i} className="pill">{r.rel}: {r.a === s.id ? r.b : r.a}</span>)}</div>}
      {d.page && <><h3>Страница по ссылке</h3><pre className="md small">{JSON.stringify(d.page, null, 2)}</pre></>}
      {d.technical && <><h3>Технический проход</h3><pre className="md small">{JSON.stringify(d.technical, null, 2)}</pre></>}
    </div>)
}

function Jobs({ jobs, reload }: { jobs: Any[]; reload: () => void }) {
  const act = async (id: string, a: string) => { await api.send('POST', `/api/jobs/${id}/${a}`); reload() }
  return <table><thead><tr><th>Этап</th><th>Состояние</th><th>Попытки</th><th>Результат</th><th></th></tr></thead><tbody>
    {jobs.map((j) => <tr key={j.id}><td>{j.stage}</td><td><Badge s={j.state} /></td><td>{j.attempts}</td><td className="small">{j.error || j.note}</td>
      <td>{['failed', 'blocked', 'cancelled', 'partial'].includes(j.state) && <button onClick={() => act(j.id, 'retry')}>Повторить</button>}
        {['queued', 'running'].includes(j.state) && <button onClick={() => act(j.id, 'cancel')}>Отменить</button>}</td></tr>)}</tbody></table>
}
