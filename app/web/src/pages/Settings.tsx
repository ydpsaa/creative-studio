import { useState } from 'react'
import { api, Any, fileUrl } from '../api'
import { Badge, Err, Toast, useLoad } from '../ui'

function SkillExport() {
  const { data, reload } = useLoad<Any[]>('/api/skill/exports')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const run = async () => { setBusy(true); setErr(null); try { await api.send('POST', '/api/skill/export', {}); reload() } catch (e: Any) { setErr(e.message) } finally { setBusy(false) } }
  return (
    <div>
      <h2>Переносимый творческий навык</h2>
      <div className="card">
        <div className="note">Папка-навык с методами, примерами, профилями брендов, предпочтениями (studio/brand), шаблонами и критериями качества. Работает без студии: её можно передать Claude, Codex или ChatGPT. Правки уровня ролика/проекта в навык не входят.</div>
        <Err e={err} />
        <button className="primary" onClick={run} disabled={busy}>{busy ? 'Экспорт…' : 'Экспортировать новую версию'}</button>
        <table style={{ marginTop: 10 }}><thead><tr><th>Версия</th><th>Методов / примеров</th><th>Проверка самостоятельности</th><th>Изменения</th><th></th></tr></thead>
          <tbody>{(data || []).map((v) => <tr key={v.version}>
            <td>{v.version}<div className="small muted">{v.exported_at?.slice(0, 16)}</div></td>
            <td>{v.methods} / {v.examples}</td>
            <td><Badge s={v.check.ok ? 'completed' : 'failed'} title={v.check.ok ? 'пройдена' : 'есть проблемы'} />
              {v.check.problems.slice(0, 3).map((p: string, i: number) => <div key={i} className="small">{p}</div>)}
              {v.check.warnings.length > 0 && <div className="small muted">изменено после экспорта: {v.check.warnings.length}</div>}</td>
            <td className="small"><pre className="md">{v.changelog.split('\n').slice(2, 8).join('\n')}</pre></td>
            <td>{v.zip && <a href={fileUrl(v.zip, true)}>скачать ZIP</a>}</td></tr>)}</tbody></table>
        {data && data.length === 0 && <div className="muted small">Экспортов ещё нет.</div>}
      </div>
    </div>)
}

export default function Settings() {
  const { data, reload, error } = useLoad<Any>('/api/settings')
  const [dest, setDest] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [limits, setLimits] = useState<Any>(null)
  if (error) return <Err e={error} />
  if (!data) return <div className="muted">Загрузка…</div>
  const L = limits || data.limits
  async function backup() {
    setBusy(true)
    try { const r = await api.send('POST', '/api/backup', dest ? { dest } : {}); setMsg(`Копия: ${r.files} файлов`); reload() }
    catch (e: Any) { setErr(e.message) } finally { setBusy(false) }
  }
  async function saveLimits() {
    try { await api.send('PUT', '/api/settings', { limits: L }); setMsg('Лимиты сохранены'); setLimits(null); reload() } catch (e: Any) { setErr(e.message) }
  }
  return (
    <div>
      <h1>Настройки</h1>
      <Err e={err} />
      <div className="grid2">
        <div className="card">
          <h3>Данные</h3>
          <div className="kv"><div>Папка данных</div><div><code>{data.data_dir}</code></div>
            <div>Сменить</div><div className="small">Остановите студию и запустите с <code>STUDIO_DATA_DIR=/путь ./studio start</code>. Пути внутри данных относительные — папку можно перенести целиком.</div>
            <div>Адрес</div><div>http://127.0.0.1:{data.port} (только этот компьютер)</div></div>
          <h3>AI-режим</h3>
          <div className="kv"><div>Режим</div><div><Badge s="blocked" title="none" /> без AI-провайдера</div>
            <div>Что передаётся наружу</div><div className="small">{data.outbound}</div>
            <div>Как работать</div><div className="small">Карточка источника → «Подготовить пакет для Claude» → Claude разбирает по навыку creative-video-analysis → вкладка «Разбор» → «Импортировать разбор». Для проекта — «Пакет project-generation».</div></div>
          <h3>Лимиты</h3>
          <div className="kv">
            <div>Размер партии</div><div><input type="number" value={L.batch_size} onChange={(e) => setLimits({ ...L, batch_size: +e.target.value })} /></div>
            <div>Параллельных задач</div><div><input type="number" value={L.parallel_jobs} onChange={(e) => setLimits({ ...L, parallel_jobs: +e.target.value })} /> <span className="small muted">сейчас worker выполняет задачи по одной</span></div>
            <div>Бюджет платных задач, $</div><div><input type="number" value={L.paid_budget_usd} onChange={(e) => setLimits({ ...L, paid_budget_usd: +e.target.value })} /> <span className="small muted">платные провайдеры не подключены; стоимость неизвестна</span></div>
          </div>
          {limits && <button className="primary" onClick={saveLimits} style={{ marginTop: 8 }}>Сохранить лимиты</button>}
        </div>
        <div className="card">
          <h3>Обработчики</h3>
          <table><tbody>{Object.entries(data.tools).map(([k, v]: Any) => (
            <tr key={k}><td>{k}</td><td><Badge s={(v.available ?? v.alive) ? 'completed' : 'blocked'} title={(v.available ?? v.alive) ? 'доступно' : 'нет'} /></td>
              <td className="small">{v.method || v.how || v.path || ''}</td></tr>))}</tbody></table>
        </div>
      </div>
      <SkillExport />
      <h2>Резервное копирование</h2>
      <div className="card">
        <div className="note">Снимок базы делается через SQLite backup API (согласованно при работающей студии), файлы проверяются по SHA-256 в манифесте. Копия на том же диске не защищает от потери диска.</div>
        <div className="row"><input style={{ width: 420 }} placeholder="Внешний путь, например /Volumes/Backup/CreativeStudio (пусто = studio-data/backups)" value={dest} onChange={(e) => setDest(e.target.value)} />
          <button className="primary" disabled={busy} onClick={backup}>{busy ? 'Копирование…' : 'Создать копию'}</button></div>
        <div className="small" style={{ marginTop: 8 }}>Проверка восстановления: <code>./studio restore-check &lt;папка копии&gt; &lt;пустая папка&gt;</code></div>
        <h3>Копии в папке данных</h3>{data.backups.map((b: string) => <div key={b} className="small">{b}</div>)}
        {data.backups.length === 0 && <div className="muted small">Пока нет.</div>}
      </div>
      <Toast msg={msg} onDone={() => setMsg(null)} />
    </div>)
}
