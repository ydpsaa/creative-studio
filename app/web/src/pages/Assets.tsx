import { useState } from 'react'
import { Any, fileUrl, go } from '../api'
import { Badge, useLoad } from '../ui'

export default function Assets() {
  const [kind, setKind] = useState('audio')
  const { data } = useLoad<Any[]>(`/api/sources?kind=${kind}`, [kind])
  return (
    <div>
      <h1>Звуки и ассеты</h1>
      <div className="sub">Прослушивание и выбор материалов. Статус использования определяет, попадёт ли файл в переносимый экспорт проекта.</div>
      <div className="row" style={{ marginBottom: 12 }}>
        {[['audio', 'Звуки и музыка'], ['image', 'Изображения и графика'], ['video', 'Видео']].map(([k, l]) =>
          <button key={k} className={kind === k ? 'primary' : ''} onClick={() => setKind(k)}>{l}</button>)}
      </div>
      <div className="note">В экспорт с файлами попадают только ассеты со статусом «мой материал» или «разрешено». «Только референс» и «неизвестно» экспортируются ссылкой.</div>
      <div className="card">
        <table><thead><tr><th>Материал</th><th>Просмотр</th><th>Назначение</th><th>Использование</th><th>Происхождение</th></tr></thead>
          <tbody>{(data || []).map((s) => (
            <tr key={s.id}>
              <td><a onClick={() => go(`/source/${s.id}`)}>{s.title}</a><div className="small muted">{s.id} · {s.duration ? `${s.duration.toFixed(1)} с` : ''}</div></td>
              <td>{s.rel_path ? (kind === 'audio' ? <audio controls src={fileUrl(s.rel_path)} style={{ width: 240 }} /> :
                kind === 'image' ? <img src={fileUrl(s.rel_path)} style={{ width: 90, borderRadius: 6 }} /> :
                  s.poster ? <img src={fileUrl(s.poster)} style={{ width: 70, borderRadius: 6 }} /> : '—') : <span className="muted small">нет файла</span>}</td>
              <td className="small">{s.purpose}</td>
              <td><span className={`badge ${s.usage_status === 'разрешено' || s.usage_status === 'мой материал' ? 'ok' : 'warn'}`}>{s.usage_status}</span>
                <div className="small muted">{s.usage_status === 'разрешено' || s.usage_status === 'мой материал' ? 'файл попадёт в экспорт' : 'в экспорт только ссылкой'}</div></td>
              <td className="small">{s.provenance || '—'}</td>
            </tr>))}</tbody></table>
        {data && data.length === 0 && <div className="muted">Материалов этого типа пока нет. Добавьте их во «Входящих».</div>}
      </div>
    </div>)
}
